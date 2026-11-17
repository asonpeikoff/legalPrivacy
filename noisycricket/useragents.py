"""The user-agent pool: browsers from the last two years, plus a few non-browser clients.

Nothing here is a raw string copied from one machine. Every browser UA is built from
a template and a release (Chrome, Chromium and Firefox majors, from
ua_catalog.py, which tools/build_ua_catalog.py regenerates from the official release
calendars). Commonness is modelled from release age, so the newest two or three
majors of each browser are the ones most often chosen.

Non-browser clients are included on purpose, at low weight, so the traffic is not
only browsers. They are only used on the plain-HTTP lane, never with a real browser.
"""
from __future__ import annotations

from . import ua_catalog

# How often each persona is picked (sums to 1).
PERSONA_WEIGHTS = {
    "desktop-chrome": 0.50,
    "desktop-firefox": 0.28,
    "desktop-chromium": 0.05,
    "mobile-chrome": 0.15,
    "tool": 0.02,
}

_WIN = "Windows NT 10.0; Win64; x64"
_MAC = "Macintosh; Intel Mac OS X 10_15_7"
_LINUX = "X11; Linux x86_64"

# Non-browser clients. These are real, default User-Agent strings of command-line
# tools. Their versions are pinned here; refresh them with the catalog if they age.
TOOL_AGENTS = [
    ("curl/8.7.1", "curl"),
    ("Wget/1.21.4", "curl"),
    ("Go-http-client/1.1", "curl"),
]


def _entry(ua: str, platform: str, *, chromium: bool = False, mobile: bool = False,
           weight: float = 1.0, release: str = "") -> dict:
    return {"ua": ua, "platform": platform, "chromium": chromium, "mobile": mobile,
            "weight": weight, "release": release}


def _chrome(major: int) -> str:
    return f"Chrome/{major}.0.0.0"


def build_pool() -> dict[str, list[dict]]:
    pool: dict[str, list[dict]] = {p: [] for p in PERSONA_WEIGHTS}

    for major, released, w in ua_catalog.CHROME:
        for os_tok, plat in ((_WIN, "Windows"), (_MAC, "macOS"), (_LINUX, "Linux")):
            pool["desktop-chrome"].append(_entry(
                f"Mozilla/5.0 ({os_tok}) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"{_chrome(major)} Safari/537.36", plat, chromium=True,
                weight=w / 3, release=released))
        pool["mobile-chrome"].append(_entry(
            f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"{_chrome(major)} Mobile Safari/537.36", "Android", chromium=True, mobile=True,
            weight=w, release=released))

    for major, released, w in ua_catalog.CHROMIUM:
        pool["desktop-chromium"].append(_entry(
            f"Mozilla/5.0 ({_LINUX}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chromium/{major}.0.0.0 {_chrome(major)} Safari/537.36", "Linux", chromium=True,
            weight=w, release=released))

    for major, released, w in ua_catalog.FIREFOX + ua_catalog.FIREFOX_ESR:
        for os_tok, plat in (("Windows NT 10.0; Win64; x64", "Windows"),
                             ("Macintosh; Intel Mac OS X 10.15", "macOS"),
                             ("X11; Linux x86_64", "Linux")):
            pool["desktop-firefox"].append(_entry(
                f"Mozilla/5.0 ({os_tok}; rv:{major}.0) Gecko/20100101 Firefox/{major}.0",
                plat, weight=w / 3, release=released))

    for ua, plat in TOOL_AGENTS:
        pool["tool"].append(_entry(ua, plat, weight=1.0))
    return pool


def latest_firefox_esr() -> int:
    return max(m for m, _, _ in ua_catalog.FIREFOX_ESR)


def latest_firefox() -> int:
    return max(m for m, _, _ in ua_catalog.FIREFOX)
