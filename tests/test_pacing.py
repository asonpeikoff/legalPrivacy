"""Pacing tests: diurnal curve, lognormal minimums, burst suppression."""

from random import Random

from noisycricket.pacing import Budget, Pacer, diurnal_factor


def test_diurnal_curve_bounds():
    for h in range(24):
        f = diurnal_factor(h)
        assert 0.35 <= f <= 1.6, f"hour {h}: {f}"


def test_diurnal_peaks_late_evening():
    assert diurnal_factor(21.0) > diurnal_factor(4.5)


def test_min_gap_suppresses_bursts():
    p = Pacer(Random(7), mu=1.0, sigma=1.5, min_gap=2.0)
    for _ in range(50):
        assert p.next_delay(now_local_hour=12.0) >= 2.0


def test_persona_multiplier():
    slow = Pacer(Random(7), mu=2.2, sigma=0.9, pace_multiplier=1.8)
    fast = Pacer(Random(7), mu=2.2, sigma=0.9, pace_multiplier=0.6)
    assert slow.next_delay(now_local_hour=12.0) > fast.next_delay(now_local_hour=12.0)


def test_backoff_raises_next_delay():
    p = Pacer(Random(7), mu=0.5, sigma=0.2, realtime_scale=0.0)
    p.on_failure(4)
    assert p.next_delay() >= 8.0


def test_budget_exhausts_and_counts_failures():
    b = Budget(per_day=3)
    for _ in range(3):
        b.record_hit(1.0)
    assert b.exhausted
    b2 = Budget(per_day=2)
    b2.record_failure(1.0)
    b2.record_hit(1.0)
    assert b2.failures == 1 and not b2.exhausted
