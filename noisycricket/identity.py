"""Per-identity consistent fingerprints.

An identity is a pinned tuple (user agent, client hints, accept-language,
timing persona, query taste). Identities stay consistent for their lifetime
so traffic looks like distinct people instead of one rotating bot.

The UA pool mirrors *real, frequency-weighted* browser populations (several
Chrome majors circulate at once in real traffic — not just the newest).
Client hints are emitted ONLY for Chromium personas: Firefox and Safari do
not send them, and hinting on their UAs would be the giveaway. DNT is dead
(removed from Firefox in v135); Sec-GPC is opt-in and rare, so identities
send it with low probability, pinned per identity.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

def _ua(ua: str, chromium: bool = False, mobile: bool = False,
        platform: str = "Windows") -> dict:
    return {"ua": ua, "chromium": chromium, "mobile": mobile, "platform": platform}

# Real strings observed in live traffic; refresh occasionally. Chrome/Edge use
# the reduced UA (major-version only, frozen .0.0.0); Android freezes the OS
# token at 10 and the device model at "K"; iOS freezes the OS token too.
UA_POOL: dict[str, list[dict]] = {
    "desktop-chrome": [
        _ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            chromium=True, platform="Windows"),
        _ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
            chromium=True, platform="Windows"),
        _ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0",
            chromium=True, platform="Windows"),
        _ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
            chromium=True, platform="macOS"),
        _ua("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
            chromium=True, platform="Linux"),
        # a legacy-major straggler: real populations always carry some
        _ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            chromium=True, platform="Windows"),
    ],
    "desktop-firefox": [
        _ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) "
            "Gecko/20100101 Firefox/158.0", platform="Windows"),
        _ua("Mozilla/5.0 (X11; Linux x86_64; rv:158.0) "
            "Gecko/20100101 Firefox/158.0", platform="Linux"),
        _ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:158.0) "
            "Gecko/20100101 Firefox/158.0", platform="macOS"),
        # ESR long-tail: support branches linger for a year+
        _ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) "
            "Gecko/20100101 Firefox/140.0", platform="Windows"),
    ],
    "mobile": [
        _ua("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36",
            chromium=True, mobile=True, platform="Android"),
        _ua("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Mobile Safari/537.36",
            chromium=True, mobile=True, platform="Android"),
        _ua("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0.1 "
            "Mobile/15E148 Safari/604.1", mobile=True, platform="iOS"),
        _ua("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 "
            "Mobile/15E148 Safari/604.1", mobile=True, platform="iOS"),
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

# GREASE brand-lists observed in the wild (low-entropy client hints).
_CH_ANTIBRANDS = [
    '"Not_A Brand";v="8"',
    '"Not/A)Brand";v="99"',
    '"Not?A_Brand";v="24"',
    '"Not A(Brand";v="99"',
]
_EDGE_BRAND = "Microsoft Edge"

_SEC_FETCH_NAV = {
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


@dataclass
class Identity:
    id: int
    persona: str
    user_agent: str
    accept_language: str
    pace: str                 # fast | normal | slow
    query_taste: list[str]    # seed words this identity prefers
    chromium: bool = False    # Chromium personas carry client hints; others never
    mobile: bool = False
    platform: str = "Windows"
    gpc: bool = False         # Sec-GPC opt-in, pinned low-rate like real users

    @property
    def pace_multiplier(self) -> float:
        return PACE_MULTIPLIERS[self.pace]


def _client_hints(identity: Identity) -> dict[str, str]:
    """Low-entropy client hints, brand version matched to the UA string.

    Only valid for Chromium personas — Firefox and Safari send none, and a
    Sec-CH-UA on their UAs is an instant non-human tell.
    """
    if not identity.chromium:
        return {}
    major = re.search(r"Chrome/(\d+)", identity.user_agent)
    if major is None:                      # defensive: non-chromium UA string
        return {}
    v = major.group(1)
    notbrand = _CH_ANTIBRANDS[identity.id % len(_CH_ANTIBRANDS)]
    is_edge = _EDGE_BRAND.lower() in identity.user_agent.lower()
    first = f'"{_EDGE_BRAND}";v="{v}"' if is_edge else notbrand
    last = notbrand if is_edge else f'"Google Chrome";v="{v}"'
    brand_list = f'{first}, "Chromium";v="{v}", {last}'
    hints = {
        "Sec-CH-UA": brand_list,
        "Sec-CH-UA-Mobile": "?1" if identity.mobile else "?0",
        "Sec-CH-UA-Platform": f'"{identity.platform}"',
    }
    return hints


def _query_taste(rng: random.Random, wordlist: list[str], size: int = 12) -> list[str]:
    if not wordlist:
        return ["weather", "news", "recipes"]
    return rng.sample(wordlist, min(size, len(wordlist)))


def make_identity(rng: random.Random, ident_id: int, wordlist: list[str]) -> Identity:
    """Build one pinned identity from the shared RNG."""
    persona = rng.choice(PERSONA_CLASSES)
    entry = rng.choice(UA_POOL[persona])
    return Identity(
        id=ident_id,
        persona=persona,
        user_agent=entry["ua"],
        accept_language=rng.choice(LANGUAGE_POOL),
        pace=rng.choice(["fast", "normal", "normal", "slow"]),
        query_taste=_query_taste(rng, wordlist),
        chromium=entry["chromium"],
        mobile=entry["mobile"],
        platform=entry["platform"],
        gpc=rng.random() < 0.06,
    )


def headers_for(identity: Identity) -> dict[str, str]:
    """Headers pinned to this identity (no per-request rotation — that is the point)."""
    headers = {
        "User-Agent": identity.user_agent,
        "Accept-Language": identity.accept_language,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        # navigations carry fetch-metadata on every modern engine; it always
        # looks like a fresh top-level visit.
        "Upgrade-Insecure-Requests": "1",
        **_SEC_FETCH_NAV,
    }
    headers.update(_client_hints(identity))
    if identity.gpc:
        headers["Sec-GPC"] = "1"
    return headers
