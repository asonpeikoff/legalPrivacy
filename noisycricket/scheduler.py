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
    follow_next: Target | None = None         # click-through chain: search -> result -> page
    search_factory: object | None = None
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
                tor_endpoint: Callable[[], tuple[str, str | None]] | None = None,
                clearnet_transport: Transport | None = None) -> list[Lane]:
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
            transport=clearnet_transport or ClearnetTransport(proxy=tr["clearnet"].proxy),
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
    queries: object | None = None      # QuerySource shared by all lanes (live/real queries)
    domains: object | None = None      # DomainPool (real popular domains)
    click_prob: float = 0.6            # chance a search is followed by opening a result
    rate: object | None = None         # RatePlanner: hourly plan (legacy app mode)
    sessions: object | None = None     # SessionPlanner: human sessions + breaks (app mode)
    dns_plan: object | None = None     # DnsPlan: which resolver DNS noise uses

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
        self.day_failures = 0           # daily failure caps (independent of hourly budget resets)
        self.day_onion_failures = 0
        self.hour: int | None = None
        self.hour_target = 0.0
        self._pace: tuple[float, float] | None = None   # (min_delay, max_delay) for this hour
        self.activity: dict = {"state": "starting"}       # what is happening right now (for the UI)
        self.session_no = 0

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

    # ── hourly plan (app mode) ──────────────────────────────────────────────
    def engine_shares(self) -> dict[tuple[str, str], float]:
        """(lane, engine) -> share of all actions, from the engines' relative weights."""
        w = {}
        for lane in self.lanes:
            for e in lane.engines:
                w[(lane.name, e)] = max(0.0, float(self.profile.engines[e].budget_per_day))
        tot = sum(w.values()) or 1.0
        return {k: v / tot for k, v in w.items()}

    def _apply_pace(self, pacer: Pacer | None) -> None:
        if pacer is None or self._pace is None:
            return
        pacer.min_delay, pacer.max_delay = self._pace
        pacer.min_gap = min(pacer.min_gap, self._pace[0])
        pacer.diurnal = False              # the hourly plan already follows the day/night curve

    def plan_hour(self, hour: int) -> float:
        """Set this hour's per-engine allowances and pacing from the RatePlanner."""
        shares = self.engine_shares()
        search_share = sum(v for (ln, e), v in shares.items() if e == "search") or 0.3
        total = self.rate.actions_for_hour(hour, search_share)
        self.hour, self.hour_target = hour, total
        for lane in self.lanes:
            for e, b in lane.budgets.items():
                x = total * shares.get((lane.name, e), 0)
                b.per_day = int(x) + (1 if self.rng.random() < x - int(x) else 0)   # unbiased rounding
                b.spent = 0
        gap = 3600.0 / max(total, 0.25)
        self._pace = (max(1.0, gap * 0.35), max(2.0, gap * 1.35))
        for lane in self.lanes:
            self._apply_pace(lane.pacer)
        self._emit("info", "scheduler", f"Hour {hour:02d}:00 plan: about {total:.0f} actions "
                   f"({self.profile.rate.mode}, level {self.profile.rate.level:g}).")
        return total

    def reset_budgets(self) -> None:
        for lane in self.lanes:
            for b in lane.budgets.values():
                b.spent = b.failures = 0
                b.history.clear()
            lane.hits_by_identity = lane.consecutive_failures = 0
        self.day_failures = self.day_onion_failures = 0
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
        if server == "system":
            if DnsNoise.resolve_system(domain, timeout=target.timeout):
                return HitResult(200, "")
            return HitResult(None, error="lookup via your normal DNS failed")
        noise = DnsNoise(self.rng, self.wordlist, self.domains)
        if noise.fire(packet, server, timeout=target.timeout):
            return HitResult(200, "")
        if self.dns_plan is not None and hasattr(self.dns_plan, "mark_failed"):
            self.dns_plan.mark_failed(server)          # next lookups move to another / the fallback
        return HitResult(None, error=f"no answer from {server}")

    def _dns_label(self, server: str) -> str:
        if self.dns_plan is not None:
            for ip, label in self.dns_plan.secure_servers():
                if ip == server:
                    fb = any(r["ip"] == ip and r["role"] == "fallback" for r in self.dns_plan.cfg.resolvers)
                    return f"{label}{' (fallback)' if fb else ''} {ip}"
        return server

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
            self._apply_pace(lane.pacer)
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
        self._execute(lane, eng_name, identity, pacer, target)
        if wait:
            pacer.wait(hour)
        return True

    def _execute(self, lane: Lane, eng_name: str, identity: Identity, pacer: Pacer, target: Target,
                 settle: bool = True) -> HitResult:
        """Make one request and do all the bookkeeping (counts, failures, log, backoff)."""
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
            if settle:
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
        if not result.ok:
            if eng_name == "onion":
                self.day_onion_failures += 1
            else:
                self.day_failures += 1
        total_failures, onion_failures = self.day_failures, self.day_onion_failures
        self._record_hit(lane, eng_name, identity, target, result)
        if total_failures > self.profile.daily_max_failures or \
                onion_failures > self.profile.daily_max_failures * 3:
            raise MaxFailuresExceeded("daily_max_failures exceeded — stopping run")
        if result.dim_transport and hasattr(lane.transport, "dimmed"):
            lane.transport.dimmed = True
        return result

    def _record_hit(self, lane: Lane, eng_name: str, identity: Identity,
                    target: Target, result: HitResult) -> None:
        self._hit_seq += 1
        status = f"HTTP {result.status}" if result.status else (result.error or "ERROR")
        shown = target.url.split("/", 3)[-1] if target.kind == "dns" else target.url   # dns: just the name
        rec = {
            "seq": self._hit_seq, "ts": time.time(), "lane": lane.name, "engine": eng_name,
            "url": shown, "status": result.status, "ok": result.ok,
            "error": result.error, "latency_ms": getattr(result, "latency_ms", None),
            "identity": getattr(identity, "id", None),
            "note": target.note, "role": target.role, "detail": getattr(result, "detail", None),
        }
        self.hit_log.append(rec)
        short = shown if len(shown) <= 90 else shown[:87] + "…"
        tag = f" [{target.note}]" if target.note else (" [click]" if target.role == "result" else "")
        self._emit("info" if result.ok else "warn", lane.name,
                   f"{eng_name:6s} {short}{tag} → {status}"
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
        if eng_name == "search":
            lane.follow_next = None
            if target.role == "result" and self.domains is not None and hasattr(self.domains, "remember"):
                import urllib.parse as _u
                self.domains.remember(_u.urlparse(target.url).hostname or "")
            if result.html:
                if target.role == "search" and self.rng.random() < self.click_prob \
                        and lane.search_factory is not None:
                    lane.follow_next = lane.search_factory.result_target(result.html, target.url)
                elif target.role == "result" and self.rng.random() < 0.25:
                    from .engines import CrawlTargetFactory
                    lane.follow_next = CrawlTargetFactory(rng=self.rng, seeds=[]).next_from(
                        result.html, target.url)
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
            if lane.follow_next is not None:
                nxt, lane.follow_next = lane.follow_next, None
                return nxt
            if lane.search_factory is None:
                lane.search_factory = SearchTargetFactory(
                    rng=self.rng, words=words, queries=self.queries,
                    html_mode=not getattr(lane.transport, "is_browser", False))
            return lane.search_factory.make(identity)
        if eng_name == "crawl":
            if lane.crawl_next is not None:
                return lane.crawl_next
            cfg = self.profile.engines["crawl"]
            lane.crawl_factory = CrawlTargetFactory(rng=self.rng, seeds=cfg.seeds,
                                                    max_depth=cfg.max_depth,
                                                    same_site_only=cfg.same_site_only,
                                                    domains=self.domains)
            return lane.crawl_factory.make(identity)
        if eng_name == "dns":
            if lane.name == "tor":
                # Arbitrary UDP cannot traverse Tor circuits: DoH over the circuit.
                fac = DohNoise(rng=self.rng, words=words, domains=self.domains)
                return fac.make(identity)
            fac = DnsNoise(rng=self.rng, words=words, domains=self.domains, plan=self.dns_plan)
            _packet, domain, server = fac.make_query()
            via = "your normal DNS" if server == "system" else self._dns_label(server)
            return Target(url=f"dns://{server}/{domain}", kind="dns", timeout=4.0, note=f"via {via}")
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
            now = time.localtime()
            if now.tm_yday != self.day:
                self.day = now.tm_yday
                self.reset_budgets()
                self.paused = False if self.paused == "failures" else self.paused
            if self.rate is not None and now.tm_hour != self.hour:
                total = self.plan_hour(now.tm_hour)
                # start somewhere inside the hour, never in a burst on the hour mark
                self.sleep_fn(self.rng.uniform(0, min(600.0, 3600.0 / max(total, 0.25))) * self.profile.realtime_scale)
                continue
            if self.paused:
                self.sleep_fn(1.0)
                continue
            try:
                worked = self.run_once()
            except MaxFailuresExceeded as exc:
                self._emit("error", "scheduler", f"{exc}. Pausing until tomorrow.")
                self.paused = "failures"           # auto-resumes at the next day reset
                continue
            if not worked:
                self.sleep_fn(2.0)       # all budgets spent or lanes gated: idle politely
        self.close()
        self._emit("info", "scheduler", "Noise generation stopped.")

    # ── human sessions (app mode) ───────────────────────────────────────────
    def _sleep(self, seconds: float) -> None:
        self.sleep_fn(max(0.0, seconds) * self.profile.realtime_scale)

    def _now(self) -> float:
        return float(self.clock())

    def run_sessions(self) -> None:
        """Alternate browsing sessions and breaks until stopped."""
        self._emit("info", "scheduler", "Noise generation started (human-like sessions).")
        last = None
        while not self._stopped():
            now = time.localtime()
            if now.tm_yday != self.day:
                self.day = now.tm_yday
                self.reset_budgets()
                if self.paused == "failures":
                    self.paused = False
            if self.paused:
                self.activity = {"state": "paused"}
                self._sleep(1.0)
                continue
            block = self.sessions.next_block(now.tm_hour + now.tm_min / 60, last)
            if block.kind == "break":
                until = time.time() + block.seconds * self.profile.realtime_scale
                self.activity = {"state": "break", "until": until, "since": time.time()}
                if block.seconds > 30:
                    self._emit("info", "scheduler", f"Taking a break for about {block.seconds / 60:.0f} min "
                               f"(until {time.strftime('%H:%M', time.localtime(until))}), like a person stepping away.")
                self._background_lookups(block.seconds)
                last = "break"
                continue
            lane = next((l for l in self.lanes if l.name == block.lane and l.open), None) \
                or next((l for l in self.lanes if l.name == "clearnet"), None)
            if lane is None:
                self.activity = {"state": "waiting", "why": "waiting for Tor to connect"}
                self._sleep(5)
                continue
            try:
                if lane.name == "tor":
                    self._onion_session(lane, block.seconds)
                else:
                    self._web_session(lane, block.seconds)
            except MaxFailuresExceeded as exc:
                self._emit("error", "scheduler", f"{exc}. Pausing until tomorrow.")
                self.paused = "failures"
            last = "session"
        self.activity = {"state": "stopped"}
        self.close()
        self._emit("info", "scheduler", "Noise generation stopped.")

    def _background_lookups(self, seconds: float) -> None:
        """During a break: mostly quiet, with the odd background DNS lookup (devices do that)."""
        end = self._now() + seconds * self.profile.realtime_scale
        lane = next((l for l in self.lanes if l.name == "clearnet" and "dns" in l.engines), None)
        while not self._stopped() and self._now() < end:
            gap = min(end - self._now(), self.rng.uniform(120, 900) * self.profile.realtime_scale)
            self.sleep_fn(max(0.0, gap))
            if lane and not self._stopped() and self._now() < end and self.rng.random() < 0.5:
                ident, pacer = self._identity_pacer_for(lane)
                self._execute(lane, "dns", ident, pacer, self._make_target(lane, "dns", ident))

    def _set_doing(self, **kw) -> None:
        self.activity.update(kw)

    def _web_session(self, lane: Lane, seconds: float) -> None:
        from .engines import CrawlTargetFactory, SearchTargetFactory
        from .sessions import related_query
        sp = self.sessions
        end = self._now() + seconds * self.profile.realtime_scale
        identity, pacer = self._identity_pacer_for(lane)
        engines = set(lane.engines)
        self.session_no += 1
        if lane.search_factory is None:
            lane.search_factory = SearchTargetFactory(rng=self.rng, words=self.wordlist, queries=self.queries,
                                                      html_mode=not getattr(lane.transport, "is_browser", False))
        sf = lane.search_factory
        crawl = CrawlTargetFactory(rng=self.rng, seeds=[], domains=self.domains)
        theme, origin = (sf.queries.next(identity) if "search" in engines else ("", "none"))
        self.activity = {"state": "session", "session": self.session_no, "lane": lane.name, "theme": theme,
                         "since": time.time(), "until": time.time() + seconds * self.profile.realtime_scale,
                         "steps": 0, "doing": "starting"}
        self._emit("info", "scheduler", f"Session {self.session_no} started ({seconds / 60:.0f} min)"
                   + (f" — topic: “{theme}”" if theme else ""))
        query = theme
        serp: tuple[str, str] | None = None
        visited: set[str] = set()
        last_role, last_html, last_url = None, "", ""
        if "search" not in engines or ("crawl" in engines and self.rng.random() < 0.15):
            last_role = "open_site"                       # sometimes start by opening a familiar site
            self.activity["theme"] = "popular sites"
        while not self._stopped() and self._now() < end:
            if last_role == "open_site":
                target, role, eng = crawl.make(identity), "page", "crawl"
            else:
                step = sp.next_step(last_role, serp is not None) if "search" in engines else "follow"
                target, role, eng = None, "page", "search"
                if step == "search":
                    target, role = sf.make_for(query, origin), "search"
                elif step == "related_search":
                    nq = related_query(self.rng, last_html, query) if last_role == "page" else None
                    if nq:
                        query, origin = nq, "related to last page"
                    else:
                        query, origin = sf.queries.next(identity)
                    target, role = sf.make_for(query, origin), "search"
                elif step in ("result", "another_result") and serp:
                    target = sf.result_target(serp[1], serp[0], exclude=visited)
                elif step == "follow" and last_html:
                    target = crawl.next_from(last_html, last_url)
                    eng = "crawl" if "crawl" in engines else "search"
                if target is None:                         # nothing to click: search something related
                    query, origin = sf.queries.next(identity)
                    target, role = sf.make_for(query, origin), "search"
            target.dwell = sp.dwell(role)
            host = (target.url.split("/")[2] if "://" in target.url else target.url)
            self._set_doing(doing=("searching “%s”" % query) if role == "search" else f"reading {host}",
                            steps=self.activity.get("steps", 0) + 1, theme=query)
            if eng not in lane.budgets:
                eng = next(iter(lane.budgets))
            result = self._execute(lane, eng, identity, pacer, target, settle=False)
            visited.add(target.url)
            if result.ok:
                last_html, last_url = result.html or "", target.url
                if role == "search":
                    serp = (target.url, last_html)
                last_role = "search" if role == "search" else "page"
                if not getattr(lane.transport, "is_browser", False):
                    self._sleep(target.dwell)              # plain HTTP: time spent "reading"
            else:
                last_role = None if role == "search" else last_role
            if "dns" in engines and self.rng.random() < 0.4:
                for _ in range(self.rng.randint(1, 2)):
                    self._execute(lane, "dns", identity, pacer, self._make_target(lane, "dns", identity))
            self._sleep(sp.think())
        self._emit("info", "scheduler", f"Session {self.session_no} ended after {self.activity.get('steps', 0)} steps.")

    def _onion_session(self, lane: Lane, seconds: float) -> None:
        sp = self.sessions
        end = self._now() + seconds * self.profile.realtime_scale
        identity, pacer = self._identity_pacer_for(lane)
        self.session_no += 1
        self.activity = {"state": "session", "session": self.session_no, "lane": "tor", "theme": "onion sites",
                         "since": time.time(), "until": time.time() + seconds * self.profile.realtime_scale,
                         "steps": 0, "doing": "starting"}
        self._emit("info", "scheduler", f"Session {self.session_no} started on Tor ({seconds / 60:.0f} min).")
        while not self._stopped() and self._now() < end and lane.open:
            target = self._make_target(lane, "onion", identity)
            target.dwell = sp.dwell("page")
            self._set_doing(doing=f"reading {target.url.split('/')[2][:16]}….onion",
                            steps=self.activity.get("steps", 0) + 1)
            result = self._execute(lane, "onion", identity, pacer, target)
            if result.ok:
                self._sleep(target.dwell)
            else:
                self._sleep(self.rng.uniform(10, 40))     # onion sites are often slow or offline
            self._sleep(sp.think())

    def close(self) -> None:
        """Release transport resources (real browsers) from the thread that used them."""
        for lane in self.lanes:
            fn = getattr(lane.transport, "close", None)
            if fn:
                try:
                    fn()
                except Exception:
                    pass
