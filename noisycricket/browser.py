"""Real-browser transport: a genuine Chrome/Edge/Chromium driven like a person.

Why: a plain HTTP client fetches HTML only — no JavaScript, images or CSS, no
scrolling, and a TLS/HTTP2 fingerprint that is not a browser's no matter what
User-Agent it claims. Sites (and anyone watching traffic) can tell. Driving a
real browser gives the real network fingerprint plus everything HumanDriver does:
scrolling, reading pauses, mouse movement, clicking/tapping results, the
occasional file download (fetched and discarded, never written to disk).

Desktop personas use the browser's own, honest User-Agent (no spoofing, so the
header, JS navigator and TLS all agree). Mobile personas (~25%) get touch
emulation, a phone viewport and an Android UA built from the browser's real
major version, with matching client-hint metadata.

Optional: needs the `playwright` Python package and a Chromium-family browser.
If either is missing, noisyCricket falls back to the plain HTTP transport.
"""
from __future__ import annotations

import glob
import os
import platform
import random
import shutil
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable

from .humanize import HumanDriver, lognormal_clipped
from .transports import ClearnetTransport, HitResult, Target, Transport

DESKTOP_VIEWPORTS = [((1920, 1080), 30), ((1536, 864), 14), ((1366, 768), 12), ((1440, 900), 9),
                     ((1280, 720), 8), ((2560, 1440), 7), ((1600, 900), 6), ((1280, 800), 5)]
MOBILE_VIEWPORTS = [((390, 844), 3.0, 30), ((412, 915), 2.625, 25), ((393, 873), 2.75, 20), ((360, 800), 3.0, 15)]
MOBILE_SHARE = 0.25
DOWNLOAD_EXT = (".pdf", ".zip", ".epub", ".csv", ".xlsx", ".docx", ".mp3")
MAX_DOWNLOAD = 8_000_000


def find_browser() -> str | None:
    """Locate a Chromium-family browser executable on this machine."""
    env = os.environ.get("CRICKET_BROWSER")
    if env and Path(env).exists():
        return env
    cands: list[str] = []
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            cands += [rf"{base}\Google\Chrome\Application\chrome.exe",
                      rf"{base}\Microsoft\Edge\Application\msedge.exe",
                      rf"{base}\BraveSoftware\Brave-Browser\Application\brave.exe",
                      rf"{base}\Chromium\Application\chrome.exe"]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "microsoft-edge", "microsoft-edge-stable", "brave-browser"):
            p = shutil.which(name)
            if p:
                cands.append(p)
    # Playwright-managed Chromium (e.g. after `python -m playwright install chromium`)
    pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" if sys.platform == "win32"
        else Path.home() / ("Library/Caches/ms-playwright" if sys.platform == "darwin" else ".cache/ms-playwright"))
    for pat in ("chromium-*/chrome-linux*/chrome", "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-win*/chrome.exe"):
        cands += sorted(glob.glob(os.path.join(pw, pat)), reverse=True)
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


def browser_support() -> tuple[bool, str]:
    """(available, human-readable reason)."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "Python package 'playwright' is not installed (pip install playwright)"
    exe = find_browser()
    if not exe:
        return False, "No Chrome, Edge, Brave or Chromium found on this computer"
    return True, exe


class PlaywrightActuator:
    """Adapts a Playwright page to HumanDriver's Actuator protocol."""

    def __init__(self, page, mobile: bool, stop: threading.Event | None, cdp=None):
        self.page, self.mobile, self._stop, self.cdp = page, mobile, stop, cdp

    def viewport(self) -> tuple[int, int]:
        v = self.page.viewport_size or {"width": 1280, "height": 720}
        return v["width"], v["height"]

    def page_height(self) -> int:
        try:
            return int(self.page.evaluate("Math.max(document.documentElement.scrollHeight, "
                                          "document.body ? document.body.scrollHeight : 0)"))
        except Exception:
            return 0

    def scroll_y(self) -> float:
        try:
            return float(self.page.evaluate("window.scrollY"))
        except Exception:
            return 0.0

    def move(self, x: float, y: float) -> None:
        self.page.mouse.move(x, y)

    def wheel(self, dy: float) -> None:
        if self.mobile and self.cdp is not None:
            w, h = self.viewport()
            self.cdp.send("Input.synthesizeScrollGesture", {
                "x": w / 2 + random.uniform(-40, 40), "y": h * 0.6 + random.uniform(-60, 60),
                "yDistance": -dy, "gestureSourceType": "touch", "speed": int(random.uniform(600, 1400))})
        else:
            self.page.mouse.wheel(0, dy)

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self.stopped():
            left = end - time.monotonic()
            if left <= 0:
                return
            self.page.wait_for_timeout(min(250, left * 1000))

    def stopped(self) -> bool:
        return bool(self._stop and self._stop.is_set())


