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

from .useragents import DEFAULT_DEVICE, MOBILE_DEVICES
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


def find_playwright_firefox() -> str | None:
    """Playwright's own Firefox build (installed with `python -m playwright install firefox`).
    Playwright cannot drive an ordinary Firefox install, only this build."""
    bases = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")]
    home = Path.home()
    if sys.platform == "win32":
        bases.append(os.path.join(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")), "ms-playwright"))
        pats = ["firefox-*/firefox/firefox.exe"]
    elif sys.platform == "darwin":
        bases.append(str(home / "Library" / "Caches" / "ms-playwright"))
        pats = ["firefox-*/firefox/Nightly.app/Contents/MacOS/firefox"]
    else:
        bases.append(str(home / ".cache" / "ms-playwright"))
        pats = ["firefox-*/firefox/firefox"]
    for base in filter(None, bases):
        for pat in pats:
            hits = sorted(glob.glob(os.path.join(base, pat)))
            if hits:
                return hits[-1]
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
        self.page.mouse.wheel(0, dy)

    def swipe(self, dy: float) -> None:
        """A finger drag (real touch events; Chrome adds its own fling momentum)."""
        if self.cdp is None:
            self.page.mouse.wheel(0, dy)
            return
        w, h = self.viewport()
        x = w * random.uniform(0.35, 0.7)
        travel = max(-h * 0.7, min(h * 0.7, dy * random.uniform(0.55, 0.8)))
        y0 = h * (0.72 if travel > 0 else 0.3) + random.uniform(-30, 30)
        n = random.randint(7, 13)
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y0}]})
        for i in range(1, n + 1):
            t = i / n
            t = t * t * (3 - 2 * t)
            self.cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
                {"x": x + random.uniform(-2, 2) + 6 * t, "y": y0 - travel * t}]})
            self.page.wait_for_timeout(random.uniform(10, 22))
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self.stopped():
            left = end - time.monotonic()
            if left <= 0:
                return
            self.page.wait_for_timeout(min(250, left * 1000))

    def select_text(self, x: float, y: float, dx: float) -> bool:
        """Press, drag across a line of plain text, release: only on text, never on a link,
        button or form field, so it can never click anything."""
        try:
            ok = self.page.evaluate(
                "([x, y]) => { const e = document.elementFromPoint(x, y); if (!e) return false;"
                " if (e.closest('a,button,input,select,textarea,label,summary,[onclick],[role=button],[contenteditable]')) return false;"
                " return ['P','SPAN','LI','H1','H2','H3','H4','TD','BLOCKQUOTE','ARTICLE','DIV'].includes(e.tagName)"
                " && (e.innerText || '').trim().length > 40; }", [x, y])
            if not ok:
                return False
            m = self.page.mouse
            m.down()
            for i in range(1, 9):
                m.move(x + dx * i / 8, y + (i % 3 - 1) * 0.6)
                time.sleep(0.03)
            m.up()
            return True
        except Exception:
            return False

    def stopped(self) -> bool:
        return bool(self._stop and self._stop.is_set())


