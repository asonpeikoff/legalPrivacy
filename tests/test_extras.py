"""Optional extras: active hours, data cap, typos, search-engine weights, browser-like DNS,
hover and text selection, polite mode, settings validation."""
import os
import random
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from noisycricket import engines  # noqa: E402
from noisycricket.config import ConfigError, Profile, apply_overrides  # noqa: E402
from noisycricket.engines import ENGINE_NAMES, add_typo, build_dns_query, pick_search_endpoint  # noqa: E402
from noisycricket.humanize import HumanDriver  # noqa: E402
from noisycricket.rate import RatePlanner  # noqa: E402
from noisycricket.scheduler import Runner, build_lanes  # noqa: E402
from noisycricket.transports import HitResult  # noqa: E402


def _runner(extras, probe=None):
    p = Profile(name="t")
    apply_overrides(p, {"extras": extras, "rate": {"mode": "steady", "level": 5}})
    return Runner(profile=p, lanes=build_lanes(p, ["w"]), wordlist=["w"], sleep_fn=lambda s: None,
                  probe_fn=probe or (lambda *a: HitResult(200, "x" * 1000)),
                  rate=RatePlanner(random.Random(1), p.rate), mix=True)


def test_active_hours_rest_outside_the_window_and_wrap_past_midnight():
    r = _runner({"active_hours": True, "active_from": 8, "active_to": 22})
    assert r.plan_hour(3) == 0 and all(b.per_day == 0 for l in r.lanes for b in l.budgets.values())
    assert r.plan_hour(12) > 0
    night = _runner({"active_hours": True, "active_from": 20, "active_to": 2})
    assert night.plan_hour(23) > 0 and night.plan_hour(1) > 0 and night.plan_hour(10) == 0


def test_daily_data_cap_pauses_until_tomorrow():
    from noisycricket.scheduler import MaxFailuresExceeded
    r = _runner({"daily_data_mb": 0.002})               # 2 kB
    r.plan_hour(12)
    hit_cap = False
    for _ in range(10):
        try:
            r.run_once(wait=False)
        except MaxFailuresExceeded as exc:
            hit_cap = "data cap" in str(exc)
            break
    assert hit_cap and r.bytes_today >= 2000
    r.reset_budgets()
    assert r.bytes_today == 0


def test_typos_are_small_human_slips():
    rng = random.Random(3)
    q = "best hiking boots for beginners"
    for _ in range(50):
        t = add_typo(rng, q)
        assert t != q and abs(len(t) - len(q)) <= 1 and t.split()[0] == "best"


def test_search_engine_weights_and_caps_apply():
    engines._ENGINE_HITS.clear()
    engines.configure_search({n: (10 if n == "Bing" else 0) for n in ENGINE_NAMES})
    picks = {pick_search_endpoint(random.Random(i), now=1e6 + i) for i in range(8)}
    assert picks == {"https://www.bing.com/search?q="}
    engines._ENGINE_HITS.clear()
    engines.configure_search({})
    try:
        apply_overrides(Profile(name="t"), {"extras": {"search_engines": {n: 0 for n in ENGINE_NAMES}}})
        raise AssertionError("all engines off was accepted")
    except ConfigError:
        pass


def test_dns_questions_for_aaaa_and_https_records():
    for qt, code in (("A", 1), ("AAAA", 28), ("HTTPS", 65)):
        pkt = build_dns_query("example.com", 7, qt)
        assert struct.unpack(">HH", pkt[-4:]) == (code, 1)


class _Page:
    mobile = False
    def __init__(self): self.y, self.sel = 0.0, 0
    def viewport(self): return (1280, 720)
    def page_height(self): return 8000
    def scroll_y(self): return self.y
    def move(self, x, y): pass
    def wheel(self, dy): self.y = max(0.0, min(8000 - 720, self.y + dy))
    def sleep(self, s): pass
    def stopped(self): return False
    def select_text(self, x, y, dx):
        self.sel += 1
        return True


def test_reading_includes_hovers_and_occasional_text_selection():
    tot = {"hovers": 0, "selections": 0}
    for seed in range(80):
        s = HumanDriver(random.Random(seed), _Page()).browse()
        tot["hovers"] += s["hovers"]
        tot["selections"] += s["selections"]
    assert tot["hovers"] > 20 and 0 < tot["selections"] < tot["hovers"]


def test_polite_mode_avoids_the_same_site_within_a_minute():
    from noisycricket.transports import Target
    got = []
    r = _runner({"polite": True}, lambda lane, ident, target: (got.append(target.url), HitResult(200, ""))[1])
    r.plan_hour(12)
    r.clock = lambda: 1000.0
    r._host_last["same.example"] = 990.0                     # visited 10 s ago
    queue = [Target(url="https://same.example/a"), Target(url="https://other.example/b")]
    r._make_target = lambda lane, eng, ident: queue.pop(0) if queue else Target(url="https://x.example/")
    for lane in r.lanes:                                      # only page-type engines this time
        lane.engines = [e for e in lane.engines if e == "crawl"] or lane.engines
    r.run_once(wait=False)
    assert got == ["https://other.example/b"]
    r2 = _runner({"polite": False}, lambda lane, ident, target: (got.append(target.url), HitResult(200, ""))[1])
    r2.plan_hour(12)
    r2._host_last["same.example"] = r2.clock() - 10
    r2._make_target = lambda lane, eng, ident: Target(url="https://same.example/a")
    got.clear()
    r2.run_once(wait=False)
    assert got == ["https://same.example/a"]                  # off: no change
