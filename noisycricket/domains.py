"""Real, popular, safe domains (Tranco ranking) for DNS lookups and page visits."""
from __future__ import annotations

import random
from collections import deque

from .feeds import FeedStore
from .safety import safe_domain

# Used only when the Tranco list has not been downloaded yet. All real, mainstream, non-government.
FALLBACK_DOMAINS = [
    "wikipedia.org", "mozilla.org", "python.org", "github.com", "stackoverflow.com", "bbc.com",
    "nytimes.com", "theguardian.com", "reuters.com", "apnews.com", "npr.org", "weather.com", "imdb.com",
    "reddit.com", "linkedin.com", "amazon.com", "ebay.com", "etsy.com", "walmart.com", "target.com",
    "bestbuy.com", "wired.com", "arstechnica.com", "theverge.com", "medium.com", "quora.com", "yelp.com",
    "tripadvisor.com", "booking.com", "espn.com", "archive.org", "spotify.com", "netflix.com", "twitch.tv",
    "pinterest.com", "cnn.com", "bloomberg.com", "forbes.com", "cnet.com", "techcrunch.com",
    "khanacademy.org", "coursera.org", "duolingo.com", "goodreads.com", "allrecipes.com", "healthline.com",
    "mayoclinic.org", "webmd.com", "stackexchange.com", "wikihow.com",
]


class DomainPool:
    def __init__(self, rng: random.Random, store: FeedStore | None = None):
        self.rng = rng
        self.store = store
        self._recent: deque[str] = deque(maxlen=60)

    def _ranked(self) -> list[str]:
        ranked = self.store.items("tranco") if self.store else []
        deny = self.store.denylist() if self.store else set()
        ranked = [d for d in ranked if safe_domain(d, deny)]
        return ranked or FALLBACK_DOMAINS

    def pick(self) -> str:
        """Zipf-weighted draw: popular sites dominate, like real browsing, but the tail still appears."""
        ranked = self._ranked()
        weights = [1.0 / (i + 8) for i in range(len(ranked))]
        for _ in range(6):
            d = self.rng.choices(ranked, weights=weights)[0]
            if d not in self._recent:
                break
        self._recent.append(d)
        return d

    def hostname(self) -> str:
        d = self.pick()
        return f"www.{d}" if self.rng.random() < 0.45 and d.count(".") == 1 else d
