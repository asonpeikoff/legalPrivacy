"""Human-shaped activity: browsing SESSIONS separated by BREAKS.

People don't fire one request every few minutes. They sit down, search for
something, open a result, read it for a while (scrolling, pausing, scrolling
back), follow a link or two, search for something related, and after 5-25
minutes get up and do something else. Then they come back later and start on
a different topic.

This module only decides the shape: when sessions happen, how long they last,
what the next step inside a session is, and how long to linger. The scheduler
(Runner.run_sessions) carries the steps out.
"""
from __future__ import annotations

import html as _html
import math
import random
import re
from dataclasses import dataclass, field

from .pacing import diurnal_factor
from .safety import safe_query

# active browsing minutes per hour at each slider level (1..10)
ACTIVE_MINUTES = [2, 4, 6, 9, 13, 18, 24, 32, 42, 52]


def active_minutes(level: float) -> float:
    lv = min(10.0, max(1.0, float(level))) - 1
    lo = int(math.floor(lv))
    hi = min(9, lo + 1)
    f = lv - lo
    return ACTIVE_MINUTES[lo] * (1 - f) + ACTIVE_MINUTES[hi] * f


def lognorm(rng: random.Random, median: float, sigma: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, rng.lognormvariate(math.log(median), sigma)))


@dataclass
class Block:
    kind: str            # "session" | "break"
    seconds: float
    lane: str = "clearnet"


@dataclass
class SessionPlanner:
    rng: random.Random
    mode: str = "random"            # random | steady
    level: float = 5.0
    onion_share: float = 0.0        # chance a session happens on the Tor lane (onion browsing)
    started: bool = field(default=False, init=False)
    last_session: float = field(default=600.0, init=False)

    def target_active_fraction(self, hour: float) -> float:
        base = active_minutes(self.level) / 60.0
        if self.mode == "steady":
            return base
        if self.rng.random() < 0.12:                      # stepped away for a while
            return base * 0.1
        return min(0.9, base * self.rng.lognormvariate(0, 0.45) * diurnal_factor(hour) / 0.95)

    def next_block(self, hour: float, last_kind: str | None) -> Block:
        if not self.started:                              # start promptly so the user sees it working
            self.started = True
            return Block("break", self.rng.uniform(3, 15))
        if last_kind != "session":
            minutes = lognorm(self.rng, 10, 0.55, 3, 30) if self.mode == "random" else \
                self.rng.uniform(8, 12)
            lane = "tor" if self.rng.random() < self.onion_share else "clearnet"
            self.last_session = minutes * 60
            return Block("session", minutes * 60, lane)
        frac = max(0.02, self.target_active_fraction(hour))
        mean_break = self.last_session * (1 / frac - 1)  # keeps the hour's active share on target
        if self.mode == "steady":
            return Block("break", mean_break * self.rng.uniform(0.85, 1.15))
        return Block("break", lognorm(self.rng, mean_break, 0.5, 60, 2.5 * 3600))

    # ── inside a session ────────────────────────────────────────────────
    def next_step(self, last_role: str | None, have_serp: bool) -> str:
        """search | result | another_result | follow | related_search"""
        r = self.rng.random()
        if last_role in (None,):
            return "search"
        if last_role == "search":
            return "result" if r < 0.82 else "related_search"
        # just read a page
        if r < 0.32:
            return "follow"
        if r < 0.55 and have_serp:
            return "another_result"
        return "related_search"

    def dwell(self, role: str) -> float:
        """Seconds to spend on a page. Results pages are scanned; articles are read."""
        if role == "search":
            return lognorm(self.rng, 7, 0.5, 2.5, 25)
        if self.rng.random() < 0.15:                      # bounced: not what they wanted
            return self.rng.uniform(3, 9)
        return lognorm(self.rng, 45, 0.7, 10, 240)

    def think(self) -> float:
        """Gap between finishing one page and acting again (typing, deciding)."""
        return lognorm(self.rng, 4, 0.6, 1, 25)


GENERIC_TITLE = re.compile(r"(help center|terms|privacy|cookie|log ?in|sign ?(in|up)|search results|"
                           r"access denied|just a moment|not found|404|403|error|captcha|subscribe|"
                           r"home ?page|welcome to|redirect)", re.I)
STOP_TITLE = re.compile(r"\s+[\-|–—:·•]\s+[^\-|–—:·•]{2,40}$")


def related_query(rng: random.Random, html: str, previous: str) -> str | None:
    """What a reader might search next: words from the page they just read (title / first heading)."""
    cands = []
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    if m:
        cands.append(m.group(1))
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S | re.I)
    if m:
        cands.append(re.sub(r"<[^>]+>", " ", m.group(1)))
    for c in cands:
        t = _html.unescape(re.sub(r"\s+", " ", c)).strip()
        t = STOP_TITLE.sub("", t)                         # drop " - Site Name"
        words = [w.strip(",.!?:;\"'()[]") for w in t.split()]
        words = [w for w in words if w and len(w) > 1][:7]
        q = " ".join(words).lower()
        if GENERIC_TITLE.search(q):
            continue
        if 3 <= len(words) and q != previous.lower() and safe_query(q):
            return q
    return None