class BrowserTransport(Transport):
    is_browser = True

    def __init__(self, proxy: str | None = None, headless: bool = True, executable: str | None = None,
                 stop_event: threading.Event | None = None, scale: Callable[[], float] = lambda: 1.0,
                 download_prob: float = 0.03, on_event: Callable[[str, str], None] | None = None,
                 rng: random.Random | None = None, low_data: bool = False, image_prob: float = 0.04,
                 offscreen: bool = False, engine: str = "chromium"):
        self.engine = engine if engine in ("chromium", "firefox") else "chromium"
        self.offscreen = offscreen and not headless and self.engine == "chromium"
        self.low_data = low_data
        self.image_prob = 0.0 if low_data else image_prob     # no images loaded in low-data mode
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
        if self.engine == "firefox":
            # Firefox (Gecko): its own TLS/HTTP fingerprint, the same as people browsing with Firefox.
            self._pw = sync_playwright().start()
            self._browser = self._pw.firefox.launch(headless=self.headless,
                                                    proxy={"server": self.proxy} if self.proxy else None)
            self._major = (self._browser.version or "0").split(".")[0]
            self.on_event("info", f"Real browser started: Firefox {self._browser.version}"
                                  f" ({'headless' if self.headless else 'visible'})")
            return
        if not self.executable:
            raise RuntimeError("no Chromium-family browser found")
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled", "--no-default-browser-check",
                "--no-first-run", "--disable-background-networking"]
        if self.offscreen:                                 # a real window, placed where no screen is
            args += ["--window-position=-32000,-32000", "--window-size=1366,850"]
        if os.environ.get("CRICKET_NO_SANDBOX") == "1" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            args.append("--no-sandbox")                    # Chromium refuses to sandbox as root
        self._browser = self._pw.chromium.launch(executable_path=self.executable, headless=self.headless,
                                                 args=args, proxy={"server": self.proxy} if self.proxy else None)
        self._major = (self._browser.version or "0").split(".")[0]
        self.on_event("info", f"Real browser started: {Path(self.executable).name} {self._browser.version}"
                              f" ({'headless' if self.headless else 'off-screen window' if self.offscreen else 'visible'})")

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

    def _desktop_override(self) -> dict | None:
        """Headless Chromium says "HeadlessChrome" in its user agent and client hints, which no
        real person's browser does. Present the same browser and version as a normal window."""
        if not self.headless:
            return None
        exe = Path(self.executable or "").name.lower()
        brand = ("Microsoft Edge" if "msedge" in exe else "Brave" if "brave" in exe
                 else None if "chromium" in exe else "Google Chrome")
        if sys.platform == "win32":
            os_tok, plat = "Windows NT 10.0; Win64; x64", "Windows"
        elif sys.platform == "darwin":
            os_tok, plat = "Macintosh; Intel Mac OS X 10_15_7", "macOS"
        else:
            os_tok, plat = "X11; Linux x86_64", "Linux"
        ua = (f"Mozilla/5.0 ({os_tok}) AppleWebKit/537.36 (KHTML, like Gecko) "
              f"Chrome/{self._major}.0.0.0 Safari/537.36" + (" Edg/%s.0.0.0" % self._major if brand == "Microsoft Edge" else ""))
        brands = [{"brand": "Not_A Brand", "version": "8"}, {"brand": "Chromium", "version": self._major}]
        if brand:
            brands.append({"brand": brand, "version": self._major})
        return {"userAgent": ua, "userAgentMetadata": {
            "brands": brands, "platform": plat, "platformVersion": "", "architecture": "x86",
            "model": "", "mobile": False}}

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
        # The identity decides: phone (and which phone) or desktop. Older identities without
        # that information fall back to a random share.
        mobile = bool(identity.mobile) if hasattr(identity, "mobile") else rng.random() < MOBILE_SHARE
        if self.engine == "firefox":
            mobile = False          # Playwright's Firefox can't emulate a phone; those visits run as desktop
        device = MOBILE_DEVICES.get(getattr(identity, "device", "") or "")
        ios = bool(mobile and "iPhone" in (getattr(identity, "user_agent", "") or ""))
        if mobile and device is None:
            device = MOBILE_DEVICES[DEFAULT_DEVICE if ios else "Pixel 7"]
        lang = (getattr(identity, "accept_language", "") or "en-US").split(",")[0]
        if mobile:
            (w, h), dpr = device["viewport"], device["dpr"]
            # No user_agent here: Playwright's own override keeps "HeadlessChrome" client hints
            # and a Linux navigator.platform. The DevTools override below sets everything at once.
            ctx = self._browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr,
                                            is_mobile=True, has_touch=True, locale=lang)
        else:
            (w, h), _ = rng.choices(DESKTOP_VIEWPORTS, weights=[v[1] for v in DESKTOP_VIEWPORTS])[0]
            vh = h - rng.choice([0, 70, 85, 110])          # browser chrome eats some height
            ctx = self._browser.new_context(viewport={"width": w, "height": vh}, locale=lang)
        if self.low_data:              # optional: skip images, video and fonts to save data
            ctx.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ("image", "media", "font") else route.continue_())
        page = ctx.new_page()
        counter = {"bytes": 0}
        try:
            page.on("response", lambda r: counter.__setitem__(
                "bytes", counter["bytes"] + int(r.headers.get("content-length") or 0)))
        except Exception:
            pass
        cdp = None
        try:
            if ios:
                # iPhone Safari: its user agent, platform "iPhone", and no client hints at all
                # (Safari sends none; leaving the metadata out makes Chromium send none either).
                cdp = ctx.new_cdp_session(page)
                cdp.send("Emulation.setUserAgentOverride", {
                    "userAgent": identity.user_agent, "acceptLanguage": lang, "platform": "iPhone"})
            elif mobile:
                ua = (f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                      f"Chrome/{self._major}.0.0.0 Mobile Safari/537.36")
                cdp = ctx.new_cdp_session(page)
                cdp.send("Emulation.setUserAgentOverride", {
                    "userAgent": ua, "acceptLanguage": lang, "platform": "Linux armv8l",
                    "userAgentMetadata": {
                        "brands": [{"brand": "Chromium", "version": self._major},
                                   {"brand": "Google Chrome", "version": self._major},
                                   {"brand": "Not-A.Brand", "version": "24"}],
                        "platform": "Android", "platformVersion": "10.0.0", "architecture": "",
                        "model": "K", "mobile": True}})
            elif self.engine == "chromium":
                ov = self._desktop_override()
                if ov:
                    cdp = ctx.new_cdp_session(page)
                    cdp.send("Emulation.setUserAgentOverride", {**ov, "acceptLanguage": lang})
        except Exception:
            cdp = None
        s = {"ctx": ctx, "page": page, "mobile": mobile, "cdp": cdp, "last_url": None, "counter": counter}
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
        bytes_before = s.get("counter", {}).get("bytes", 0)
        driver = HumanDriver(self.rng, act, scale=self.scale())
        how = "open"
        try:
            resp = None
            t_load = time.time()
            if target.role == "result" and target.referer and s["last_url"] == target.referer:
                resp, how = self._click_through(page, driver, target, mobile)
            if resp is None and how == "open":
                resp = page.goto(target.url, referer=target.referer, wait_until="domcontentloaded",
                                 timeout=int(target.timeout * 1000))
            load_ms = (time.time() - t_load) * 1000.0
            status = resp.status if resp is not None else 200
            title = (page.title() or "").lower()
            if ("/sorry/" in page.url or "captcha" in page.url.lower()
                    or any(t in title for t in ("captcha", "unusual traffic", "are you a robot", "just a moment",
                                                "access denied", "attention required"))
                    or (status == 202 and "duckduckgo" in page.url)):
                status = 429          # a bot check: back off instead of counting it as a visit
            stats = driver.serp() if target.role == "search" else driver.browse()
            downloaded = self._maybe_download(page) or self._maybe_save_image(page)
            s["last_url"] = page.url
            html = page.content()[:2_000_000]
            parts = [f"{how} {'📱' if mobile else '🖥'}", f"{stats['scrolls']} scrolls",
                     f"{int(stats['depth'] * 100)}% depth", f"{stats['seconds']:.0f}s on page"]
            if stats.get("serp"):
                parts.insert(1, f"results: {stats['serp']}")
            if stats["back_scrolls"]:
                parts.append(f"{stats['back_scrolls']} scroll-back")
            if stats.get("selections"):
                parts.append(f"{stats['selections']} text selected")
            if downloaded:
                parts.append(downloaded)
            self.on_event("debug", f"{urllib.parse.urlparse(page.url).hostname}: " + ", ".join(parts))
            res = HitResult(status, html)
            res.detail = ", ".join(parts)
            res.load_ms = load_ms
            res.on_page_s = stats["seconds"]
            # content-length is missing for some compressed responses, so this is a floor
            res.bytes = max(len(html), s.get("counter", {}).get("bytes", 0) - bytes_before)
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

    # Page JavaScript fetch() goes out through the browser's own network stack (its TLS and
    # HTTP/2 fingerprint, cookies, cache). Playwright's context.request would not: it is a
    # separate HTTP client with its own fingerprint, so it is not used for noise.
    _FETCH_JS = """async ([u, cap, cors]) => {
        const ac = new AbortController(); const t = setTimeout(() => ac.abort(), 30000);
        try {
            const r = await fetch(u, {credentials: 'include', mode: cors ? 'cors' : 'no-cors', signal: ac.signal});
            if (r.type === 'opaque') { return [0, -1]; }            // cross-site image: fetched, size hidden
            const n = +(r.headers.get('content-length') || 0);
            if (n > cap) { ac.abort(); return [r.status, n]; }
            const b = await r.arrayBuffer(); return [r.status, b.byteLength];
        } catch (e) { return [-1, 0]; } finally { clearTimeout(t); }
    }"""

    def _maybe_download(self, page) -> str | None:
        """Occasionally fetch a linked document (same site) the way the browser would, then discard it."""
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
            status, size = page.evaluate(self._FETCH_JS, [url, MAX_DOWNLOAD, True])
        except Exception:
            return None
        if status < 0:
            return None
        return f"downloaded {Path(urllib.parse.urlparse(url).path).name} ({max(size, 0) // 1024} KB, discarded)"

    def _maybe_save_image(self, page) -> str | None:
        """Now and then 'save' a picture from the page: fetch it again through the browser, discard it."""
        if self.rng.random() >= self.image_prob:
            return None
        try:
            srcs = page.eval_on_selector_all(
                "img", "els => els.filter(e => e.naturalWidth >= 200 && e.naturalHeight >= 150"
                       " && /^https?:/.test(e.currentSrc || e.src)).map(e => e.currentSrc || e.src)")
        except Exception:
            return None
        if not srcs:
            return None
        src = self.rng.choice(srcs)
        same = urllib.parse.urlparse(src).hostname == urllib.parse.urlparse(page.url).hostname
        try:
            status, _ = page.evaluate(self._FETCH_JS, [src, MAX_DOWNLOAD, same])
        except Exception:
            return None
        return "saved an image (discarded)" if status >= 0 else None
