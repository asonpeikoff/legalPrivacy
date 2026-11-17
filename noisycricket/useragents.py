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

import datetime as dt
import hashlib
import random
import time

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


def _entry(ua: str, platform: str, *, persona: str, chromium: bool = False, mobile: bool = False,
           weight: float = 1.0, release: str = "") -> dict:
    return {"ua": ua, "platform": platform, "persona": persona, "chromium": chromium,
            "mobile": mobile, "weight": weight, "release": release, "id": ua_id(ua)}


def ua_id(ua: str) -> str:
    """Short, stable id for a user agent string (used for pinning)."""
    return hashlib.sha1(ua.encode("utf-8")).hexdigest()[:10]


def _chrome(major: int) -> str:
    return f"Chrome/{major}.0.0.0"


def build_pool() -> dict[str, list[dict]]:
    pool: dict[str, list[dict]] = {p: [] for p in PERSONA_WEIGHTS}

    for major, released, w in ua_catalog.CHROME:
        for os_tok, plat in ((_WIN, "Windows"), (_MAC, "macOS"), (_LINUX, "Linux")):
            pool["desktop-chrome"].append(_entry(
                f"Mozilla/5.0 ({os_tok}) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"{_chrome(major)} Safari/537.36", plat, persona="desktop-chrome", chromium=True,
                weight=w / 3, release=released))
        pool["mobile-chrome"].append(_entry(
            f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"{_chrome(major)} Mobile Safari/537.36", "Android", persona="mobile-chrome",
            chromium=True, mobile=True,
            weight=w, release=released))

    for major, released, w in ua_catalog.CHROMIUM:
        pool["desktop-chromium"].append(_entry(
            f"Mozilla/5.0 ({_LINUX}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chromium/{major}.0.0.0 {_chrome(major)} Safari/537.36", "Linux",
            persona="desktop-chromium", chromium=True,
            weight=w, release=released))

    for major, released, w in ua_catalog.FIREFOX + ua_catalog.FIREFOX_ESR:
        for os_tok, plat in (("Windows NT 10.0; Win64; x64", "Windows"),
                             ("Macintosh; Intel Mac OS X 10.15", "macOS"),
                             ("X11; Linux x86_64", "Linux")):
            pool["desktop-firefox"].append(_entry(
                f"Mozilla/5.0 ({os_tok}; rv:{major}.0) Gecko/20100101 Firefox/{major}.0",
                plat, persona="desktop-firefox", weight=w / 3, release=released))

    for ua, plat in TOOL_AGENTS:
        pool["tool"].append(_entry(ua, plat, persona="tool", weight=1.0))
    return pool


def latest_firefox_esr() -> int:
    return max(m for m, _, _ in ua_catalog.FIREFOX_ESR)


def latest_firefox() -> int:
    return max(m for m, _, _ in ua_catalog.FIREFOX)


def library() -> list[dict]:
    """Every browser and tool user agent, flattened, with its share of picks (percent)."""
    pool = build_pool()
    total = sum(PERSONA_WEIGHTS[p] for p in pool)
    rows = []
    for persona, entries in pool.items():
        wsum = sum(e["weight"] for e in entries) or 1.0
        for e in entries:
            rows.append({**e, "group": persona,
                         "share": round(100 * PERSONA_WEIGHTS[persona] / total * e["weight"] / wsum, 2)})
    return rows


def sample_entry(rng: random.Random) -> dict:
    """One user agent drawn the same way new identities are drawn."""
    pool = build_pool()
    persona = rng.choices(list(PERSONA_WEIGHTS), weights=list(PERSONA_WEIGHTS.values()))[0]
    entries = pool[persona]
    return rng.choices(entries, weights=[e["weight"] for e in entries])[0]


def entry_from_ua(ua: str) -> dict:
    """Describe a user agent string (e.g. your own browser's) in the same shape as the pool."""
    ua = ua.strip()
    if "Chromium/" in ua:
        persona = "desktop-chromium"
    elif "Chrome/" in ua:
        persona = "desktop-chrome"
    elif "Firefox/" in ua:
        persona = "desktop-firefox"
    else:
        persona = "own"
    chromium = "Chrome/" in ua or "Chromium/" in ua
    mobile = "Mobile" in ua or "iPhone" in ua or "iPad" in ua
    if "Android" in ua:
        plat = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        plat = "iOS"
    elif "Windows" in ua:
        plat = "Windows"
    elif "Macintosh" in ua:
        plat = "macOS"
    else:
        plat = "Linux"
    return _entry(ua, plat, persona=persona, chromium=chromium, mobile=mobile, weight=1.0)


class UaChooser:
    """Decides which user agent new noise identities use.

    mode "pin": always the pinned one (your own browser, or one library entry).
    mode "rotate": a random library entry, re-drawn every hour or every day.
    The draw is cached for the whole period, so one period looks like one browser.
    """

    def __init__(self, rng: random.Random, cfg, clock=time.time):
        self.rng, self.cfg, self.clock = rng, cfg, clock
        self._key = None
        self._entry: dict | None = None

    def pinned_entry(self) -> dict | None:
        pin = (self.cfg.ua_pin or "").strip()
        if pin == "own":
            return entry_from_ua(self.cfg.own_ua) if self.cfg.own_ua.strip() else None
        for e in library():
            if e["id"] == pin:
                return {k: v for k, v in e.items() if k not in ("group", "share")}
        return None

    def _period_key(self) -> str:
        t = time.localtime(self.clock())
        if self.cfg.rotate_every == "day":
            return f"{t.tm_year}-{t.tm_yday}"
        return f"{t.tm_year}-{t.tm_yday}-{t.tm_hour}"

    def choose(self) -> dict:
        if self.cfg.ua_mode == "pin":
            e = self.pinned_entry()
            if e is not None:
                return e
        key = self._period_key()
        if key != self._key or self._entry is None:
            self._key = key
            self._entry = sample_entry(self.rng)
        return self._entry


def detect_own_ua(timeout: float = 45) -> dict:
    """Read this computer's installed browser user agent by starting it briefly (headless).

    Returns {"ua": ...} or {"error": ...}. Needs the playwright package and a
    Chromium-family browser; otherwise paste your UA by hand (chrome://version).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "Playwright is not installed here; paste your browser's user agent instead."}
    from .browser import find_browser
    exe = find_browser()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=exe, timeout=timeout * 1000)
            try:
                page = browser.new_page()
                ua = page.evaluate("navigator.userAgent")
            finally:
                browser.close()
    except Exception as exc:
        return {"error": f"Could not start your browser to read its user agent: {exc}"}
    return {"ua": ua.replace("HeadlessChrome/", "Chrome/")}
