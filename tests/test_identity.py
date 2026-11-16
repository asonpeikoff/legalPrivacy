"""Identity and config tests (offline, seeded)."""

try:
    import pytest
except ImportError:
    class _RaisesContext:
        def __init__(self, exc): self.exc = exc
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None: raise AssertionError(f"DID NOT RAISE {self.exc}")
            return issubclass(exc_type, self.exc)
    import types
    pytest = types.ModuleType("pytest")
    pytest.raises = _RaisesContext

from pathlib import Path

from noisycricket.config import ConfigError, load_profile

PROFILE = str(Path(__file__).resolve().parents[1] / "profiles" / "example.yaml")
from noisycricket.identity import headers_for, make_identity, UA_POOL
from random import Random

WORDS = ["weather", "news", "books", "travel", "history", "recipes",
         "fitness", "music", "movies", "coffee", "art", "jobs"]


def test_identity_pins_fingerprint():
    rng = Random(42)
    ident = make_identity(rng, 3, WORDS)
    assert ident.user_agent in [e["ua"] for e in UA_POOL[ident.persona]]
    assert ident.query_taste and set(ident.query_taste) <= set(WORDS)
    # headers must be *the same every time* for one identity — no per-hit rotation
    h1, h2 = headers_for(ident), headers_for(ident)
    assert h1 == h2 and h1["User-Agent"] == ident.user_agent


def test_identities_diverge():
    rng = Random(9)
    personas = {make_identity(rng, i, WORDS).persona for i in range(20)}
    assert len(personas) > 1


def test_search_profile_parses():
    profile = load_profile(PROFILE)
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
    p = load_profile(PROFILE)
    assert p.relay.enabled is False
    assert p.relay.mode == "middle"


# --- 2026 fingerprint rules ---

from noisycricket.identity import Identity  # noqa: E402
import re  # noqa: E402


def test_no_dnt_header():
    ident = make_identity(Random(3), 7, WORDS)
    h = headers_for(ident)
    assert "DNT" not in h  # DNT was removed from browsers; sending it is a tell


def test_client_hints_only_for_chromium_and_match_version():
    chrome = Identity(id=1, persona="desktop-chrome", user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"),
        accept_language="en-US,en;q=0.9", pace="normal", query_taste=WORDS,
        chromium=True, platform="Windows")
    h = headers_for(chrome)
    assert f'Chromium";v="156"' in h["Sec-CH-UA"]
    assert '"Google Chrome";v="156"' in h["Sec-CH-UA"]
    assert h["Sec-CH-UA-Mobile"] == "?0"
    assert h["Sec-CH-UA-Platform"] == '"Windows"'

    firefox = Identity(id=2, persona="desktop-firefox", user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) "
        "Gecko/20100101 Firefox/158.0"),
        accept_language="en-US,en;q=0.9", pace="normal", query_taste=WORDS)
    assert "Sec-CH-UA" not in headers_for(firefox)

    ios = Identity(id=3, persona="mobile", user_agent=(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0.1 "
        "Mobile/15E148 Safari/604.1"),
        accept_language="en-US,en;q=0.9", pace="normal", query_taste=WORDS,
        mobile=True, platform="iOS")
    assert "Sec-CH-UA" not in headers_for(ios)  # Safari never sends hints

    android = Identity(id=4, persona="mobile", user_agent=(
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/157.0.0.0 Mobile Safari/537.36"),
        accept_language="en-US,en;q=0.9", pace="normal", query_taste=WORDS,
        chromium=True, mobile=True, platform="Android")
    h = headers_for(android)
    assert h["Sec-CH-UA-Mobile"] == "?1" and h["Sec-CH-UA-Platform"] == '"Android"'
    assert re.search(r"Chrome/(\d+)", android.user_agent).group(1) == "157"


def test_fetch_metadata_headers_present():
    ident = make_identity(Random(11), 8, WORDS)
    h = headers_for(ident)
    assert h["Sec-Fetch-Dest"] == "document"
    assert h["Sec-Fetch-Mode"] == "navigate"
    assert h["Upgrade-Insecure-Requests"] == "1"


def test_gpc_is_pinned_per_identity():
    ident = make_identity(Random(21), 9, WORDS)
    assert headers_for(ident).get("Sec-GPC") == headers_for(ident).get("Sec-GPC")


def test_profile_merges_extra_yaml_keys_gracefully(tmp_path=None):
    import tempfile, os
    yaml_content = """
profile:
  name: extra-keys
  engines:
    search:
      budget_per_day: 80
      custom_unsupported_key: "hello"
  transports:
    clearnet:
      extra_junk: 123
"""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(yaml_content)
        path = fh.name
    try:
        p = load_profile(path)
        assert p.name == "extra-keys"
        assert p.engines["search"].budget_per_day == 80
    finally:
        os.remove(path)
