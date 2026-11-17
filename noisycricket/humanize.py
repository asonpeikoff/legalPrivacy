"""Human browsing behaviour, independent of any browser library.

HumanDriver turns a page "visit" into the kind of activity a person produces:
settle pause, idle mouse drift, bursts of wheel/touch scrolling that start and
end slowly, reading pauses (log-normal, occasionally very long), small
scroll-backs, depth that varies from "glanced at the top" to "read to the end",
and curved mouse paths with a human-sized hover before clicking.

It talks to an Actuator (move / wheel / sleep / page geometry), so the same
logic is unit-tested with a fake and driven for real by Playwright.
"""
from __future__ import annotations

import math
import random
from typing import Protocol


class Actuator(Protocol):
    mobile: bool
    def viewport(self) -> tuple[int, int]: ...
    def page_height(self) -> int: ...
    def scroll_y(self) -> float: ...
    def move(self, x: float, y: float) -> None: ...
    def wheel(self, dy: float) -> None: ...
    def sleep(self, seconds: float) -> None: ...
    def stopped(self) -> bool: ...


def lognormal_clipped(rng: random.Random, median: float, sigma: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, rng.lognormvariate(math.log(median), sigma)))


def reading_pause(rng: random.Random) -> float:
    """Seconds spent reading between scrolls. Median ~2.8s; ~3% are long 'got distracted' pauses."""
    if rng.random() < 0.03:
        return rng.uniform(20, 55)
    return lognormal_clipped(rng, 2.8, 0.7, 0.5, 18.0)


def target_depth(rng: random.Random) -> float:
    """Fraction of the scrollable page this visit reads: most glance, some read to the end."""
    return min(1.0, rng.betavariate(1.3, 2.0) + (0.5 if rng.random() < 0.07 else 0.0))


def scroll_steps(rng: random.Random, distance: float, mobile: bool) -> list[tuple[float, float]]:
    """(dy, delay_seconds) steps for one scroll burst, eased: slow start, fast middle, slow end."""
    steps: list[tuple[float, float]] = []
    remaining = abs(distance)
    sign = 1 if distance >= 0 else -1
    notch = rng.uniform(55, 95) if mobile else rng.choice([100, 100, 100, 120, 53, 80]) + rng.uniform(-6, 6)
    total = max(1, math.ceil(remaining / notch))
    for i in range(total):
        x = (i + 0.5) / total
        speed = 0.35 + 0.65 * math.sin(math.pi * x)             # 0.35 .. 1.0 .. 0.35
        dy = min(remaining, notch * (0.6 + 0.8 * speed) * rng.uniform(0.85, 1.15))
        remaining -= dy
        steps.append((sign * dy, (0.045 if not mobile else 0.03) / speed * rng.uniform(0.6, 1.5)))
        if remaining <= 1:
            break
    return steps


def bezier_path(rng: random.Random, start: tuple[float, float], end: tuple[float, float],
                steps: int | None = None) -> list[tuple[float, float]]:
    """A slightly curved, eased cursor path (cubic Bézier with jittered control points)."""
    (x0, y0), (x1, y1) = start, end
    dist = math.hypot(x1 - x0, y1 - y0)
    n = steps or max(8, min(40, int(dist / 25) + rng.randint(4, 10)))
    spread = max(10.0, dist * 0.18)
    c1 = (x0 + (x1 - x0) * 0.3 + rng.uniform(-spread, spread), y0 + (y1 - y0) * 0.1 + rng.uniform(-spread, spread))
    c2 = (x0 + (x1 - x0) * 0.75 + rng.uniform(-spread, spread), y0 + (y1 - y0) * 0.9 + rng.uniform(-spread, spread))
    pts = []
    for i in range(1, n + 1):
        t = i / n
        t = t * t * (3 - 2 * t)                                  # ease in/out
        u = 1 - t
        pts.append((u**3 * x0 + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * x1,
                    u**3 * y0 + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * y1))
    return pts


