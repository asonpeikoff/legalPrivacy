"""Onion (darknet) noise: v3 .onion address validation, seeds, and target factory.

Seeds are public, well-known sites that run official onion mirrors. Every
address is checked offline with the v3 checksum (sha3-256) so a mistyped or
corrupted address can never be dispatched. Users can add their own seeds.
"""
from __future__ import annotations

import base64
import hashlib
import random
import re
import urllib.parse
from dataclasses import dataclass, field

from .engines import _LinkExtractor
from .transports import Target

# (label, url) — official onion mirrors of public sites.
BUILTIN_ONION_SEEDS: list[tuple[str, str]] = [
    ("DuckDuckGo", "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/"),
    ("Tor Project", "http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/"),
    ("The New York Times", "https://nytimesn7cgmftshazwhfgzm37qxb44r64ytbb2dj3x62d2lljsciiyd.onion/"),
    ("BBC", "https://bbcweb3hytmzhn5d532owbu6oqadra5z3ar726vq5kgwwn6aucdccrad.onion/"),
    ("Debian", "http://2s4yqjx5ul6okpp3f2gaunr2syex5jgbfpfvhxxbbjwnrsvbk5v3qbid.onion/"),
    ("Proton", "https://protonmailrmez3lotccipshtkleegetolb73fuirgj7r4o4vfu7ozyd.onion/"),
]

_ONION_RE = re.compile(r"^[a-z2-7]{56}$")


def is_valid_v3_onion(host: str) -> bool:
    """True iff `host` is a well-formed v3 onion address with a correct checksum."""
    h = host.strip().lower().rstrip(".")
    if h.endswith(".onion"):
        h = h[: -len(".onion")]
    h = h.rsplit(".", 1)[-1]          # ignore subdomains
    if not _ONION_RE.match(h):
        return False
    try:
        raw = base64.b32decode(h.upper())
    except Exception:
        return False
    pubkey, checksum, version = raw[:32], raw[32:34], raw[34:]
    if version != b"\x03":
        return False
    return hashlib.sha3_256(b".onion checksum" + pubkey + version).digest()[:2] == checksum


def is_onion_url(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return False
    return host.endswith(".onion") and is_valid_v3_onion(host)


def clean_seed_list(urls: list[str]) -> tuple[list[str], list[str]]:
    """Split user-supplied seeds into (valid, rejected)."""
    good, bad = [], []
    for raw in urls:
        u = raw.strip()
        if not u or u.startswith("#"):
            continue
        if "://" not in u:
            u = "http://" + u
        (good if is_onion_url(u) else bad).append(u)
    return good, bad


@dataclass
class OnionTargetFactory:
    """Random walk over onion sites: start at a seed, follow links, restart often.

    `follow_offsite` lets the walk hop to other valid onion services it finds
    (organic discovery); off-site non-onion links are always ignored so this
    engine can never leak a clearnet request.
    """
    rng: random.Random
    seeds: list[str] = field(default_factory=lambda: [u for _, u in BUILTIN_ONION_SEEDS])
    max_depth: int = 3
    follow_offsite: bool = True
    timeout: float = 60.0

    def __post_init__(self) -> None:
        good, _ = clean_seed_list(self.seeds)
        self.seeds = good or [u for _, u in BUILTIN_ONION_SEEDS]

    def make(self, identity=None) -> Target:
        return Target(url=self.rng.choice(self.seeds), timeout=self.timeout)

    def next_from(self, html: str, current_url: str) -> Target | None:
        parser = _LinkExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass
        base_host = (urllib.parse.urlparse(current_url).hostname or "").lower()
        links = []
        for href in parser.links:
            absu = urllib.parse.urljoin(current_url, href)
            p = urllib.parse.urlparse(absu)
            if p.scheme not in ("http", "https") or not is_onion_url(absu):
                continue
            if not self.follow_offsite and (p.hostname or "").lower() != base_host:
                continue
            links.append(absu)
        return Target(url=self.rng.choice(links), timeout=self.timeout) if links else None
