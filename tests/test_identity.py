"""Identity and config tests (offline, seeded)."""

import pytest

from noisycricket.config import ConfigError, load_profile
from noisycricket.identity import headers_for, make_identity, UA_POOL
from random import Random

WORDS = ["weather", "news", "books", "travel", "history", "recipes",
         "fitness", "music", "movies", "coffee", "art", "jobs"]


def test_identity_pins_fingerprint():
    rng = Random(42)
    ident = make_identity(rng, 3, WORDS)
    assert ident.user_agent in UA_POOL[ident.persona]
    assert ident.query_taste and set(ident.query_taste) <= set(WORDS)
    # headers must be *the same every time* for one identity — no per-hit rotation
    h1, h2 = headers_for(ident), headers_for(ident)
    assert h1 == h2 and h1["User-Agent"] == ident.user_agent


def test_identities_diverge():
    rng = Random(9)
    personas = {make_identity(rng, i, WORDS).persona for i in range(20)}
    assert len(personas) > 1


def test_search_profile_parses():
    profile = load_profile("profiles/example.yaml")
    assert profile.name == "example"
    assert profile.transports["tor"].socks == "127.0.0.1:9050"
    assert profile.engines["search"].budget_per_day == 120
    # all three engines run on both lanes; the mechanism differs (raw UDP vs DoH)
    assert profile.active_engines("clearnet") == ["search", "crawl", "dns"]
    assert profile.active_engines("tor") == ["search", "crawl", "dns"]


def test_missing_profile_is_error():
    with pytest.raises(ConfigError):
        load_profile("/nonexistent/profile.yaml")


def test_relay_defaults_never_exit():
    p = load_profile("profiles/example.yaml")
    assert p.relay.enabled is False
    assert p.relay.mode == "middle"
