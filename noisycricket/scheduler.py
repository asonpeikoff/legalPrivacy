"""Scheduler: identity lifetimes, budgets, engine dispatch across transports."""

from __future__ import annotations

import random
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .config import Profile
from .identity import Identity, make_identity
from .events import EventLog
from .pacing import Budget, Pacer
from .transports import ClearnetTransport, HitResult, Target, TorTransport, Transport


class MaxFailuresExceeded(RuntimeError):
    """Raised when total failures exceed daily_max_failures."""


@dataclass
class Lane:
    """One transport + its budget state and currently pinned identity."""
    name: str
    transport: Transport
    engines: list[str]
    budgets: dict[str, Budget]
    identity: Identity | None = None
    pacer: Pacer | None = None
    hits_by_identity: int = 0
    consecutive_failures: int = 0
    crawl_factory: object | None = None
    crawl_next: Target | None = None
    crawl_depth: int = 0
    onion_factory: object | None = None
    onion_next: Target | None = None
    onion_depth: int = 0
    gate: Callable[[], bool] | None = None    # e.g. "is Tor bootstrapped?"; closed = lane idles
    hits_total: int = 0
    failures_total: int = 0
    last_error: str | None = None
    last_hit_ts: float | None = None
    last_latency_ms: float | None = None

    @property
    def open(self) -> bool:
        return self.gate is None or bool(self.gate())


