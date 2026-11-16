"""Realistic query generation: a weighted mix of real-world sources.

Why a mix: any single fixed pool (especially a small hand-written one) would be
hit at a far higher rate than real users hit it, which is itself a fingerprint.
So no source dominates, the hand-written list is a minority, and every
generated query is de-duplicated against recent history.
"""
from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import dataclass

from .feeds import FeedStore
from .safety import safe_query

# ---- small built-in fallback (minority source; also used when offline) ------
BUILTIN_THEMES: dict[str, list[str]] = {
    "tech": ["python", "linux", "docker", "kubernetes", "rust language", "git commands", "sqlite database",
             "bash scripting", "ssh keys setup", "raspberry pi projects", "home assistant", "nginx reverse proxy",
             "rest api", "tailwind css", "react components", "typescript tutorial", "dns record types"],
    "cooking": ["sourdough bread recipe", "cast iron seasoning", "pasta carbonara", "easy weeknight dinner",
                "air fryer chicken breast", "chocolate chip cookies", "thai green curry", "vegetarian chili",
                "meal prep containers", "french press ratio", "roasted vegetables", "homemade pizza dough"],
    "travel": ["flight tracker live", "national park annual pass", "hiking boots lightweight",
               "camping checklist", "carry on bag dimensions", "scenic train routes", "hotel booking discounts",
               "travel insurance comparison", "road trip map offline", "campsite reservation tips"],
    "entertainment": ["movie showtimes today", "sci fi novels", "indie video games", "vinyl record player",
                      "streaming series release dates", "audio drama podcasts", "fantasy book series",
                      "concert tour dates", "board games strategy"],
    "health_fitness": ["5k running schedule beginner", "morning mobility routine", "core strength exercises",
                       "stretching for lower back", "healthy meal prep ideas", "kettlebell workout",
                       "sleep tracking accuracy", "electrolyte drinks"],
    "science_nature": ["james webb telescope images", "solar eclipse path", "bird migration tracker",
                       "mycelium network", "quantum computing basics", "deep sea exploration",
                       "renewable energy battery", "night sky constellations"],
    "home_daily": ["indoor plants low light", "compost tumbler", "drip irrigation raised bed",
                   "soil ph test kit", "hand woodworking tools", "local public transit schedule",
                   "recycling guidelines", "lawn aeration schedule"],
}
BUILTIN_TOPICS = [t for v in BUILTIN_THEMES.values() for t in v]
PATTERNS = ["{t}", "{t}", "{t} tutorial", "how to use {t}", "best {t}", "{t} tips", "{t} review",
            "what is {t}", "{t} for beginners", "{t} near me", "{t} reddit", "{t} guide"]
REFINERS = ["review", "price", "near me", "reddit", "vs", "how to", "meaning", "images", "for beginners",
            "2026", "cheap", "best", "youtube", "examples", "tutorial"]

WEIGHTS = {"live": 0.38, "dataset": 0.34, "builtin": 0.10, "words": 0.04}


@dataclass
class QueryConfig:
    live: bool = True
    dataset: bool = True
    builtin: bool = True
    words: bool = True


def _typo(rng: random.Random, q: str) -> str:
    """Rare adjacent-letter swap inside a longer word, the way fast typists err."""
    words = q.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    if not idx:
        return q
    i = rng.choice(idx)
    w = words[i]
    j = rng.randrange(1, len(w) - 2)
    words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    return " ".join(words)


class QuerySource:
    def __init__(self, rng: random.Random, store: FeedStore | None, cfg: QueryConfig | None = None,
                 words: list[str] | None = None):
        self.rng = rng
        self.store = store
        self.cfg = cfg or QueryConfig()
        # dictionary words are a small garnish: short common-looking lowercase words only
        self.words = [w for w in (words or []) if 4 <= len(w) <= 8 and w.isalpha() and w.islower()][:200_000]
        self._recent: deque[str] = deque(maxlen=500)
        self._last: dict[int, str] = {}

    def _pools(self) -> dict[str, list[str]]:
        st = self.store
        pools: dict[str, list[str]] = {}
        if st:
            if self.cfg.live:
                live = st.items("trends") + st.items("wiki") + st.items("news")
                if live:
                    pools["live"] = live
            if self.cfg.dataset:
                ds = st.items("dataset")
                if ds:
                    pools["dataset"] = ds
        if self.cfg.builtin or not pools:           # never run completely dry
            pools["builtin"] = BUILTIN_TOPICS
        if self.cfg.words and self.words:
            pools["words"] = self.words
        return pools

    def next(self, identity=None) -> tuple[str, str]:
        """Return (query, origin). origin ∈ live|dataset|builtin|words|refine."""
        ident = getattr(identity, "id", None)
        prev = self._last.get(ident)
        if prev and self.rng.random() < 0.18:        # a person refines the previous search
            q = f"{prev} {self.rng.choice(REFINERS)}"
            if safe_query(q) and q not in self._recent:
                return self._finish(ident, q, "refine")
        pools = self._pools()
        names = list(pools)
        for _ in range(8):
            origin = self.rng.choices(names, weights=[WEIGHTS[n] for n in names])[0]
            raw = self.rng.choice(pools[origin])
            if origin == "builtin":
                raw = self.rng.choice(PATTERNS).format(t=raw)
            elif origin == "words":
                raw = self.rng.choice(["{w}", "{w} meaning", "define {w}", "{w} synonym"]).format(w=raw)
            if raw not in self._recent and safe_query(raw):
                return self._finish(ident, raw, origin)
        return self._finish(ident, self.rng.choice(BUILTIN_TOPICS), "builtin")

    def _finish(self, ident, q: str, origin: str) -> tuple[str, str]:
        q = re.sub(r"\s+", " ", q).strip()
        if origin != "dataset" and self.rng.random() < 0.03:
            q = _typo(self.rng, q)
        self._recent.append(q)
        self._last[ident] = q if origin != "refine" else q
        if len(self._last) > 200:
            self._last.pop(next(iter(self._last)))
        return q, origin
