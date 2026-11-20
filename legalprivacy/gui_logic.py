"""Display-free pieces of the native desktop app: formatters, the state-to-view mapping, the debounced
live-apply helper and the thread hand-off queue. Nothing here imports tkinter, so it is fully testable
without a display (see tests/test_gui.py)."""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

# ── palette ─────────────────────────────────────────────────────────────────
LIGHT = {
    "bg": "#f4f6f8", "panel": "#ffffff", "fg": "#1d2733", "muted": "#66727f", "accent": "#2f6fdd",
    "accent_fg": "#ffffff", "border": "#d3dae1", "ok": "#1f8a4c", "warn": "#b7791f", "bad": "#c0392b",
    "info": "#2f6fdd", "field": "#ffffff", "select": "#cfe0fb", "band": "#cfe0fb", "line": "#2f6fdd",
    "you": "#1f8a4c", "noise": "#c0392b", "grid": "#e4e8ec", "log_bg": "#fbfcfd",
}
DARK = {
    "bg": "#1c2128", "panel": "#252c35", "fg": "#e6edf3", "muted": "#9aa7b4", "accent": "#5b9dff",
    "accent_fg": "#0d1117", "border": "#3a4450", "ok": "#4cc38a", "warn": "#e3b341", "bad": "#ff7b72",
    "info": "#79b8ff", "field": "#2d3640", "select": "#35507a", "band": "#2f4a73", "line": "#79b8ff",
    "you": "#4cc38a", "noise": "#ff7b72", "grid": "#323c47", "log_bg": "#161b22",
}
PALETTES = {"light": LIGHT, "dark": DARK}

LOG_LEVEL_ORDER = ["debug", "info", "warn", "error"]
LOG_LEVEL_LABELS = {"debug": "Everything", "info": "Normal", "warn": "Warnings and errors", "error": "Errors only"}
LOG_MAX_LINES = 5000
ACTIVITY_MAX_ROWS = 500

STATUS_GLYPH = {"ok": "✓", "bad": "✗", "info": "ℹ", "unknown": "?"}


