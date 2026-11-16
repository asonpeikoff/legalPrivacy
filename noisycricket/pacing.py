"""Human-cadence pacing: lognormal delays, diurnal curve, failure backoff."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field


@dataclass
class Pacer:
    """Lognormal inter-hit pacing with persona multiplier and diurnal modulation."""
    rng: random.Random
    mu: float = 2.2            # median delay ~ e^2.2 ≈ 9s
    sigma: float = 0.9
    min_gap: float = 1.5       # burst suppression
    realtime_scale: float = 1.0
    pace_multiplier: float = 1.0
    diurnal: bool = True
    _backoff: float = 0.0
    sleep_fn: object = time.sleep

    def next_delay(self, now_local_hour: float | None = None) -> float:
        """Seconds until the next hit for this identity."""
        if self._backoff:
            delay = self._backoff          # backoff is real time, never scaled down
            self._backoff = 0.0
            return delay
        base = self.rng.lognormvariate(self.mu, self.sigma) * self.pace_multiplier
        gap = max(base, self.min_gap)
        if self.diurnal and now_local_hour is not None:
            gap /= diurnal_factor(now_local_hour)
        return gap * self.realtime_scale

    def wait(self, now_local_hour: float | None = None) -> float:
        delay = self.next_delay(now_local_hour)
        self.sleep_fn(delay)
        return delay

    def on_failure(self, attempt: int) -> float:
        """Exponential backoff with jitter after a 403/429/timeout."""
        self._backoff = min(2 ** attempt, 600) * (0.8 + 0.4 * self.rng.random())
        return self._backoff


def diurnal_factor(hour: float) -> float:
    """Local-hour activity multiplier: smooth diurnal curve peaking 19:00–22:00, trough 04:00–05:00.

    Returns a factor in [0.35, 1.6]; divide drawn delays by it.
    """
    h = hour % 24.0
    phase = 2 * math.pi * ((h - 4.5) % 24.0) / 24.0
    wave = 0.5 - 0.5 * math.cos(phase)
    d_eve = min((h - 21.0) % 24.0, (21.0 - h) % 24.0)
    eve_boost = math.exp(-0.5 * (d_eve / 2.5) ** 2)
    combined = 0.5 * wave + 0.5 * eve_boost
    norm = max(0.0, min(1.0, (combined - 0.005) / 0.84))
    return 0.35 + 1.25 * norm


@dataclass
class Budget:
    """Daily per-engine budget drain."""
    per_day: int
    spent: int = 0
    failures: int = 0
    history: list[float] = field(default_factory=list)  # spent timestamps for shrinkage

    @property
    def remaining(self) -> int:
        return max(0, self.per_day - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def record_hit(self, t: float) -> None:
        self.spent += 1
        self.history.append(t)

    def record_failure(self, t: float) -> None:
        """Failures don't drain the hit budget, but count toward daily_max_failures."""
        self.failures += 1
        self.history.append(t)
