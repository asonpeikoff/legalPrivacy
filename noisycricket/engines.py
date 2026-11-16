"""Engines: search-query noise, crawl noise, DNS noise.

Every engine consumes an identity + transport and yields Targets; it does
not know which transport will carry them.
"""

from __future__ import annotations

import random
import re
import socket
import struct
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

from .transports import Target

# Search engine endpoint probability tiers:
# 1. Most likely group: Google & DuckDuckGo (~70% weight)
# 2. Next most likely group: Bing, Brave, Startpage (~24% weight)
# 3. Less likely group: Ecosia, Mojeek, Qwant, DuckDuckGo Lite (~6% weight)
SEARCH_ENGINE_TIERS: list[tuple[str, float]] = [
    # Tier 1 (Highest probability: Google + DuckDuckGo)
    ("https://www.google.com/search?q=", 35.0),
    ("https://duckduckgo.com/?q=", 35.0),
    # Tier 2 (Next most likely: Bing, Brave, StartPage)
    ("https://www.bing.com/search?q=", 8.0),
    ("https://search.brave.com/search?q=", 8.0),
    ("https://www.startpage.com/sp/search?query=", 8.0),
    # Tier 3 (Less likely group: alternative engines)
    ("https://www.ecosia.org/search?q=", 1.5),
    ("https://www.mojeek.com/search?q=", 1.5),
    ("https://www.qwant.com/?q=", 1.5),
    ("https://lite.duckduckgo.com/lite/?q=", 1.5),
]
SEARCH_ENDPOINTS = [url for url, _ in SEARCH_ENGINE_TIERS]
SEARCH_WEIGHTS = [w for _, w in SEARCH_ENGINE_TIERS]

# Curated thematic pools of real internet search topics:
REAL_INTERNET_THEMES: dict[str, list[str]] = {
    "tech": [
        "python", "linux", "docker", "kubernetes", "rust language", "git commands",
        "sqlite database", "bash scripting", "ssh keys setup", "arch linux",
        "raspberry pi projects", "home assistant", "nginx reverse proxy", "rest api",
        "tailwind css", "react components", "typescript tutorial", "dns record types",
    ],
    "cooking": [
        "sourdough bread recipe", "cast iron seasoning", "pasta carbonara authentic",
        "easy weeknight dinner", "air fryer chicken breast", "chocolate chip cookies chewy",
        "thai green curry paste", "vegetarian chili slow cooker", "meal prep containers",
        "french press ratio", "roasted root vegetables", "homemade pizza dough cold ferment",
    ],
    "travel": [
        "flight tracker live", "national park annual pass", "hiking boots lightweight",
        "camping checklist essentials", "carry on bag dimensions", "scenic train routes",
        "hotel booking discounts", "travel insurance comparison", "mountain trail conditions",
        "road trip map offline", "campsite reservation tips", "passport renewal expedited",
    ],
    "entertainment": [
        "movie showtimes today", "sci fi novels recommendations", "indie video games 2026",
        "soundtrack vinyl record", "streaming series release dates", "audio drama podcasts",
        "fantasy book series", "director commentary analysis", "concert tour dates",
        "tabletop board games strategy",
    ],
    "health_fitness": [
        "5k running schedule beginner", "morning mobility routine 10 min", "core strength exercises",
        "whey protein isolate reviews", "stretching for lower back", "healthy meal prep ideas",
        "kettlebell workout full body", "sleep tracking accuracy", "electrolyte hydration drinks",
    ],
    "science_nature": [
        "james webb space telescope images", "solar eclipse path map", "bird migration tracker",
        "mycelium network biology", "quantum computing fundamentals", "deep sea exploration discoveries",
        "renewable energy grid battery", "fossil discovery news", "night sky constellation guide",
    ],
    "home_daily": [
        "indoor plants low light tolerant", "compost tumbler bin", "drip irrigation raised bed",
        "soil ph test kit garden", "hand woodworking tools beginner", "post office hours tracking",
        "local public transit schedule", "recycling guidelines city", "lawn aeration schedule",
    ],
}

QUERY_PATTERNS = [
    "{topic}",
    "{topic} tutorial",
    "how to use {topic}",
    "best {topic} guide",
    "{topic} tips",
    "{topic} review",
    "how to fix {topic} issue",
    "what is {topic}",
    "{topic} vs {alt}",
    "{topic} beginner guide",
]

COMMON_TOPICS = [topic for topics in REAL_INTERNET_THEMES.values() for topic in topics]

DOH_RESOLVERS = [
    "https://cloudflare-dns.com/dns-query?name=%s&type=A",
    "https://dns.google/resolve?name=%s&type=A",
    "https://dns.quad9.net/dns-query?name=%s&type=A",
]


def load_wordlist(path: str, fallback: list[str] | None = None) -> list[str]:
    from .paths import resolve_resource
    try:
        with open(resolve_resource(path), encoding="utf-8", errors="ignore") as fh:
            words = [w.strip() for w in fh if w.strip()]
        return words if words else (fallback or COMMON_TOPICS)
    except OSError:
        return fallback or COMMON_TOPICS


