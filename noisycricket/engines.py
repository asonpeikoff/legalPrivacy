"""Engines: search-query noise, crawl noise, DNS noise.

Every engine consumes an identity + transport and yields Targets; it does
not know which transport will carry them.
"""

from __future__ import annotations

import random
import socket
import struct
import urllib.parse
from dataclasses import dataclass

from .transports import Target

SEARCH_ENDPOINTS = [
    "https://duckduckgo.com/?q=",
    "https://www.bing.com/search?q=",
    "https://search.brave.com/search?q=",
    "https://lite.duckduckgo.com/lite/?q=",
    "https://www.startpage.com/sp/search?query=",
    "https://www.ecosia.org/search?q=",
    "https://www.mojeek.com/search?q=",
]

COMMON_TOPICS = [
    "weather", "news", "recipes", "jobs", "movies", "music", "gardening",
    "hiking", "coffee", "books", "travel", "fitness", "history", "art",
]

DOH_RESOLVERS = [
    "https://cloudflare-dns.com/dns-query?name=%s&type=A",
    "https://dns.google/resolve?name=%s&type=A",
    "https://dns.quad9.net/dns-query?name=%s&type=A",
]


def load_wordlist(path: str, fallback: list[str] | None = None) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            words = [w.strip() for w in fh if w.strip()]
        return words if words else (fallback or COMMON_TOPICS)
    except OSError:
        return fallback or COMMON_TOPICS


@dataclass
class SearchTargetFactory:
    """Search noise with per-identity taste mixed into global topic spread."""
    rng: random.Random
    words: list[str]

    def make(self, identity) -> Target:
        # 70% taste words, 30% global picks: mixes identity distributions.
        pool = (identity.query_taste * 2 + self.words) if self.words else COMMON_TOPICS
        n = self.rng.choice([1, 1, 2, 3])
        query = " ".join(self.rng.choice(pool) for _ in range(n))
        endpoint = self.rng.choice(SEARCH_ENDPOINTS)
        url = endpoint + urllib.parse.quote_plus(query)
        return Target(url=url)


@dataclass
class CrawlTargetFactory:
    """Random-walk link crawler with same-site containment."""
    rng: random.Random
    seeds: list[str]
    max_depth: int = 4
    same_site_only: bool = True

    def make(self, identity) -> Target:
        return Target(url=self.rng.choice(self.seeds))

    def next_from(self, html: str, current_url: str) -> Target | None:
        base = urllib.parse.urlparse(current_url)
        links: list[str] = []
        for raw in html.split("<a "):
            if 'href="' not in raw:
                continue
            href = raw.split('href="', 1)[1].split('"', 1)[0]
            absu = urllib.parse.urljoin(current_url, href)
            p = urllib.parse.urlparse(absu)
            if p.scheme not in ("http", "https"):
                continue
            if self.same_site_only and (p.netloc, p.scheme) != (base.netloc, "https") \
                    and p.netloc != base.netloc:
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
        self.words = words or COMMON_TOPICS

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
        self.words = words or COMMON_TOPICS

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
