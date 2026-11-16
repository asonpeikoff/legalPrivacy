"""Per-identity consistent fingerprints.

An identity is a pinned tuple (user agent, accept-language, timing persona,
query taste). Identities stay consistent for their lifetime so traffic looks
like distinct people instead of one rotating bot.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# UA pool curated per persona class: desktop Chrome, desktop Firefox, mobile.
UA_POOL: dict[str, list[str]] = {
    "desktop-chrome": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ],
    "desktop-firefox": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    ],
    "mobile": [
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
    ],
}

LANGUAGE_POOL = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.8",
    "es-ES,es;q=0.9,en;q=0.6",
    "fr-FR,fr;q=0.9,en;q=0.5",
    "de-DE,de;q=0.9,en;q=0.5",
]

PERSONA_CLASSES = list(UA_POOL)

# fast / normal / slow pacing multipliers per persona
PACE_MULTIPLIERS = {"fast": 0.6, "normal": 1.0, "slow": 1.8}


@dataclass
class Identity:
    id: int
    persona: str
    user_agent: str
    accept_language: str
    pace: str                 # fast | normal | slow
    query_taste: list[str]    # seed words this identity prefers

    @property
    def pace_multiplier(self) -> float:
        return PACE_MULTIPLIERS[self.pace]


def _query_taste(rng: random.Random, wordlist: list[str], size: int = 12) -> list[str]:
    if not wordlist:
        return ["weather", "news", "recipes"]
    return rng.sample(wordlist, min(size, len(wordlist)))


def make_identity(rng: random.Random, ident_id: int, wordlist: list[str]) -> Identity:
    """Build one pinned identity from the shared RNG."""
    persona = rng.choice(PERSONA_CLASSES)
    return Identity(
        id=ident_id,
        persona=persona,
        user_agent=rng.choice(UA_POOL[persona]),
        accept_language=rng.choice(LANGUAGE_POOL),
        pace=rng.choice(["fast", "normal", "normal", "slow"]),
        query_taste=_query_taste(rng, wordlist),
    )


def headers_for(identity: Identity) -> dict[str, str]:
    """Headers pinned to this identity (no per-request rotation — that is the point)."""
    return {
        "User-Agent": identity.user_agent,
        "Accept-Language": identity.accept_language,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "DNT": "1",
    }