class BrowserTransport(Transport):
    is_browser = True

    def __init__(self, proxy: str | None = None, headless: bool = True, executable: str | None = None,
                 stop_event: threading.Event | None = None, scale: Callable[[], float] = lambda: 1.0,
                 download_prob: float = 0.03, on_event: Callable[[str, str], None] | None = None,
                 rng: random.Random | None = None):
        self.proxy, self.headless = proxy, headless
        self.executable = executable or find_browser()
        self.stop_event, self.scale, self.download_prob = stop_event, scale, download_prob
        self.on_event = on_event or (lambda level, msg: None)
        self.rng = rng or random.Random()
        self.fallback = ClearnetTransport(proxy=proxy)     # DoH and anything non-page
        self.dimmed = False
        self._pw = self._browser = None
        self._sessions: dict[int, dict] = {}               # identity id -> {ctx, page, mobile, cdp}
        self._major = "0"

    # ── lifecycle ──────────────────────────────────────────────────────────
    def _ensure(self) -> None:
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright
        if not self.executable:
            raise RuntimeError("no Chromium-family browser found")
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled", "--no-default-browser-check",
                "--no-first-run", "--disable-background-networking"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")                    # Chromium refuses to sandbox as root
        self._browser = self._pw.chromium.launch(executable_path=self.executable, headless=self.headless,
                                                 args=args, proxy={"server": self.proxy} if self.proxy else None)
        self._major = (self._browser.version or "0").split(".")[0]
        self.on_event("info", f"Real browser started: {Path(self.executable).name} {self._browser.version}"
                              f" ({'headless' if self.headless else 'visible'})")

    def close(self) -> None:
        for s in list(self._sessions.values()):
            try:
                s["ctx"].close()
            except Exception:
                pass
        self._sessions.clear()
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:
                pass
        self._browser = self._pw = None

    def _session(self, identity) -> dict:
        key = getattr(identity, "id", 0)
        s = self._sessions.get(key)
        if s:
            return s
        while len(self._sessions) >= 2:                    # an identity retires: close its context
            old_key = next(iter(self._sessions))
            try:
                self._sessions.pop(old_key)["ctx"].close()
            except Exception:
                pass
        rng = random.Random(f"{key}-{id(self)}")
        mobile = rng.random() < MOBILE_SHARE
        lang = (getattr(identity, "accept_language", "") or "en-US").split(",")[0]
        if mobile:
            (w, h), dpr, _ = rng.choices(MOBILE_VIEWPORTS, weights=[m[2] for m in MOBILE_VIEWPORTS])[0]
            ua = (f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                  f"Chrome/{self._major}.0.0.0 Mobile Safari/537.36")
            ctx = self._browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr,
                                            is_mobile=True, has_touch=True, user_agent=ua, locale=lang)
        else:
            (w, h), _ = rng.choices(DESKTOP_VIEWPORTS, weights=[v[1] for v in DESKTOP_VIEWPORTS])[0]
            vh = h - rng.choice([0, 70, 85, 110])          # browser chrome eats some height
            ctx = self._browser.new_context(viewport={"width": w, "height": vh}, locale=lang)
        page = ctx.new_page()
        cdp = None
        if mobile:
            try:
                cdp = ctx.new_cdp_session(page)
                cdp.send("Emulation.setUserAgentOverride", {
                    "userAgent": ua, "acceptLanguage": lang, "platform": "Linux armv8l",
                    "userAgentMetadata": {
                        "brands": [{"brand": "Chromium", "version": self._major},
                                   {"brand": "Not-A.Brand", "version": "24"}],
                        "platform": "Android", "platformVersion": "10.0.0", "architecture": "",
                        "model": "K", "mobile": True}})
            except Exception:
                cdp = None
        s = {"ctx": ctx, "page": page, "mobile": mobile, "cdp": cdp, "last_url": None}
        self._sessions[key] = s
        return s

    # ── a hit ──────────────────────────────────────────────────────────────
    def hit(self, identity, target: Target) -> HitResult:
        if target.kind != "html":
            return self.fallback.hit(identity, target)
        try:
            self._ensure()
            s = self._session(identity)
        except Exception as exc:
            return HitResult(None, error=f"browser unavailable: {exc}")
        page, mobile = s["page"], s["mobile"]
        act = PlaywrightActuator(page, mobile, self.stop_event, s["cdp"])
        driver = HumanDriver(self.rng, act, scale=self.scale())
        how = "open"
        try:
            resp = None
            if target.role == "result" and target.referer and s["last_url"] == target.referer:
                resp, how = self._click_through(page, driver, target, mobile)
            if resp is None and how == "open":
                resp = page.goto(target.url, referer=target.referer, wait_until="domcontentloaded",
                                 timeout=int(target.timeout * 1000))
            status = resp.status if resp is not None else 200
            if "/sorry/" in page.url or "captcha" in page.url.lower():
                status = 429
            stats = driver.browse(max_seconds=60)
            downloaded = self._maybe_download(page)
            s["last_url"] = page.url
            html = page.content()[:2_000_000]
            parts = [f"{how} {'📱' if mobile else '🖥'}", f"{stats['scrolls']} scrolls",
                     f"{int(stats['depth'] * 100)}% depth", f"{stats['seconds']:.0f}s reading"]
            if stats["back_scrolls"]:
                parts.append(f"{stats['back_scrolls']} scroll-back")
            if downloaded:
                parts.append(downloaded)
            self.on_event("debug", f"{urllib.parse.urlparse(page.url).hostname}: " + ", ".join(parts))
            res = HitResult(status, html)
            res.detail = ", ".join(parts)
            return res
        except Exception as exc:
            msg = str(exc).splitlines()[0][:200]
            if "Target page, context or browser has been closed" in msg:
                self._sessions.pop(getattr(identity, "id", 0), None)
            return HitResult(None, error=msg)

    def _click_through(self, page, driver: HumanDriver, target: Target, mobile: bool):
        """Find the result's link on the results page and click (or tap) it like a person."""
        host = urllib.parse.urlparse(target.url).hostname or ""
        bare = host.removeprefix("www.")
        try:
            loc = page.locator(f'a[href*="{bare}"]').first
            if loc.count() == 0:
                return None, "open"
            loc.evaluate("el => el.removeAttribute('target')")     # stay in this tab
            loc.scroll_into_view_if_needed(timeout=3000)
            box = loc.bounding_box()
            if not box:
                return None, "open"
            x = box["x"] + box["width"] * driver.rng.uniform(0.2, 0.7)
            y = box["y"] + box["height"] * driver.rng.uniform(0.3, 0.7)
            driver.hover_then_click_point(x, y)
            with page.expect_navigation(wait_until="domcontentloaded", timeout=int(target.timeout * 1000)) as nav:
                if mobile:
                    page.touchscreen.tap(x, y)
                else:
                    page.mouse.click(x, y, delay=int(lognormal_clipped(driver.rng, 90, 0.4, 40, 250)))
            return nav.value, ("tap" if mobile else "click")
        except Exception:
            return None, "open"

    def _maybe_download(self, page) -> str | None:
        """Occasionally fetch a linked document through the browser's own network stack, then discard it."""
        if self.rng.random() >= self.download_prob:
            return None
        try:
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        except Exception:
            return None
        host = urllib.parse.urlparse(page.url).hostname
        files = [h for h in hrefs if urllib.parse.urlparse(h).hostname == host
                 and urllib.parse.urlparse(h).path.lower().endswith(DOWNLOAD_EXT)]
        if not files:
            return None
        url = self.rng.choice(files)
        try:
            r = page.context.request.get(url, timeout=30000, max_redirects=5)
            size = len(r.body()) if int(r.headers.get("content-length") or 0) <= MAX_DOWNLOAD else 0
            r.dispose()
            return f"downloaded {Path(urllib.parse.urlparse(url).path).name} ({size // 1024} KB, discarded)"
        except Exception:
            return None
