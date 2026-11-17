"""Hourly rate planning and DNS resolver choice (offline)."""
import os
import random
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CRICKET_HOME", tempfile.mkdtemp(prefix="cricket-test-"))

from noisycricket.config import ConfigError, DnsConfig, Profile, apply_overrides  # noqa: E402
from noisycricket.dnsplan import DnsPlan  # noqa: E402
from noisycricket.rate import RateConfig, RatePlanner, describe, searches_per_hour  # noqa: E402
from noisycricket.scheduler import Runner, build_lanes  # noqa: E402
from noisycricket.transports import HitResult  # noqa: E402


def test_slider_is_monotonic_and_labelled_with_benchmarks():
    vals = [searches_per_hour(lv) for lv in range(1, 11)]
    assert vals == sorted(vals) and vals[0] < 1 and vals[-1] >= 20
    d = describe(5, 0.3)
    assert d["searches_per_hour"] == 4 and d["vs_average_person"] > 1


def test_random_mode_varies_hour_to_hour_and_has_quiet_hours():
    rp = RatePlanner(random.Random(1), RateConfig(mode="random", level=5))
    xs = [rp.actions_for_hour(h % 24, 0.3) for h in range(24 * 30)]
    assert statistics.pstdev(xs) > 0.3 * statistics.mean(xs)
    assert sum(x < 2 for x in xs) > 24 * 30 * 0.05            # some near-empty "away" hours
    night = [rp.actions_for_hour(4, 0.3) for _ in range(400)]
    evening = [rp.actions_for_hour(20, 0.3) for _ in range(400)]
    assert statistics.mean(evening) > statistics.mean(night)


def test_steady_mode_is_constant():
    rp = RatePlanner(random.Random(1), RateConfig(mode="steady", level=5))
    assert len({round(rp.actions_for_hour(h, 0.3), 6) for h in range(24)}) == 1


def test_plan_hour_sets_budgets_and_spreads_pacing():
    p = Profile(name="t")
    apply_overrides(p, {"rate": {"mode": "steady", "level": 6}})
    lanes = build_lanes(p, ["w"])
    r = Runner(profile=p, lanes=lanes, wordlist=["w"], sleep_fn=lambda s: None,
               probe_fn=lambda *a: HitResult(200, ""), rate=RatePlanner(random.Random(2), p.rate))
    total = r.plan_hour(14)
    budgeted = sum(b.per_day for l in r.lanes for b in l.budgets.values())
    assert abs(budgeted - total) <= len(lanes[0].budgets) + 1
    lo, hi = r._pace
    assert lo < 3600 / total < hi                                  # spread across the hour, not a burst


def test_dns_defaults_to_your_normal_resolver():
    assert Profile(name="t").dns.mode == "system"
    plan = DnsPlan(random.Random(1), DnsConfig())
    assert all(plan.pick()[0] == "system" for _ in range(20))


def test_secure_mode_uses_opennic_quad9_and_custom_only_when_enabled():
    cfg = DnsConfig(mode="secure", opennic=True, quad9=True, custom=["10.0.0.53"])
    plan = DnsPlan(random.Random(3), cfg, ["148.113.235.58|ns1.ca|CA"])
    ips = {plan.pick()[0] for _ in range(200)}
    assert ips == {"148.113.235.58", "9.9.9.9", "149.112.112.112", "10.0.0.53"}
    cfg2 = DnsConfig(mode="secure", opennic=False, quad9=False)
    assert DnsPlan(random.Random(3), cfg2, ["148.113.235.58|ns1.ca|CA"]).pick()[0] == "system"


def test_mix_mode_switches_in_blocks_with_the_right_share():
    t = [0.0]
    plan = DnsPlan(random.Random(4), DnsConfig(mode="mix", quad9=True, system_share=0.7),
                   [], clock=lambda: t[0])
    modes, runs, last = [], 0, None
    for _ in range(20000):                 # one minute steps over ~2 weeks
        m = plan.current_mode()
        runs += m != last
        last = m
        modes.append(m)
        t[0] += 60
    share = modes.count("system") / len(modes)
    assert 0.6 < share < 0.8
    assert runs < len(modes) / 8                            # blocks, not per-lookup flapping


def test_bad_dns_settings_rejected():
    for bad in ({"dns": {"mode": "weird"}}, {"dns": {"custom": "not-an-ip"}}, {"rate": {"level": 11}}):
        try:
            apply_overrides(Profile(name="t"), bad)
            raise AssertionError(f"accepted {bad}")
        except ConfigError:
            pass