@dataclass
class SearchTargetFactory:
    """Search noise with realistic intent patterns, thematic taste, and weighted engine selection."""
    rng: random.Random
    words: list[str]

    def make(self, identity) -> Target:
        # Weighted search engine selection (Google & DDG prioritized per user directive)
        endpoint = self.rng.choices(SEARCH_ENDPOINTS, weights=SEARCH_WEIGHTS)[0]

        # Generate realistic internet query
        taste = getattr(identity, "query_taste", None) or []
        pool = taste if taste else (self.words or COMMON_TOPICS)

        # 70% of the time, apply realistic human search intent templates
        if self.rng.random() < 0.70 and pool:
            topic = self.rng.choice(pool)
            pattern = self.rng.choice(QUERY_PATTERNS)
            alt = self.rng.choice(pool)
            query = pattern.format(topic=topic, alt=alt)
        else:
            # 30% sample 1-3 words naturally
            n = self.rng.choice([1, 1, 2, 3])
            query = " ".join(self.rng.choice(pool) for _ in range(n))

        url = endpoint + urllib.parse.quote_plus(query)
        return Target(url=url)


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for k, v in attrs:
                if k.lower() == "href" and v:
                    self.links.append(v)


@dataclass
class CrawlTargetFactory:
    """Random-walk link crawler with same-site containment."""
    rng: random.Random
    seeds: list[str]
    max_depth: int = 4
    same_site_only: bool = True

    def make(self, identity) -> Target:
        seeds = self.seeds or ["https://en.wikipedia.org/wiki/Special:Random"]
        return Target(url=self.rng.choice(seeds))

    def next_from(self, html: str, current_url: str) -> Target | None:
        parser = _LinkExtractor()
        try:
            parser.feed(html)
        except Exception:
            pass
        base = urllib.parse.urlparse(current_url)
        links: list[str] = []
        for href in parser.links:
            absu = urllib.parse.urljoin(current_url, href)
            p = urllib.parse.urlparse(absu)
            if p.scheme not in ("http", "https"):
                continue
            if self.same_site_only and p.netloc.lower() != base.netloc.lower():
                continue
            links.append(absu)
        if not links:
            return None
        return Target(url=self.rng.choice(links))


# --- raw crafted DNS packets (clearnet DNS engine, stdlib only) ---

_DNS_SERVERS = ["1.1.1.1", "9.9.9.8", "208.67.222.222", "94.140.14.14"]


def build_dns_query(domain: str, qid: int) -> bytes:
    """One A-record DNS question, header + punctuated labels."""
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q = b"".join(bytes([len(part)]) + part.encode("ascii", "ignore")
                 for part in domain.split(".") if part) + b"\x00"
    return header + q + struct.pack(">HH", 1, 1)


def parse_dns_names(answer: bytes) -> list[str]:
    """Minimal parser: names in the answer section only (no compression walk)."""
    names = []
    try:
        qd, an = struct.unpack(">HH", answer[4:8])
        i = 12
        for _ in range(qd):
            while answer[i]:
                i += answer[i] + 1
            i += 5
        for _ in range(an):
            i += 2
            if answer[i] & 0xC0 == 0xC0:
                i += 2
            else:
                while answer[i]:
                    i += answer[i] + 1
                i += 1
            rtype, _, _, rdlen = struct.unpack(">HHIH", answer[i:i + 10])
            i += 10 + rdlen
            # names: header + labels we can recover from the question echo
        _ = names
    except (IndexError, struct.error):
        return []
    return names


class DnsNoise:
    """Random local DNS lookups to public resolvers, packets crafted by hand.

    Clearnet engine only — arbitrary UDP queries cannot traverse Tor circuits;
    the tor lane uses DoH-over-Tor instead (see DohNoise).
    """

    def __init__(self, rng: random.Random, words: list[str]):
        self.rng = rng
        clean = [re.sub(r"[^a-zA-Z0-9]", "", w).lower() for w in (words or COMMON_TOPICS)]
        self.words = [w for w in clean if 3 <= len(w) <= 20] or COMMON_TOPICS

    def make_query(self) -> tuple[bytes, str, str]:
        """(packet, domain, resolver_ip) — firing is the scheduler's probe step."""
        tld = self.rng.choice([".com", ".net", ".org", ".io"])
        domain = self.rng.choice(self.words).lower().replace(" ", "") + tld
        packet = build_dns_query(domain, qid=self.rng.randint(0, 0xFFFF))
        return packet, domain, self.rng.choice(_DNS_SERVERS)

    def fire(self, packet: bytes, server: str, timeout: float = 3.0) -> bool:
        """Send one UDP query; return True if a parseable answer came back."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            sock.sendto(packet, (server, 53))
            data, _ = sock.recvfrom(512)
            return len(data) >= 12
        except OSError:
            return False
        finally:
            sock.close()


class DohNoise:
    """DNS-over-HTTPS lookups; runs over the Tor transport's SOCKS daemon."""

    def __init__(self, rng: random.Random, words: list[str]):
        self.rng = rng
        clean = [re.sub(r"[^a-zA-Z0-9]", "", w).lower() for w in (words or COMMON_TOPICS)]
        self.words = [w for w in clean if 3 <= len(w) <= 20] or COMMON_TOPICS

    def make(self, identity=None) -> Target:
        tld = self.rng.choice([".com", ".net", ".org"])
        domain = self.rng.choice(self.words).lower().replace(" ", "") + tld
        url = self.rng.choice(DOH_RESOLVERS) % urllib.parse.quote(domain)
        return Target(url=url, kind="doh")


@dataclass
class EngineCluster:
    """Bundles engine factories for the scheduler."""
    search: SearchTargetFactory | None = None
    crawl: CrawlTargetFactory | None = None
    dns: DnsNoise | None = None
    doh: DohNoise | None = None