class HumanDriver:
    def __init__(self, rng: random.Random, act: Actuator, scale: float = 1.0):
        self.rng, self.act, self.scale = rng, act, max(0.01, scale)
        self.pos: tuple[float, float] = (rng.uniform(200, 600), rng.uniform(200, 500))
        self.stats = {"scrolls": 0, "pauses": 0, "back_scrolls": 0, "moves": 0, "depth": 0.0, "seconds": 0.0}

    # -- primitives --------------------------------------------------------
    def pause(self, seconds: float) -> None:
        s = seconds * self.scale
        self.stats["pauses"] += 1
        self.stats["seconds"] += s
        self.act.sleep(s)

    def move_to(self, x: float, y: float) -> None:
        if self.act.mobile:
            self.pos = (x, y)
            return
        for px, py in bezier_path(self.rng, self.pos, (x, y)):
            if self.act.stopped():
                return
            self.act.move(px, py)
            self.act.sleep(self.rng.uniform(0.004, 0.016) * self.scale)
        self.pos = (x, y)
        self.stats["moves"] += 1

    def scroll_burst(self, distance: float) -> None:
        swipe = getattr(self.act, "swipe", None)
        if self.act.mobile and swipe is not None:            # phones: one finger swipe per burst
            swipe(distance)
            self.act.sleep(self.rng.uniform(0.35, 0.9) * self.scale)   # let the fling settle
            self.stats["scrolls"] += 1
            return
        for dy, delay in scroll_steps(self.rng, distance, self.act.mobile):
            if self.act.stopped():
                return
            self.act.wheel(dy)
            self.act.sleep(delay * self.scale)
        self.stats["scrolls"] += 1

    # -- a whole visit -----------------------------------------------------
    def browse(self, max_seconds: float = 90.0, min_seconds: float = 0.0) -> dict:
        """Behave like a person who just opened this page.

        Reads down to a chosen depth, then — if the planned time on the page isn't up —
        keeps reading: long pauses, small scrolls both ways, the occasional mouse drift.
        """
        rng, act = self.rng, self.act
        w, h = act.viewport()
        self.pause(lognormal_clipped(rng, 1.2, 0.5, 0.4, 4.0))              # page settles / eyes land
        self.move_to(rng.uniform(0.2, 0.8) * w, rng.uniform(0.25, 0.7) * h)
        scrollable = max(0, act.page_height() - h)
        depth = target_depth(rng) if scrollable > h * 0.3 else 0.0
        goal = depth * scrollable
        spent = 0.0
        while act.scroll_y() < goal and not act.stopped() and spent < max_seconds:
            burst = h * rng.uniform(0.25, 0.9)
            self.scroll_burst(burst)
            rp = reading_pause(rng)
            spent += rp
            self.pause(rp)
            if rng.random() < 0.14:                                        # glance back up
                self.scroll_burst(-h * rng.uniform(0.1, 0.4))
                self.stats["back_scrolls"] += 1
                self.pause(lognormal_clipped(rng, 1.4, 0.5, 0.4, 5.0))
            if rng.random() < 0.25:                                        # fidget with the mouse
                self.move_to(rng.uniform(0.1, 0.9) * w, rng.uniform(0.15, 0.85) * h)
            if act.page_height() - h <= 0:
                break
        budget = min_seconds * self.scale
        while self.stats["seconds"] < budget and not act.stopped():       # keep reading until time's up
            self.pause(min(lognormal_clipped(rng, 6.0, 0.6, 1.5, 25.0),
                           max(0.2, (budget - self.stats["seconds"]) / self.scale)))
            if self.stats["seconds"] >= budget:
                break
            r = rng.random()
            if r < 0.45 and scrollable:
                self.scroll_burst(h * rng.uniform(0.15, 0.6) * (1 if rng.random() < 0.8 else -1))
            elif r < 0.7:
                self.move_to(rng.uniform(0.1, 0.9) * w, rng.uniform(0.15, 0.85) * h)
        if rng.random() < 0.5:
            self.pause(lognormal_clipped(rng, 2.0, 0.6, 0.5, 10.0))       # final look before leaving
        self.stats["depth"] = round(min(1.0, act.scroll_y() / scrollable), 2) if scrollable else 0.0
        return self.stats

    def hover_then_click_point(self, x: float, y: float) -> None:
        """Curve to (x, y), settle a human-sized beat; the caller issues the click/tap."""
        self.move_to(x + self.rng.uniform(-4, 4), y + self.rng.uniform(-3, 3))
        self.pause(lognormal_clipped(self.rng, 0.25, 0.5, 0.08, 1.2))
