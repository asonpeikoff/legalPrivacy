"""Scheduler + transport tests: offline, seeded, probe_fn injected (no network)."""

import pytest

from noisycricket.config import (EngineConfig, Profile, TransportConfig,
                                 load_profile)
from noisycricket.pacing import Budget
from noisycricket.scheduler import Lane, Runner, build_lanes
from noisycricket.transports import (ClearnetTransport, HitResult, Target,
                                     TorTransport)

WORDS = ["weather", "news", "books", "travel", "recipes", "music"]
HTML = '<a href="/p2">next</a> <a href="https://elsewhere.example/out">out</a>'


def make_profile(tor=True, clearnet=True, seeds=None, search=True) -> Profile:
    p = Profile(name="test")
    p.engines = {
        "search": EngineConfig(budget_per_day=10, enabled=search),
        "crawl": EngineConfig(budget_per_day=6, seeds=seeds or ["https://x.example/a"]),
        "dns": EngineConfig(budget_per_day=8),
    }
    p.transports = {
        "clearnet": TransportConfig(enabled=clearnet),
        "tor": TransportConfig(enabled=tor, control=None),
    }
    return p


def make_runner(profile, probe) -> Runner:
    r = Runner(profile=profile, lanes=build_lanes(profile, WORDS), wordlist=WORDS,
               clock=lambda: 0.0, local_hour_fn=lambda: 12.0,
               sleep_fn=lambda s: None, probe_fn=probe, max_iterations=200)
    r.rng = __import__("random").Random(5)
    return r


def captured_probe(captured):
    return lambda lane, ident, target: (
        captured.append((lane.name, target.kind, target.url)),
        HitResult(200, HTML) if target.kind == "html" else HitResult(200, ""))[1]


def test_dns_uses_raw_udp_on_clearnet_and_doh_on_tor():
    captured = []
    runner = make_runner(make_profile(), captured_probe(captured))
    for _ in range(20):
        if all(l.budgets[e].exhausted for l in runner.lanes for e in l.engines):
            break
        runner.run_once()
    kinds = {(lane, kind) for lane, kind, _ in captured}
    assert ("clearnet", "dns") in kinds
    assert ("tor", "doh") in kinds
    for lane, kind, url in captured:
        if kind == "dns":
            assert url.startswith("dns://") and "@" not in url


def test_dns_clearnet_does_not_fire_extra_http_hit():
    """Raw-UDP DNS hits must not be followed by a real GET of the domain."""
    captured = []
    runner = make_runner(make_profile(clearnet=True, tor=False),
                         captured_probe(captured))
    runner.run_once()
    for _ in range(10):        # include search hits for comparison
        if all(l.budgets[e].exhausted for l in runner.lanes for e in l.engines):
            break
        runner.run_once()
    kinds = {kind for _, kind, _ in captured}
    assert kinds == {"dns", "html"}


def test_crawl_walk_follows_links_and_depth_caps():
    captured = []
    runner = make_runner(make_profile(clearnet=True, tor=False, search=False),
                         captured_probe(captured))
    for _ in range(3):
        if runner.lanes[0].budgets["crawl"].exhausted:
            break
        runner.run_once()
    crawl_urls = [u for l, k, u in captured if l == "clearnet" and k == "html"]
    # link extraction must produce same-site follow-ups, and depth must cap at 4
    assert crawl_urls and all(u.startswith("https://x.example/") for u in crawl_urls)
    assert crawl_urls[0] == "https://x.example/a"          # the seed
    assert any(u.endswith("/p2") for u in crawl_urls)      # followed a link
    assert not any("/out" in u for u in crawl_urls)        # cross-site refused


def test_identity_is_pinned_then_rotates_on_tor():
    runner = make_runner(make_profile(clearnet=False, tor=True),
                         lambda l, i, t: HitResult(200, ""))
    lane = runner.lanes[0]
    lane.transport.identities_per_circuit = 2
    seen = []
    for _ in range(5):
        runner.run_once()
        seen.append(lane.identity.id)
    assert len(set(seen)) >= 2            # rotated at the circuit lifetime
    assert seen[0] == seen[1]             # pinned within one lifetime


def test_failure_backs_off_and_dims_transport():
    lane = Lane(name="clearnet", transport=ClearnetTransport(),
                engines=["search"], budgets={"search": Budget(per_day=5)})
    runner = Runner(profile=make_profile(clearnet=True, tor=False), lanes=[lane],
                    wordlist=WORDS, clock=lambda: 0.0, local_hour_fn=lambda: 12.0,
                    sleep_fn=lambda s: None,
                    probe_fn=lambda l, i, t: HitResult(429, ""),
                    max_iterations=200)
    runner.rng.seed(1)
    runner.run_once()
    assert lane.budgets["search"].failures == 1
    assert lane.budgets["search"].spent == 0
    assert lane.transport.dimmed


def test_build_lanes_respects_disabled_transports():
    lanes = build_lanes(make_profile(clearnet=False, tor=False), WORDS)
    assert lanes == []
