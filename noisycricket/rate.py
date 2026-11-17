"""How much noise per hour.

The user picks an activity LEVEL (1-10) on a slider. The recommended mode,
"random", varies the amount every hour around that level (log-normal), follows
a day/night curve, and leaves some hours almost empty, the way a person is busy,
then away. "steady" keeps the same amount every hour.

Benchmark used to label the slider: active US desktop users run about 126 Google
searches a month (SparkToro, 2024), roughly 4 a day; about 2.7 per internet user
per day worldwide. Noise only hides you if it is at least comparable to your real
activity, so even the middle levels are deliberately busier than that average.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .pacing import diurnal_factor

# searches per hour at each slider level (1..10)
SEARCHES_PER_HOUR = [0.5, 1, 1.5, 2.5, 4, 6, 9, 13, 20, 30]
LEVEL_NAMES = ["barely there", "very light", "light", "light-moderate", "moderate",
               "moderate-busy", "busy", "very busy", "heavy", "very heavy"]
AVG_PERSON_SEARCHES_PER_DAY = 4.2      # SparkToro 2024, active US desktop users (126/month)


@dataclass
class RateConfig:
    mode: str = "random"          # random (recommended) | steady
    level: float = 5.0            # 1..10 slider

    def validate(self) -> None:
        if self.mode not in ("random", "steady"):
            raise ValueError("rate.mode must be random or steady")
        if not 1 <= float(self.level) <= 10:
            raise ValueError("rate.level must be between 1 and 10")


def searches_per_hour(level: float) -> float:
    """Interpolate the table for fractional slider positions."""
    lv = min(10.0, max(1.0, float(level))) - 1
    lo, hi = int(math.floor(lv)), min(9, int(math.floor(lv)) + 1)
    f = lv - lo
    return SEARCHES_PER_HOUR[lo] * (1 - f) + SEARCHES_PER_HOUR[hi] * f


def describe(level: float, search_share: float) -> dict:
    """Numbers for the UI slider label."""
    s = searches_per_hour(level)
    total = s / max(search_share, 0.05)
    from .sessions import active_minutes
    return {"level": level, "name": LEVEL_NAMES[int(round(level)) - 1],
            "minutes_per_hour": round(active_minutes(level)),
            "searches_per_hour": round(s, 1), "actions_per_hour": round(total),
            "searches_per_day_awake": round(s * 15), "vs_average_person": round(s * 15 / AVG_PERSON_SEARCHES_PER_DAY, 1)}


class RatePlanner:
    def __init__(self, rng: random.Random, cfg: RateConfig):
        self.rng, self.cfg = rng, cfg

    def actions_for_hour(self, hour: int, search_share: float) -> float:
        """Total actions (all engines) planned for the given local hour."""
        base = searches_per_hour(self.cfg.level) / max(search_share, 0.05)
        if self.cfg.mode == "steady":
            return base
        if self.rng.random() < 0.12:                      # away from the computer this hour
            return base * self.rng.uniform(0.0, 0.15)
        wobble = self.rng.lognormvariate(0.0, 0.5)        # some hours busy, some quiet
        day = diurnal_factor(hour + self.rng.uniform(0, 1)) / 0.95
        return base * wobble * day
