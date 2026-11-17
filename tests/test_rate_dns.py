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


def test_recommended_resolvers_primaries_then_fallback():
    cfg = DnsConfig(mode="secure")
    assert [r["ip"] for r in cfg.resolvers if r["role"] == "primary"] == \
        ["80.152.203.134", "193.31.24.55", "80.153.146.105"]
    plan = DnsPlan(random.Random(3), cfg)
    assert {plan.pick()[0] for _ in range(100)} == {"80.152.203.134", "193.31.24.55", "80.153.146.105"}
    for ip in ("80.152.203.134", "193.31.24.55"):
        plan.health[ip] = {"ip": ip, "ok": True, "rtt_ms": 900, "egress": None}      # answering, but slow
    plan.health["80.153.146.105"] = {"ip": "80.153.146.105", "ok": False, "rtt_ms": None, "egress": None}
    plan.health["9.9.9.9"] = {"ip": "9.9.9.9", "ok": True, "rtt_ms": 20, "egress": None}
    assert {plan.pick()[0] for _ in range(50)} == {"9.9.9.9"}                 # fallback only now
    plan.health["9.9.9.9"]["ok"] = False
    assert {plan.pick()[0] for _ in range(50)} <= {"80.152.203.134", "193.31.24.55"}   # slow beats nothing


def test_interception_is_detected():
    plan = DnsPlan(random.Random(1), DnsConfig(mode="secure"))
    plan.system_egress = "192.0.2.7"
    for r in plan.cfg.resolvers:
        plan.health[r["ip"]] = {"ip": r["ip"], "ok": True, "rtt_ms": 1, "egress": "192.0.2.7"}
    assert plan.report()["intercepted"]
    plan.health["193.31.24.55"]["egress"] = "193.31.24.55"
    assert plan.verdict("193.31.24.55") == "ok"


def test_user_can_override_and_reset_resolvers():
    p = Profile(name="t")
    apply_overrides(p, {"dns": {"resolvers": [{"ip": "10.0.0.53", "name": "mine", "role": "primary"}]}})
    assert [r["ip"] for r in p.dns.resolvers] == ["10.0.0.53"]
    apply_overrides(p, {"dns": {"reset_resolvers": True}})
    assert len(p.dns.resolvers) == 4


def test_mix_mode_switches_in_blocks_with_the_right_share():
    t = [0.0]
    plan = DnsPlan(random.Random(4), DnsConfig(mode="mix", system_share=0.7),
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
    for bad in ({"dns": {"mode": "weird"}}, {"dns": {"resolvers": [{"ip": "not-an-ip"}]}}, {"rate": {"level": 11}}):
        try:
            apply_overrides(Profile(name="t"), bad)
            raise AssertionError(f"accepted {bad}")
        except ConfigError:
            pass