# ── formatters ──────────────────────────────────────────────────────────────
def fmt_uptime(seconds: float | None) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return "—"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_clock(ts: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""


def fmt_log_line(ev: dict) -> str:
    return f"{fmt_clock(ev['ts'])}  {ev['level'].upper():5s} [{ev['source']}] {ev['message']}"


def fmt_bytes(n: float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


LANE_LABELS = {"clearnet": "Web (browser)", "clearnet-plain": "Web (plain)", "tor": "Onion (Tor)"}
ENGINE_LABELS = {"search": "searches", "crawl": "following links", "dns": "DNS lookups",
                 "onion": "onion sites", "packages": "package installs"}


def lane_label(name: str) -> str:
    return LANE_LABELS.get(name, name)


def lanes_rows(state: dict) -> list[tuple]:
    """Rows for the Dashboard lanes table: (lane, engines, hits, failures, budgets, last problem)."""
    rows = []
    for ln in state.get("lanes") or []:
        eng = ln.get("engines") or {}
        names = ", ".join(ENGINE_LABELS.get(e, e) for e in eng) or "—"
        budgets = "  ".join(f"{ENGINE_LABELS.get(e, e)} {int(b.get('spent', 0))}/{int(b.get('per_day', 0))}"
                            for e, b in eng.items()) or "—"
        status = lane_label(ln.get("name", "")) + ("" if ln.get("open", True) else " (waiting)")
        rows.append((status, names, ln.get("hits", 0), ln.get("failures", 0), budgets,
                     (ln.get("last_error") or "")[:60]))
    return rows


def short_host(url: str, width: int = 60) -> str:
    """Long .onion names are shortened in the middle, like the web dashboard does."""
    import re
    s = re.sub(r"([a-z2-7]{10})[a-z2-7]{30,}([a-z2-7]{6}\.onion)", r"\1…\2", str(url))
    return s if len(s) <= width else s[:width - 1] + "…"


def now_text(current: dict | None, now: float | None = None) -> str:
    """The Dashboard's 'what it is doing now' line (site name only, never a full address)."""
    if not current:
        return "Idle: waiting for the next action."
    now = time.time() if now is None else now
    role, eng = current.get("role"), current.get("engine")
    what = ("looking at search results" if role == "search" else "a DNS lookup" if eng == "dns"
            else "installing a package" if eng == "packages" else "reading a result" if role == "result"
            else "on a page")
    secs = max(0, int(now - (current.get("started") or now)))
    return f"Now: {what}: {site_name(current.get('url', ''))} (for {secs} s)"


def site_name(url: str) -> str:
    """Just the site (host) of an address; DNS rows and package lines are already names."""
    u = str(url or "").strip()
    if "://" in u:
        host = urlparse(u).hostname or u
    elif "/" in u and " " not in u:
        host = u.split("/", 1)[0]
    else:
        host = u
    return short_host(host.removeprefix("www."), 48)


KIND_LABELS = {"search": "search", "crawl": "page", "dns": "DNS lookup", "onion": "onion page",
               "packages": "package"}


def hit_kind(hit: dict) -> str:
    if hit.get("role") == "result":
        return "clicked a result"
    return KIND_LABELS.get(hit.get("engine"), hit.get("engine") or "")


def hit_status(hit: dict) -> tuple[str, str]:
    """(text, tag) with tag in ok | bad | mute."""
    err = hit.get("error") or ""
    if hit.get("ok"):
        return ("OK" if not hit.get("status") else f"OK ({hit['status']})"), "ok"
    if "offline" in err:
        return "site offline", "mute"
    return (f"HTTP {hit['status']}" if hit.get("status") else (err[:40] or "error")), "bad"


def hit_row(hit: dict) -> tuple:
    """(seq, time, lane, kind, site, status, tag)  — a site name only, never a page address."""
    text, tag = hit_status(hit)
    return (hit.get("seq"), fmt_clock(hit.get("ts")), lane_label(hit.get("lane", "")), hit_kind(hit),
            site_name(hit.get("url", "")), text, tag)


def newest_first(hits: list[dict], cap: int = ACTIVITY_MAX_ROWS) -> list[dict]:
    return list(reversed(hits[-cap:]))


# ── header / chips ──────────────────────────────────────────────────────────
def run_status(state: dict) -> tuple[str, str]:
    """(kind, text): kind in running | paused | stopped."""
    if state.get("running"):
        return ("paused", "Paused") if state.get("paused") else ("running", "Running")
    return "stopped", "Stopped"


def tor_chip(tor: dict | None, enabled: bool | None = None) -> tuple[str, str]:
    """(text, tone) tone in ok | warn | bad | off."""
    tor = tor or {}
    if enabled is None:
        enabled = bool(tor.get("enabled"))
    if not enabled:
        return "Tor: off", "off"
    st = tor.get("state") or "stopped"
    pct = tor.get("bootstrap_pct") or 0
    if st == "ready":
        return "Tor: connected", "ok"
    if st == "error":
        return "Tor: problem", "bad"
    if st in ("bootstrapping", "starting", "installing"):
        return (f"Tor: connecting {pct}%" if pct else f"Tor: {st}"), "warn"
    return f"Tor: {st}", "warn"


def settings_chip(status: dict | None, applying: bool = False, error: str | None = None) -> tuple[str, str]:
    """The header chip for ctl.settings_status(): '✓ settings in effect HH:MM:SS'."""
    if error:
        return f"✗ Not saved: {error}", "bad"
    if applying:
        return "Applying…", "warn"
    status = status or {}
    if not status.get("running"):
        return "Settings saved", "off"
    if status.get("in_effect"):
        at = status.get("applied_at")
        return "✓ settings in effect" + (f" {fmt_clock(at)}" if at else ""), "ok"
    return "! running settings differ from saved: press Stop, then Start", "warn"


def apply_message(status: dict | None, was_running: bool) -> tuple[str, str]:
    """Text under the settings after a live apply."""
    if not was_running:
        return "✓ Saved. It takes effect when you press Start.", "ok"
    if status and status.get("in_effect"):
        return "✓ Live and verified", "ok"
    return "Applied, but the running settings do not match the saved ones. Press Stop, then Start.", "warn"


def button_states(state: dict) -> dict[str, bool]:
    running = bool(state.get("running"))
    return {"start": not running, "pause": running, "stop": running}


def pause_label(state: dict) -> str:
    return "Resume" if state.get("paused") else "Pause"


def rate_text(level: int | float, rate_info: list[dict] | None) -> str:
    info = None
    if rate_info:
        try:
            info = rate_info[int(round(level)) - 1]
        except (IndexError, ValueError):
            info = None
    if not info:
        return f"Level {int(round(level))}"
    return (f"Level {int(round(level))}: {info['name']}. About {info['actions_per_hour']} actions an hour "
            f"({info['searches_per_hour']} searches), {info['vs_average_person']}× an average person's searching.")


# ── settings <-> controller ────────────────────────────────────────────────
DNS_CHOICES = [("mix", "Mix of both (recommended)"), ("system", "Your normal DNS only"),
               ("secure", "Privacy DNS servers only")]
WINDOW_CHOICES = [("hidden", "Hidden (recommended)"), ("offscreen", "Off-screen window"),
                  ("visible", "Visible (watch it work)")]
BRIDGE_CHOICES = [("off", "No bridges (direct)"), ("obfs4", "obfs4 (built in)"), ("snowflake", "Snowflake (built in)"),
                  ("meek", "meek (built in)"), ("custom", "My own bridges (add them in the web dashboard)")]
COOKIE_UNITS = [("hours", 1.0), ("days", 24.0)]


def cookie_to_hours(value: float, unit: str) -> float:
    return max(0.0, float(value)) * (24.0 if unit == "days" else 1.0)


def cookie_from_hours(hours: float) -> tuple[float, str]:
    """Show whole days when it divides evenly, otherwise hours."""
    h = float(hours or 0)
    if h >= 24 and abs(h / 24 - round(h / 24)) < 1e-6:
        return float(round(h / 24)), "days"
    return round(h, 2), "hours"


def build_config_payload(v: dict[str, Any], current_rate: dict | None = None) -> dict:
    """Controls -> the partial profile dict `Controller.apply_config` takes. `v` holds plain values:
    level, rate_mode (random|steady), dns_mode, cookie_value, cookie_unit, window, tor_enabled,
    bridge_mode, bridge_auto. Missing keys are left out so a partial form never overwrites other settings."""
    out: dict[str, Any] = {}
    if "level" in v or "rate_mode" in v:
        rate: dict[str, Any] = {}
        if "level" in v:
            rate["level"] = float(min(10, max(1, round(float(v["level"])))))
        if "rate_mode" in v:
            steady = v["rate_mode"] == "steady"
            old = float((current_rate or {}).get("randomness", 1.0) or 0)
            rate["mode"] = "steady" if steady else "random"
            rate["randomness"] = 0.0 if steady else (old if old > 0 else 1.0)
        out["rate"] = rate
    if "dns_mode" in v:
        out["dns"] = {"mode": v["dns_mode"]}
    clear: dict[str, Any] = {}
    if "cookie_value" in v:
        clear["cookie_hours"] = cookie_to_hours(v["cookie_value"], v.get("cookie_unit", "hours"))
    if "window" in v:
        clear["browser_window"] = v["window"]
    if clear:
        out.setdefault("transports", {})["clearnet"] = clear
    tor: dict[str, Any] = {}
    for src, dst in (("tor_enabled", "enabled"), ("bridge_mode", "bridge_mode"), ("bridge_auto", "bridge_auto")):
        if src in v:
            tor[dst] = v[src]
    if tor:
        out.setdefault("transports", {})["tor"] = tor
    return out


def profile_values(profile, app: dict) -> dict:
    """The control values that match the saved profile (inverse of build_config_payload/app_flags)."""
    rate, clear, tor = profile.rate, profile.transports["clearnet"], profile.transports["tor"]
    cv, cu = cookie_from_hours(clear.cookie_hours)
    steady = rate.mode == "steady" or float(rate.randomness or 0) == 0
    return {"level": int(round(float(rate.level))), "rate_mode": "steady" if steady else "random",
            "dns_mode": profile.dns.mode, "cookie_value": cv, "cookie_unit": cu, "window": clear.browser_window,
            "tor_enabled": bool(tor.enabled), "bridge_mode": tor.bridge_mode, "bridge_auto": bool(tor.bridge_auto),
            "autostart": bool(app.get("autostart")), "clear_logs_on_restart": bool(app.get("clear_logs_on_restart", True)),
            "observe": bool(app.get("observe", True))}


def app_flags(v: dict[str, Any]) -> dict:
    """The on/off settings that live in the app section (saved with Controller.update_app)."""
    return {k: bool(v[k]) for k in ("autostart", "clear_logs_on_restart", "observe") if k in v}


# ── log view ───────────────────────────────────────────────────────────────
def visible_events(events: list[dict], min_level: str) -> list[dict]:
    from .events import LEVELS
    floor = LEVELS.get(min_level, 10)
    return [e for e in events if LEVELS.get(e.get("level"), 20) >= floor]


# ── proof tab ───────────────────────────────────────────────────────────────
def check_lines(check: dict) -> tuple[str, str, str, str]:
    """(glyph + title, 'You want to see: …', 'We saw: …', hint) for one leak-check result."""
    return (f"{STATUS_GLYPH.get(check.get('status'), '?')}  {check.get('title', '')}",
            f"You want to see: {check.get('want', '')}", f"We saw: {check.get('saw', '')}", check.get("hint") or "")


def hours_label(h: int) -> str:
    return f"{h % 24:02d}"


def nice_max(v: float) -> float:
    """A round upper bound for a chart axis."""
    if v <= 0:
        return 1.0
    import math
    mag = 10 ** math.floor(math.log10(v))
    for k in (1, 2, 5, 10):
        if v <= k * mag:
            return float(k * mag)
    return float(10 * mag)


def scale(v: float, lo: float, hi: float, a: float, b: float) -> float:
    """Map v from [lo, hi] onto [a, b] (b may be smaller than a for an upward axis)."""
    return a if hi == lo else a + (v - lo) / (hi - lo) * (b - a)


def baseline_sources_text(base: dict) -> str:
    """'Where your baseline comes from' as plain lines."""
    inner = (base or {}).get("baseline") or {}
    lines = [inner.get("status") or "Not enough data yet."]
    for s in inner.get("sources") or []:
        note = f" ({s['note']})" if s.get("note") else ""
        lines.append(f"  • {s['name']}: {s['detail']}: {s['state']}{note}")
    return "\n".join(lines)


# ── threads ─────────────────────────────────────────────────────────────────
class UiQueue:
    """Worker threads post callables; the Tk main thread drains them from an `after` poll."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()

    def post(self, fn: Callable, *a, **kw) -> None:
        self._q.put((fn, a, kw))

    def drain(self, limit: int = 100) -> int:
        n = 0
        while n < limit:
            try:
                fn, a, kw = self._q.get_nowait()
            except queue.Empty:
                break
            n += 1
            try:
                fn(*a, **kw)
            except Exception:                    # one bad callback must not stop the others
                import traceback
                traceback.print_exc()
        return n

    def empty(self) -> bool:
        return self._q.empty()


def run_in_thread(fn: Callable, post: Callable, on_done: Callable | None = None,
                  on_error: Callable | None = None, name: str = "gui-worker") -> threading.Thread:
    """Run fn() off the main thread; hand the result (or exception) back with `post`."""
    def work():
        try:
            res = fn()
        except BaseException as exc:             # noqa: BLE001 - shown to the person, not swallowed
            if on_error:
                post(on_error, exc)
            return
        if on_done:
            post(on_done, res)
    t = threading.Thread(target=work, daemon=True, name=name)
    t.start()
    return t


class Debouncer:
    """Call `fire` once, `delay_ms` after the last `poke()`. `schedule(ms, fn) -> handle` and
    `cancel(handle)` are injected (Tk's after/after_cancel in the app, a fake clock in the tests)."""

    def __init__(self, schedule: Callable, cancel: Callable, fire: Callable, delay_ms: int = 700):
        self._schedule, self._cancel, self._fire, self.delay_ms = schedule, cancel, fire, delay_ms
        self._h = None

    @property
    def pending(self) -> bool:
        return self._h is not None

    def poke(self) -> None:
        if self._h is not None:
            self._cancel(self._h)
        self._h = self._schedule(self.delay_ms, self._go)

    def _go(self) -> None:
        self._h = None
        self._fire()

    def flush(self) -> None:
        if self._h is not None:
            self._cancel(self._h)
            self._h = None
            self._fire()


class LiveApply:
    """Debounced save-and-apply. `collect()` runs on the main thread (it reads the controls) and returns
    {"config": partial profile dict, "app": {flags}}; the work then runs in one worker thread:
    apply_config, update_app, and, if the noise is running with different settings, reapply().
    `report(kind, text, tone)` is called on the main thread with kind in applying | done | error."""

    def __init__(self, ctl, collect: Callable[[], dict], post: Callable, report: Callable,
                 schedule: Callable, cancel: Callable, delay_ms: int = 700, runner: Callable | None = None):
        self.ctl, self.collect, self.post, self.report = ctl, collect, post, report
        self._run = runner or run_in_thread
        self._lock = threading.Lock()
        self.debounce = Debouncer(schedule, cancel, self._start, delay_ms)
        self.busy = False

    def changed(self) -> None:
        self.report("applying", "Applying…", "warn")
        self.debounce.poke()

    def _start(self) -> None:
        job = self.collect()
        self.busy = True

        def work():
            with self._lock:                      # one apply at a time; later edits queue up behind it
                was_running = bool(self.ctl.running)
                if job.get("config"):
                    self.ctl.apply_config(job["config"])
                if job.get("app"):
                    self.ctl.update_app(job["app"])
                if was_running or self.ctl.running:
                    st = self.ctl.settings_status()
                    if st.get("running") and not st.get("in_effect"):
                        self.ctl.reapply()
                return was_running, self.ctl.settings_status()

        def done(res):
            self.busy = False
            if self.debounce.pending:             # more edits arrived meanwhile: stay in "Applying…"
                return
            was_running, st = res
            text, tone = apply_message(st, was_running)
            self.report("done", text, tone)

        def failed(exc):
            self.busy = False
            self.report("error", str(exc) or exc.__class__.__name__, "bad")

        self._run(work, self.post, done, failed, "gui-apply")
