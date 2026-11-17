"""Traffic mix patterns, phone emulation defaults, bookmarks import, My List weights."""
import json
import os
import random
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from noisycricket.browser import BrowserTransport  # noqa: E402
from noisycricket.config import ConfigError, Profile, apply_overrides  # noqa: E402
from noisycricket.domains import DomainPool  # noqa: E402
from noisycricket.history import _chromium_bookmark_urls, sites_from_bookmarks_file  # noqa: E402
from noisycricket.identity import make_identity  # noqa: E402
from noisycricket.rate import RatePlanner  # noqa: E402
from noisycricket.scheduler import Runner, build_lanes  # noqa: E402
from noisycricket.transports import ClearnetTransport, HitResult  # noqa: E402
from noisycricket.useragents import sample_entry  # noqa: E402


# ── phones ──────────────────────────────────────────────────────────────────
def test_default_phone_is_iphone_13_with_safari_and_about_a_third_of_identities():
    rng = random.Random(4)
    es = [sample_entry(rng) for _ in range(3000)]
    phones = [e for e in es if e["mobile"]]
    assert 0.30 < len(phones) / len(es) < 0.40
    assert all(e["device"] == "iPhone 13" and "iPhone" in e["ua"] and not e["chromium"] for e in phones)
    android = [sample_entry(rng, 1.0, "Pixel 7") for _ in range(50)]
    assert all(e["device"] == "Pixel 7" and "Android" in e["ua"] for e in android)
    mixed = {sample_entry(rng, 1.0, "mix")["device"] for _ in range(400)}
    assert "iPhone 13" in mixed and len(mixed) >= 3


class _CDP:
    def __init__(self, log): self.log = log
    def send(self, method, params): self.log.append((method, params))


class _Ctx:
    def __init__(self, kw, log): self.kw, self.log = kw, log
    def new_page(self): return object()
    def new_cdp_session(self, page): return _CDP(self.log)
    def close(self): pass


class _Browser:
    def __init__(self): self.contexts, self.cdp = [], []
    def new_context(self, **kw):
        self.contexts.append(kw)
        return _Ctx(kw, self.cdp)


def _transport(headless=True, exe="/usr/bin/google-chrome"):
    t = BrowserTransport(headless=headless, executable=exe)
    t._browser, t._major = _Browser(), "158"
    return t


def test_iphone_identity_gets_iphone_screen_touch_and_a_clean_safari_override():
    ident = make_identity(random.Random(1), 7, ["w"], entry=sample_entry(random.Random(9), 1.0, "iPhone 13"))
    t = _transport()
    t._session(ident)
    ctx = t._browser.contexts[0]
    assert ctx["viewport"] == {"width": 390, "height": 664} and ctx["device_scale_factor"] == 3.0
    assert ctx["is_mobile"] and ctx["has_touch"] and "user_agent" not in ctx    # no leaky Playwright override
    method, params = t._browser.cdp[0]
    assert params["platform"] == "iPhone" and "userAgentMetadata" not in params   # Safari: no client hints
    assert params["userAgent"] == ident.user_agent


def test_headless_desktop_does_not_say_headless():
    ident = make_identity(random.Random(1), 3, ["w"], entry=sample_entry(random.Random(2), 0.0))
    t = _transport(headless=True)
    t._session(ident)
    _, params = t._browser.cdp[0]
    assert "Headless" not in params["userAgent"] and "Chrome/158.0.0.0" in params["userAgent"]
    assert [b["brand"] for b in params["userAgentMetadata"]["brands"]][-1] == "Google Chrome"
    visible = _transport(headless=False)
    visible._session(ident)
    assert visible._browser.cdp == []          # a visible window already reports itself honestly


# ── traffic mix ─────────────────────────────────────────────────────────────
def _runner(pattern, plain=0.0):
    p = Profile(name="t")
    apply_overrides(p, {"traffic": {"pattern": pattern, "browser": 70, "plain": plain, "onion": 0},
                        "rate": {"mode": "steady", "level": 5}})
    lanes = build_lanes(p, ["w"], clearnet_transport=ClearnetTransport(), plain_transport=ClearnetTransport() if plain else None)
    return Runner(profile=p, lanes=lanes, wordlist=["w"], sleep_fn=lambda s: None,
                  probe_fn=lambda *a: HitResult(200, ""), rate=RatePlanner(random.Random(2), p.rate), mix=True)


def _lane_share(r, lane):
    return sum(v for (ln, _), v in r.engine_shares().items() if ln == lane)


def test_blend_keeps_the_lane_shares_and_plain_lane_has_no_dns():
    r = _runner("blend", plain=30)
    r.plan_hour(12)
    assert abs(_lane_share(r, "clearnet-plain") - 0.30) < 0.01
    assert "dns" not in next(l for l in r.lanes if l.name == "clearnet-plain").engines


def test_drift_moves_shares_and_alternate_runs_one_kind_at_a_time():
    r = _runner("drift", plain=30)
    seen = []
    for h in range(24):
        r.plan_hour(h)
        seen.append(_lane_share(r, "clearnet-plain"))
    assert max(seen) - min(seen) > 0.1                     # it wanders
    a = _runner("alternate", plain=30)
    dominant = []
    for h in range(24):
        a.plan_hour(h)
        dominant.append(max(_lane_share(a, "clearnet"), _lane_share(a, "clearnet-plain")))
    assert min(dominant) > 0.85                            # one lane at a time


def test_bad_traffic_settings_are_rejected():
    for bad in ({"pattern": "zigzag"}, {"device": "Nokia 3310"}, {"mobile_share": 2}):
        try:
            apply_overrides(Profile(name="t"), {"traffic": bad})
            raise AssertionError(f"accepted {bad}")
        except ConfigError:
            pass


# ── bookmarks & weights ─────────────────────────────────────────────────────
def test_bookmarks_from_chrome_json_and_an_export_file():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "Bookmarks")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"roots": {"bookmark_bar": {"children": [
            {"type": "url", "url": "https://www.example.com/a"},
            {"type": "folder", "children": [{"type": "url", "url": "https://news.example.org/"}]}]}}}, fh)
    assert sorted(_chromium_bookmark_urls(path)) == ["https://news.example.org/", "https://www.example.com/a"]
    html = '<DL><DT><A HREF="https://www.example.com/x">Ex</A><DT><A HREF="https://pornhub.com/">no</A></DL>'
    res = sites_from_bookmarks_file(html)
    assert res["sites"] == [["example.com", 1]] and res["filtered"] == 1


def test_firefox_bookmarks_query_matches_schema():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "places.sqlite")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT)")
    con.execute("CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER)")
    con.execute("INSERT INTO moz_places VALUES (1, 'https://example.net/')")
    con.execute("INSERT INTO moz_bookmarks (type, fk) VALUES (1, 1)")
    con.commit()
    con.close()
    from noisycricket.history import _read_copy
    rows = _read_copy(path, "SELECT p.url FROM moz_bookmarks b JOIN moz_places p ON p.id = b.fk WHERE b.type = 1", ())
    assert rows == [("https://example.net/",)]


def test_how_often_weights_steer_my_list():
    mine = ["often-example.com", "rare-example.org"]
    pool = DomainPool(random.Random(3), None, my_sites=mine, random_share=0.0,
                      my_weights={"often-example.com": 10, "rare-example.org": 1})
    picks = [pool._mine() for _ in range(2000)]
    assert picks.count("often-example.com") > 7 * picks.count("rare-example.org")
