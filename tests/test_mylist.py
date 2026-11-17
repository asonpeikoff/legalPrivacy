"""My List: reading browser history (fake databases), recording window cut-off, mixing."""
import os
import random
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from noisycricket.config import ConfigError, Profile, apply_overrides  # noqa: E402
from noisycricket.domains import DomainPool  # noqa: E402
from noisycricket.history import site_of, top_sites  # noqa: E402

WEBKIT = 11_644_473_600


def _chrome_db(path, visits):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT)")
    con.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    for i, (url, t) in enumerate(visits, 1):
        con.execute("INSERT INTO urls VALUES (?, ?)", (i, url))
        con.execute("INSERT INTO visits (url, visit_time) VALUES (?, ?)", (i, int((t + WEBKIT) * 1e6)))
    con.commit()
    con.close()


def _firefox_db(path, visits):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT)")
    con.execute("CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER)")
    for i, (url, t) in enumerate(visits, 1):
        con.execute("INSERT INTO moz_places VALUES (?, ?)", (i, url))
        con.execute("INSERT INTO moz_historyvisits (place_id, visit_date) VALUES (?, ?)", (i, int(t * 1e6)))
    con.commit()
    con.close()


def test_site_names_only_for_real_web_sites():
    assert site_of("https://www.example.com/a?b=c") == "example.com"
    assert site_of("http://192.168.1.1/") is None
    assert site_of("chrome://settings") is None
    assert site_of("http://router.local/") is None
    assert site_of("http://intranet/") is None


def test_top_sites_counts_across_browsers_inside_the_window_only():
    d = tempfile.mkdtemp()
    now = time.time()
    chrome, ff = os.path.join(d, "History"), os.path.join(d, "places.sqlite")
    _chrome_db(chrome, [("https://news.example.com/1", now - 100), ("https://news.example.com/2", now - 50),
                        ("https://old.example.org/", now - 10 * 86400)])
    _firefox_db(ff, [("https://www.shop.example.net/x", now - 60), ("https://pornhub.com/", now - 30)])
    files = [{"browser": "Chrome", "kind": "chromium", "path": chrome, "profile": "Default"},
             {"browser": "Firefox", "kind": "firefox", "path": ff, "profile": "x"}]
    res = top_sites(now - 3600, now, files=files)
    assert res["sites"][0] == ["news.example.com", 2]
    names = [s for s, _ in res["sites"]]
    assert "shop.example.net" in names and "old.example.org" not in names     # outside the window
    assert "pornhub.com" not in names and res["filtered"] == 1                # safety filter
    assert not res["errors"]


def test_my_list_mixes_in_the_chosen_share_of_other_sites():
    mine = ["alpha-example.com", "beta-example.org"]
    pool = DomainPool(random.Random(1), None, my_sites=mine, random_share=0.3)
    picks = [pool.pick(browsable=True) for _ in range(2000)]
    share_mine = sum(p in mine for p in picks) / len(picks)
    assert 0.62 < share_mine < 0.78
    off = DomainPool(random.Random(1), None, my_sites=[], random_share=0.3)
    assert not any(off.pick() in mine for _ in range(200))


def test_my_list_settings_clean_up_and_validate():
    p = Profile(name="t")
    apply_overrides(p, {"mylist": {"enabled": True, "sites": ["https://www.Example.com/page", "example.com", "news.org"],
                                   "random_share": 0.4}})
    assert p.mylist.sites == ["example.com", "news.org"] and p.mylist.enabled and p.mylist.random_share == 0.4
    try:
        apply_overrides(p, {"mylist": {"random_share": 2}})
        raise AssertionError("expected ConfigError")
    except ConfigError:
        pass
