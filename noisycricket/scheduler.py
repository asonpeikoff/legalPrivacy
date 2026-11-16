"""Scheduler: identity lifetimes, budgets, engine dispatch across transports."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .config import Profile
from .identity import Identity, make_identity
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


def build_lanes(profile: Profile, wordlist: list[str],
                rng: random.Random | None = None) -> list[Lane]:
    rng = rng or random.Random()
    lanes = []
    tr = profile.transports
    active_count = sum(1 for t in (tr.get("clearnet"), tr.get("tor")) if t and t.enabled) or 1

    if tr.get("clearnet") and tr["clearnet"].enabled:
        lanes.append(Lane(
            name="clearnet",
            transport=ClearnetTransport(proxy=tr["clearnet"].proxy),
            engines=profile.active_engines("clearnet"),
            budgets={name: Budget(per_day=max(1, profile.engines[name].budget_per_day // active_count))
                     for name in profile.active_engines("clearnet")},
        ))

    if tr.get("tor") and tr["tor"].enabled:
        lanes.append(Lane(
            name="tor",
            transport=TorTransport(socks=tr["tor"].socks, control=tr["tor"].control,
                                   identities_per_circuit=tr["tor"].identities_per_circuit,
                                   control_password=tr["tor"].control_password),
            engines=profile.active_engines("tor"),
            budgets={name: Budget(per_day=max(1, profile.engines[name].budget_per_day // active_count))
                     for name in profile.active_engines("tor")},
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

    def __post_init__(self) -> None:
        self.rng = random.Random()
        self._ident_counter = 0
        if self.clock is None:
            self.clock = time.time

    def _probe(self, lane: Lane, identity: Identity, target: Target) -> HitResult:
        if self.probe_fn:
            return self.probe_fn(lane, identity, target)
        if target.kind == "dns":
            return self._dns_probe(target)
        return lane.transport.hit(identity, target)

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
            if not lane.budgets[eng_name].exhausted
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
            lane.consecutive_failures = 0
            if getattr(lane.transport, "dimmed", False):
                lane.transport.dimmed = False
            self._settle(lane, eng_name, target, result)
        else:
            budget.record_failure(self.clock())
            lane.consecutive_failures += 1
            pacer.on_failure(max(1, budget.failures))
            # Rotate identity & request new circuit if failing repeatedly
            if lane.consecutive_failures >= 3:
                lane.identity = None
                lane.hits_by_identity = 0
                lane.consecutive_failures = 0
        total_failures = sum(b.failures for l in self.lanes for b in l.budgets.values())
        if total_failures > self.profile.daily_max_failures:
            raise MaxFailuresExceeded("daily_max_failures exceeded — stopping run")
        if result.dim_transport and hasattr(lane.transport, "dimmed"):
            lane.transport.dimmed = True
        if wait:
            pacer.wait(hour)
        return True

    def _settle(self, lane: Lane, eng_name: str, target: Target, result: HitResult) -> None:
        """Post-hit bookkeeping: advance the crawl random-walk from fetched HTML."""
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
        while iterations < self.max_iterations:
            if all(lane.budgets[e].exhausted
                   for lane in self.lanes for e in lane.engines):
                break
            self.run_once()
            iterations += 1
        return {lane.name: {eng: lane.budgets[eng].spent for eng in lane.engines}
                for lane in self.lanes}