def build_lanes(profile: Profile, wordlist: list[str],
                rng: random.Random | None = None,
                tor_gate: Callable[[], bool] | None = None,
                tor_endpoint: Callable[[], tuple[str, str | None]] | None = None) -> list[Lane]:
    """Build the active lanes.

    The tor lane exists only when Tor is enabled. It carries the onion engine
    (default) and, ONLY if the user opted in, clearnet engines too.
    `tor_endpoint` lets a managed Tor supply its (dynamic) socks/control address.
    """
    rng = rng or random.Random()
    lanes = []
    tr = profile.transports
    share = profile.clearnet_lane_count()

    def _budget(name: str, divisor: int) -> Budget:
        return Budget(per_day=max(1, profile.engines[name].budget_per_day // divisor))

    if tr.get("clearnet") and tr["clearnet"].enabled:
        names = profile.active_engines("clearnet")
        lanes.append(Lane(
            name="clearnet",
            transport=ClearnetTransport(proxy=tr["clearnet"].proxy),
            engines=names,
            budgets={n: _budget(n, share) for n in names},
        ))

    if tr.get("tor") and tr["tor"].enabled:
        t = tr["tor"]
        socks, control = (tor_endpoint() if tor_endpoint else (t.socks, t.control))
        names = profile.active_engines("tor")
        lanes.append(Lane(
            name="tor",
            transport=TorTransport(socks=socks, control=control,
                                   identities_per_circuit=t.identities_per_circuit,
                                   control_password=t.control_password),
            engines=names,
            budgets={n: _budget(n, 1 if n == "onion" else share) for n in names},
            gate=tor_gate,
        ))
    return [lane for lane in lanes if lane.engines]


@dataclass
class Runner:
    profile: Profile
    lanes: list[Lane]
    wordlist: list[str]
    clock: object = time.time          # injectable for tests
    local_hour_fn: object = None       # injectable: () -> float hour
    sleep_fn: object = time.sleep
    probe_fn: object = None            # injectable: (lane, identity, target) -> HitResult
    max_iterations: int = 10_000       # safety net for --once-style testing
    verbose: bool = False
    log: EventLog | None = None        # shared event log (UI visibility)
    stop_event: threading.Event | None = None
    tor_seeds: list[str] | None = None  # extra onion seeds from the profile

    def __post_init__(self) -> None:
        self.rng = random.Random()
        self._ident_counter = 0
        if self.clock is None:
            self.clock = time.time
        self.hit_log: deque[dict] = deque(maxlen=500)
        self.started_at = time.time()
        self.day = time.localtime().tm_yday
        self.paused = False
        self._hit_seq = 0

    def _emit(self, level: str, source: str, msg: str, **data) -> None:
        if self.log:
            self.log.add(level, source, msg, data or None)

    def _stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    def stats(self) -> dict:
        """Snapshot for the UI: per-lane and per-engine budgets and health."""
        lanes = []
        for lane in self.lanes:
            lanes.append({
                "name": lane.name, "open": lane.open,
                "dimmed": bool(getattr(lane.transport, "dimmed", False)),
                "hits": lane.hits_total, "failures": lane.failures_total,
                "last_error": lane.last_error, "last_hit_ts": lane.last_hit_ts,
                "latency_ms": lane.last_latency_ms,
                "identity": (lane.identity.persona if lane.identity and hasattr(lane.identity, "persona") else None),
                "engines": {e: {"spent": b.spent, "per_day": b.per_day, "failures": b.failures}
                            for e, b in lane.budgets.items()},
            })
        return {"lanes": lanes, "paused": self.paused, "uptime": int(time.time() - self.started_at)}

    def reset_budgets(self) -> None:
        for lane in self.lanes:
            for b in lane.budgets.values():
                b.spent = b.failures = 0
                b.history.clear()
            lane.hits_by_identity = lane.consecutive_failures = 0
        self._emit("info", "scheduler", "New day — daily budgets reset.")

    def _probe(self, lane: Lane, identity: Identity, target: Target) -> HitResult:
        if self.probe_fn:
            return self.probe_fn(lane, identity, target)
        if target.kind == "dns":
            return self._dns_probe(target)
        t0 = time.time()
        res = lane.transport.hit(identity, target)
        res.latency_ms = (time.time() - t0) * 1000.0
        return res

    def _dns_probe(self, target: Target) -> HitResult:
        """Raw crafted-UDP DNS on the clearnet lane; the hit IS the packet."""
        from .engines import DnsNoise, build_dns_query
        rest = target.url[len("dns://"):]
        server, _, domain = rest.partition("/")
        if not domain:
            return HitResult(None, error="malformed dns target")
        packet = build_dns_query(domain, qid=self.rng.randint(0, 0xFFFF))
        noise = DnsNoise(self.rng, self.wordlist)
        if noise.fire(packet, server, timeout=target.timeout):
            return HitResult(200, "")
        return HitResult(None, error=f"dns query to {server} failed")

    def _local_hour(self) -> float | None:
        if self.local_hour_fn:
            return self.local_hour_fn()
        return ((time.localtime().tm_hour * 60 + time.localtime().tm_min) / 60.0)

    def _identity_pacer_for(self, lane: Lane) -> tuple[Identity, Pacer]:
        """Pin one identity (and its pacer) per circuit/proxy lifetime.

        Tor rotations also signal NEWNYM so the circuit itself turns over,
        not just the fingerprint.
        """
        limit = getattr(lane.transport, "identities_per_circuit", None) or 10
        if lane.identity is None or lane.hits_by_identity >= limit:
            self._ident_counter += 1
            lane.identity = make_identity(self.rng, self._ident_counter, self.wordlist)
            lane.pacer = Pacer(self.rng,
                               pace_multiplier=lane.identity.pace_multiplier,
                               realtime_scale=self.profile.realtime_scale)
            lane.pacer.sleep_fn = self.sleep_fn
            lane.hits_by_identity = 0
            renew = getattr(lane.transport, "renew_circuits", None)
            if renew is not None:
                renew()
        return lane.identity, lane.pacer

    def run_once(self, wait: bool = True) -> bool:
        """Run one scheduled job if any lane wants it. Returns True if a hit was made."""
        hour = self._local_hour()
        options: list[tuple[Lane, str]] = [
            (lane, eng_name)
            for lane in self.lanes for eng_name in lane.engines
            if not lane.budgets[eng_name].exhausted and lane.open
        ]
        if not options:
            return False
        weights = []
        for lane, eng_name in options:
            w = float(lane.budgets[eng_name].remaining)
            if getattr(lane.transport, "dimmed", False):
                w *= 0.1  # down-weight dimmed transport so traffic prefers healthy lanes
            weights.append(max(w, 0.01))
        lane, eng_name = self.rng.choices(options, weights=weights)[0]
        identity, pacer = self._identity_pacer_for(lane)
        target = self._make_target(lane, eng_name, identity)
        result = self._probe(lane, identity, target)
        budget = lane.budgets[eng_name]
        if result.ok:
            budget.record_hit(self.clock())
            lane.hits_by_identity += 1
            lane.hits_total += 1
            lane.consecutive_failures = 0
            lane.last_hit_ts = time.time()
            lane.last_error = None
            if getattr(lane.transport, "dimmed", False):
                lane.transport.dimmed = False
            self._settle(lane, eng_name, target, result)
        else:
            budget.record_failure(self.clock())
            lane.failures_total += 1
            lane.last_error = result.error or f"HTTP {result.status}"
            lane.consecutive_failures += 1
            pacer.on_failure(max(1, budget.failures))
            # Rotate identity & request new circuit if failing repeatedly
            if lane.consecutive_failures >= 3:
                lane.identity = None
                lane.hits_by_identity = 0
                lane.consecutive_failures = 0
            if eng_name == "onion":          # a dead onion is normal; restart the walk
                lane.onion_next, lane.onion_depth = None, 0
        lane.last_latency_ms = getattr(result, "latency_ms", None)
        # Onion services are routinely offline: they don't count toward the clearnet failure cap
        # (they get their own, more generous one).
        total_failures = sum(b.failures for l in self.lanes for n, b in l.budgets.items() if n != "onion")
        onion_failures = sum(b.failures for l in self.lanes for n, b in l.budgets.items() if n == "onion")
        self._record_hit(lane, eng_name, identity, target, result)
        if total_failures > self.profile.daily_max_failures or \
                onion_failures > self.profile.daily_max_failures * 3:
            raise MaxFailuresExceeded("daily_max_failures exceeded — stopping run")
        if result.dim_transport and hasattr(lane.transport, "dimmed"):
            lane.transport.dimmed = True
        if wait:
            pacer.wait(hour)
        return True

    def _record_hit(self, lane: Lane, eng_name: str, identity: Identity,
                    target: Target, result: HitResult) -> None:
        self._hit_seq += 1
        status = f"HTTP {result.status}" if result.status else (result.error or "ERROR")
        rec = {
            "seq": self._hit_seq, "ts": time.time(), "lane": lane.name, "engine": eng_name,
            "url": target.url, "status": result.status, "ok": result.ok,
            "error": result.error, "latency_ms": getattr(result, "latency_ms", None),
            "identity": getattr(identity, "id", None),
        }
        self.hit_log.append(rec)
        short = target.url if len(target.url) <= 90 else target.url[:87] + "…"
        self._emit("info" if result.ok else "warn", lane.name,
                   f"{eng_name:6s} {short} → {status}"
                   + (f" ({rec['latency_ms']:.0f} ms)" if rec["latency_ms"] else ""), **{"hit": rec["seq"]})
        if self.verbose:
            print(f"[{time.strftime('%H:%M:%S')}] [{lane.name}] {eng_name:6s} -> {target.url[:65]} ({status})")
        if not result.ok and lane.name == "tor" and self.verbose:
            print(f"  [tor notice] {result.error or 'Hit failed'}.", file=sys.stderr)

    def _settle(self, lane: Lane, eng_name: str, target: Target, result: HitResult) -> None:
        """Post-hit bookkeeping: advance the crawl random-walk from fetched HTML."""
        if eng_name == "onion":
            nxt = lane.onion_factory.next_from(result.html, target.url) \
                if lane.onion_factory is not None and result.html else None
            max_depth = self.profile.engines["onion"].max_depth
            if nxt is not None and lane.onion_depth < max_depth:
                lane.onion_next, lane.onion_depth = nxt, lane.onion_depth + 1
            else:
                lane.onion_next, lane.onion_depth = None, 0
            return
        if eng_name != "crawl" or not result.html:
            return
        nxt = lane.crawl_factory.next_from(result.html, target.url) \
            if lane.crawl_factory is not None else None
        max_depth = self.profile.engines["crawl"].max_depth
        if nxt is not None and lane.crawl_depth < max_depth:
            lane.crawl_next = nxt
            lane.crawl_depth += 1
        else:
            lane.crawl_next = None
            lane.crawl_depth = 0

    def _make_target(self, lane: Lane, eng_name: str, identity: Identity) -> Target:
        from .engines import (CrawlTargetFactory, DnsNoise, DohNoise,
                              SearchTargetFactory, load_wordlist)
        words = self.wordlist
        if eng_name == "onion":
            from .onion import OnionTargetFactory
            if lane.onion_next is not None:
                return lane.onion_next
            tor_cfg = self.profile.transports.get("tor")
            if lane.onion_factory is None:
                lane.onion_factory = OnionTargetFactory(
                    rng=self.rng, seeds=list(tor_cfg.onion_seeds) if tor_cfg and tor_cfg.onion_seeds else [],
                    max_depth=self.profile.engines["onion"].max_depth,
                    follow_offsite=bool(tor_cfg.onion_follow_offsite) if tor_cfg else True)
            return lane.onion_factory.make(identity)
        if eng_name == "search":
            fac = SearchTargetFactory(rng=self.rng, words=words)
            return fac.make(identity)
        if eng_name == "crawl":
            if lane.crawl_next is not None:
                return lane.crawl_next
            cfg = self.profile.engines["crawl"]
            lane.crawl_factory = CrawlTargetFactory(rng=self.rng, seeds=cfg.seeds,
                                                    max_depth=cfg.max_depth,
                                                    same_site_only=cfg.same_site_only)
            return lane.crawl_factory.make(identity)
        if eng_name == "dns":
            if lane.name == "tor":
                # Arbitrary UDP cannot traverse Tor circuits: DoH over the circuit.
                fac = DohNoise(rng=self.rng, words=words)
                return fac.make(identity)
            fac = DnsNoise(rng=self.rng, words=words)
            _packet, domain, server = fac.make_query()
            return Target(url=f"dns://{server}/{domain}", kind="dns", timeout=3.0)
        raise ValueError(f"unknown engine '{eng_name}'")

    def run(self) -> dict[str, dict[str, int]]:
        """Drain budgets across lanes (respects max_iterations safety net)."""
        iterations = 0
        while iterations < self.max_iterations and not self._stopped():
            if all(lane.budgets[e].exhausted
                   for lane in self.lanes for e in lane.engines):
                break
            if not self.run_once():
                self.sleep_fn(1.0)       # lanes gated (e.g. Tor still bootstrapping)
            iterations += 1
        return {lane.name: {eng: lane.budgets[eng].spent for eng in lane.engines}
                for lane in self.lanes}

    def run_forever(self) -> None:
        """Long-running mode for the app: honours stop/pause, resets budgets daily."""
        self._emit("info", "scheduler", "Noise generation started.")
        while not self._stopped():
            today = time.localtime().tm_yday
            if today != self.day:
                self.day = today
                self.reset_budgets()
            if self.paused:
                self.sleep_fn(1.0)
                continue
            try:
                worked = self.run_once()
            except MaxFailuresExceeded as exc:
                self._emit("error", "scheduler", f"{exc}. Pausing until tomorrow.")
                self.paused = True
                continue
            if not worked:
                self.sleep_fn(2.0)       # all budgets spent or lanes gated: idle politely
        self._emit("info", "scheduler", "Noise generation stopped.")
