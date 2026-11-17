"""Human-behaviour model tests with a fake page (no browser needed)."""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from legalprivacy.humanize import (HumanDriver, bezier_path, reading_pause,  # noqa: E402
                                   scroll_steps, target_depth)


class FakePage:
    def __init__(self, height=6000, mobile=False):
        self.mobile, self.h, self.y = mobile, height, 0.0
        self.log, self.t = [], 0.0

    def viewport(self): return (1280, 720)
    def page_height(self): return self.h
    def scroll_y(self): return self.y
    def move(self, x, y): self.log.append(("move", x, y))
    def wheel(self, dy):
        self.y = max(0.0, min(self.h - 720, self.y + dy))
        self.log.append(("wheel", dy))
    def sleep(self, s): self.t += s
    def stopped(self): return False


def test_reading_pauses_vary_and_have_a_long_tail():
    rng = random.Random(1)
    xs = [reading_pause(rng) for _ in range(3000)]
    assert 1.8 < statistics.median(xs) < 4.5 and max(xs) > 20 and min(xs) >= 0.5
    assert len({round(x, 1) for x in xs}) > 100            # not a fixed delay


def test_depth_is_mostly_shallow_sometimes_full():
    rng = random.Random(2)
    d = [target_depth(rng) for _ in range(4000)]
    assert statistics.median(d) < 0.5 and sum(x >= 0.95 for x in d) > 100


def test_scroll_burst_is_eased_and_sums_to_distance():
    rng = random.Random(3)
    steps = scroll_steps(rng, 600, mobile=False)
    assert abs(sum(dy for dy, _ in steps) - 600) < 1
    assert steps[0][1] > steps[len(steps) // 2][1]           # slower at the start than mid-burst
    assert all(dy > 0 for dy, _ in steps)


def test_mouse_path_curves_and_ends_on_target():
    rng = random.Random(4)
    pts = bezier_path(rng, (0, 0), (400, 300))
    assert abs(pts[-1][0] - 400) < 1 and abs(pts[-1][1] - 300) < 1
    straight_dev = max(abs(y - 0.75 * x) for x, y in pts)
    assert straight_dev > 1                                    # not a ruler-straight line


def test_visit_produces_scrolls_pauses_and_occasional_backscroll():
    visits = []
    for seed in range(60):
        p = FakePage()
        s = HumanDriver(random.Random(seed), p).browse()
        visits.append((s, p))
    assert any(s["scrolls"] > 2 for s, _ in visits)
    assert any(s["back_scrolls"] for s, _ in visits)
    assert any(any(e[0] == "wheel" and e[1] < 0 for e in p.log) for _, p in visits)
    assert len({round(p.t) for _, p in visits}) > 10          # dwell times differ visit to visit
    assert all(s["pauses"] >= 1 for s, _ in visits)


def test_short_page_does_not_scroll():
    p = FakePage(height=700)
    HumanDriver(random.Random(1), p).browse()
    assert not [e for e in p.log if e[0] == "wheel"]


def test_mobile_uses_no_mouse_moves():
    p = FakePage(mobile=True)
    HumanDriver(random.Random(5), p).browse()
    assert not [e for e in p.log if e[0] == "move"]


def test_visits_mix_down_up_and_reread_patterns():
    stats = {"back_scrolls": 0, "re_reads": 0, "scrolls": 0}
    for seed in range(60):
        p = FakePage(height=6000)
        s = HumanDriver(random.Random(seed), p).browse()
        for k in stats:
            stats[k] += s[k]
    assert stats["scrolls"] > 300 and stats["back_scrolls"] > 20 and stats["re_reads"] > 5


def test_time_on_page_is_mostly_short_with_a_rare_long_tail():
    from legalprivacy.humanize import sample_dwell
    rng = random.Random(1)
    xs = [sample_dwell(rng) for _ in range(200_000)]
    n = len(xs)
    assert 400 < n / sum(x >= 600 for x in xs) < 620          # ~1 in 500 ten minutes or more
    assert 85 < n / sum(x >= 180 for x in xs) < 115           # ~1 in 100 three minutes or more
    assert 22 < n / sum(x >= 60 for x in xs) < 28             # ~1 in 25 a minute or more
    short = sorted(x for x in xs if x < 60)
    assert short[0] >= 1 and short[len(short) // 2] < 15       # weighted towards the short end


def test_long_visits_keep_moving_and_results_pages_use_human_styles():
    st = HumanDriver(random.Random(3), FakePage(height=9000)).browse(dwell=300)
    assert st["scrolls"] > 30 and st["back_scrolls"] > 5 and abs(st["seconds"] - 300) < 30
    styles = {HumanDriver(random.Random(s), FakePage(height=4000)).serp()["serp"] for s in range(60)}
    assert styles == {"glance", "half-screen", "skim"}
