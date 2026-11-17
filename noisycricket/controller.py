"""Application controller: one object the UI (and CLI) drives.

Owns the profile, the Tor manager, the scheduler thread and the event log, and
exposes simple start/stop/configure operations plus a JSON-friendly state()
snapshot for the dashboard.
"""
from __future__ import annotations

import copy
import platform
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .config import (ConfigError, Profile, apply_overrides, load_profile,
                     profile_to_dict, save_profile)
from .engines import load_wordlist
from .dnsplan import DnsPlan
from .domains import DomainPool
from .rate import RatePlanner, describe as describe_rate
from .events import EventLog
from .feeds import FeedStore
from .queries import QueryConfig, QuerySource
from .onion import BUILTIN_ONION_SEEDS, clean_seed_list
from .paths import app_dir, config_path, resolve_resource
from .scheduler import MaxFailuresExceeded, Runner, build_lanes
from .tormanager import TorManager, find_tor_binary, port_open

PRESETS = {   # budgets per day: search / crawl / dns / onion
    "light":  {"search": 50,  "crawl": 20,  "dns": 80,  "onion": 15},
    "normal": {"search": 120, "crawl": 40,  "dns": 200, "onion": 40},
    "heavy":  {"search": 300, "crawl": 100, "dns": 500, "onion": 100},
}
def wipe_old_log_files() -> None:
    """Delete any cricket.log left by an older version. The app keeps no log files."""
    for name in ("cricket.log", "cricket.log.1"):
        try:
            (app_dir() / name).unlink()
        except OSError:
            pass


APP_DEFAULTS = {"autostart": False, "open_browser": True, "port": 8765, "intensity": "normal",
                "auto_refresh_sources": False}   # off: use the snapshot shipped with the app (no lookups)


class Controller:
    def __init__(self, profile_path: str | None = None):
        wipe_old_log_files()                      # older versions wrote cricket.log to disk
        self.log = EventLog()                     # live log is memory-only (never written to disk)
        self.profile_path = profile_path or str(config_path())
        self.first_run = not Path(self.profile_path).exists()
        self.app: dict[str, Any] = dict(APP_DEFAULTS)
        self.profile: Profile = Profile(name="default")
        self._load()
        self.tor: TorManager | None = None
        self.feeds = FeedStore(self.log)
        self._feed_timer_started = False
        self.runner: Runner | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.log.info("app", f"noisyCricket {__version__} on {platform.system()} {platform.release()} "
                             f"(Python {platform.python_version()})")
        self.log.info("app", f"Config: {self.profile_path}")

    # ── config ──────────────────────────────────────────────────────────────
    def _load(self) -> None:
        p = Path(self.profile_path)
        if not p.exists():
            return
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            self.profile = apply_overrides(Profile(name="default"), raw)
            self.app |= {k: v for k, v in (raw.get("app") or {}).items() if k in APP_DEFAULTS}
        except Exception as exc:
            self.log.error("app", f"Could not read config ({exc}); using defaults.")

    def _save(self) -> None:
        data = profile_to_dict(self.profile)
        data["app"] = self.app
        path = Path(self.profile_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        self.first_run = False

    def wizard(self, answers: dict[str, Any]) -> dict:
        """Apply the 2–3 first-run answers and save.

        intensity: light|normal|heavy       tor: off|onion
        clearnet_via_tor: bool (advanced, default False)   autostart: bool
        """
        raw: dict[str, Any] = {"rate": {"mode": answers.get("rate_mode", "random"),
                                        "level": float(answers.get("level", 5))},
                               "transports": {"tor": {"enabled": answers.get("tor", "off") != "off",
                                                      "onion": True,
                                                      "clearnet_via_tor": bool(answers.get("clearnet_via_tor", False))}}}
        self.apply_config(raw, save=False)
        self.app["autostart"] = bool(answers.get("autostart", False))
        self._save()
        self.log.info("app", f"Setup saved: {self.profile.rate.mode} noise at level {self.profile.rate.level:g}, "
                             f"tor={answers.get('tor', 'off')}.")
        return self.state()

    def apply_config(self, raw: dict[str, Any], save: bool = True) -> dict:
        """Validate+apply a partial profile dict. Takes effect on next (re)start."""
        new = copy.deepcopy(self.profile)
        tor_raw = (raw.get("transports") or {}).get("tor") or {}
        if "onion_seeds" in tor_raw:
            seeds = tor_raw["onion_seeds"]
            if isinstance(seeds, str):
                seeds = seeds.splitlines()
            good, bad = clean_seed_list(list(seeds))
            if bad:
                raise ConfigError("invalid onion address(es): " + ", ".join(bad[:3]))
            tor_raw["onion_seeds"] = good
        if "bridges" in tor_raw and isinstance(tor_raw["bridges"], str):
            tor_raw["bridges"] = [ln.strip() for ln in tor_raw["bridges"].splitlines() if ln.strip()]
        apply_overrides(new, raw)
        self.profile = new
        if save:
            self._save()
        self.log.info("app", "Settings saved." + (" Restart noise to apply." if self.running else ""))
        return self.state()

    def update_app(self, raw: dict[str, Any]) -> None:
        for k in ("autostart", "open_browser", "auto_refresh_sources"):
            if k in raw:
                self.app[k] = bool(raw[k])
        self._save()
        self._start_feed_timer()

    # ── Tor ─────────────────────────────────────────────────────────────────
    def _tor_cfg(self):
        return self.profile.transports["tor"]

    def tor_manager(self) -> TorManager:
        cfg = self._tor_cfg()
        mode = "managed" if cfg.manage else "external"
        if self.tor is None or self.tor.mode != mode or self.tor.ext_socks != cfg.socks \
                or self.tor.bridges != [b.strip() for b in cfg.bridges if b.strip()]:
            if self.tor and self.tor.state not in ("stopped",):
                self.tor.stop()
            self.tor = TorManager(self.log, mode=mode, socks=cfg.socks, control=cfg.control,
                                  control_password=cfg.control_password,
                                  bridges=cfg.bridges, auto_install=cfg.auto_install)
        return self.tor

    # ── Tools tab: network checks (user-initiated, clearnet only) ───────────
    def resolvers_check(self) -> dict:
        from . import nettools
        dns = self.profile.dns
        secure: list[str] = []
        if dns.opennic:
            secure += [x.split("|")[0] for x in self.feeds.items("opennic")]
        if dns.quad9:
            secure += ["9.9.9.9", "149.112.112.112"]
        secure += list(dns.custom)
        return nettools.dns_leak_check(secure)

    def speed_start(self) -> dict:
        from . import nettools
        st = getattr(self, "_speed", None)
        if st and st.get("state") == "running":
            return st
        self._speed = {"state": "running", "progress": "Starting…", "result": None}
        proxy = self.profile.transports.clearnet.proxy

        def work():
            res = nettools.speed_test(proxy, progress=lambda s: self._speed.update(progress=s))
            self._speed.update(state="done", progress="", result=res)
            self.log.info("tools", f"Speed test: down {res['down_mbps']} Mbit/s, up {res['up_mbps']} Mbit/s"
                          + (f" ({'; '.join(res['errors'])})" if res["errors"] else ""))
        threading.Thread(target=work, daemon=True).start()
        return self._speed

    # ── My List ──────────────────────────────────────────────────────────────
    def _domain_pool(self, _r):
        ml = self.profile.mylist
        return DomainPool(_r.Random(), self.feeds, my_sites=ml.sites if ml.enabled else None,
                          random_share=ml.random_share, my_weights=ml.weights)

    def mylist_state(self) -> dict:
        from .history import history_files
        ml = self.profile.mylist
        now = time.time()
        ends = ml.watch_started + ml.watch_hours * 3600 if ml.watch_started else 0
        return {"enabled": ml.enabled, "sites": ml.sites, "weights": ml.weights, "random_share": ml.random_share,
                "watch_hours": ml.watch_hours, "watch_started": ml.watch_started, "watch_ends": ends,
                "watch_done": bool(ml.watch_started) and now >= ends, "now": now,
                "browsers": sorted({f"{f['browser']} ({f['profile']})" for f in history_files()}),
                "running": self.running}

    def mylist_scan(self, days: float) -> dict:
        from .history import top_sites
        days = max(1.0, min(365.0, float(days)))
        res = top_sites(time.time() - days * 86400, denylist=self.feeds.denylist())
        self.log.info("mylist", f"Scanned browser history (last {days:g} days): {len(res['sites'])} sites found.")
        return res

    def mylist_watch(self, action: str, hours: float | None = None) -> dict:
        from .history import top_sites
        ml = self.profile.mylist
        if action == "start":
            self.apply_config({"mylist": {"watch_started": time.time(),
                                          "watch_hours": float(hours or ml.watch_hours)}})
            self.log.info("mylist", f"Recording your sites for {self.profile.mylist.watch_hours:g} hours.")
            return self.mylist_state()
        if action == "cancel":
            self.apply_config({"mylist": {"watch_started": 0}})
            return self.mylist_state()
        if action == "collect":
            if not ml.watch_started:
                raise ConfigError("Not recording.")
            end = min(time.time(), ml.watch_started + ml.watch_hours * 3600)    # cut off at the window end
            res = top_sites(ml.watch_started, end, denylist=self.feeds.denylist())
            self.apply_config({"mylist": {"watch_started": 0}})
            self.log.info("mylist", f"Recording collected: {len(res['sites'])} sites.")
            return res
        raise ConfigError(f"unknown action {action}")

    def mylist_bookmarks(self, urls: list | None = None) -> dict:
        """Bookmarks from your browsers, or the links from a bookmarks export file (the page
        extracts just the links and sends those, never titles)."""
        from .history import bookmark_sites, sites_from_urls
        if urls is not None:
            res = sites_from_urls([str(u) for u in urls[:50_000]], self.feeds.denylist())
        else:
            res = bookmark_sites(self.feeds.denylist())
        self.log.info("mylist", f"Read bookmarks: {len(res['sites'])} sites found.")
        return res

    def mylist_save(self, body: dict) -> dict:
        keep = {k: body[k] for k in ("enabled", "sites", "weights", "random_share") if k in body}
        self.apply_config({"mylist": keep})
        return self.mylist_state()

    # ── user agents (Settings) ─────────────────────────────────────────────
    def ua_state(self) -> dict:
        from .useragents import library
        ident = self.profile.identity
        rows = [{"id": e["id"], "ua": e["ua"], "group": e["group"], "platform": e["platform"],
                 "release": e["release"], "share": e["share"]} for e in library()]
        return {"mode": ident.ua_mode, "rotate_every": ident.rotate_every, "pin": ident.ua_pin,
                "own_ua": ident.own_ua, "library": rows, "running": self.running,
                "applies_to": "Plain-HTTP lane. The real-browser lane always sends its own browser's "
                              "user agent, and the onion lane always uses Tor Browser's fixed one."}

    def ua_save(self, body: dict) -> dict:
        allowed = {k: str(body[k]) for k in ("ua_mode", "rotate_every", "ua_pin", "own_ua") if k in body}
        cur = self.profile.identity
        mode = allowed.get("ua_mode", cur.ua_mode)
        pin = allowed.get("ua_pin", cur.ua_pin)
        own = allowed.get("own_ua", cur.own_ua).strip()
        if mode == "pin" and not pin:
            raise ConfigError("Choose what to pin to first.")
        if mode == "pin" and pin == "own" and not own:
            raise ConfigError("Paste or detect your browser's user agent first.")
        self.apply_config({"identity": allowed})
        return self.ua_state()

    def ua_detect(self) -> dict:
        from .useragents import detect_own_ua
        return detect_own_ua()

    def speed_status(self) -> dict:
        return getattr(self, "_speed", None) or {"state": "idle"}

    def tor_action(self, action: str) -> dict:
        tm = self.tor_manager()
        if action == "start":
            tm.start()
        elif action == "stop":
            tm.stop()
        elif action == "restart":
            tm.restart()
        elif action == "newnym":
            tm.new_identity()
        elif action == "install":
            tm.install()
        else:
            raise ConfigError(f"unknown tor action '{action}'")
        return self.tor_state()

    def tor_state(self) -> dict:
        tm = self.tor_manager()
        d = tm.snapshot()
        d["enabled"] = self._tor_cfg().enabled
        d["circuits"] = tm.circuits()
        return d

    # ── realism helpers ─────────────────────────────────────────────────────
    def browser_info(self) -> dict:
        from .browser import browser_support
        cfg = self.profile.transports["clearnet"]
        ok, detail = browser_support()
        active = cfg.browser == "on" or (cfg.browser == "auto" and ok)
        return {"setting": cfg.browser, "available": ok, "detail": detail,
                "active": bool(active and ok), "visible": cfg.browser_visible}

    def _clearnet_transport(self, stop: threading.Event):
        info = self.browser_info()
        cfg = self.profile.transports["clearnet"]
        if not info["active"]:
            if cfg.browser == "on":
                self.log.warn("browser", f"Real-browser mode is on but unavailable: {info['detail']}. "
                                         "Using plain HTTP instead.")
            return None
        from .browser import BrowserTransport
        return BrowserTransport(proxy=cfg.proxy, headless=not cfg.browser_visible, executable=info["detail"],
                                stop_event=stop, scale=lambda: self.profile.realtime_scale,
                                on_event=lambda lvl, msg: self.log.add(lvl, "browser", msg))

    def _query_source(self, words):
        import random as _r
        q = self.profile.queries
        return QuerySource(_r.Random(), self.feeds, QueryConfig(q.live, q.dataset, q.builtin, q.words), words)

    def _start_feed_timer(self) -> None:
        """Only if the user opted in: re-fetch sources when they are ~3 months old."""
        if self._feed_timer_started or not self.app.get("auto_refresh_sources"):
            return
        self._feed_timer_started = True

        def loop():
            while True:
                self.feeds.refresh_async()
                time.sleep(1800)
        threading.Thread(target=loop, daemon=True, name="feed-timer").start()

    def refresh_feeds(self) -> None:
        self.feeds.refresh_async(force=True)

    # ── run control ─────────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop = threading.Event()
            prof = self.profile
            active = next((e.wordlist for e in prof.engines.values() if e.enabled and e.wordlist), None)
            words = load_wordlist(active)
            tor_cfg = self._tor_cfg()
            gate = None
            if tor_cfg.enabled:
                tm = self.tor_manager()
                tm.start()

                def gate() -> bool:    # lane opens only once Tor is bootstrapped
                    if tm.ready:
                        lane = next((l for l in (self.runner.lanes if self.runner else []) if l.name == "tor"), None)
                        if lane is not None and (lane.transport.socks_port != tm.socks_port or
                                                 lane.transport.control != tm.control_addr):
                            lane.transport.set_endpoint(tm.socks_addr, tm.control_addr, tm.cookie_path)
                        return True
                    return False
            self._start_feed_timer()
            browser_tr = self._clearnet_transport(self._stop)
            plain_tr = None
            if browser_tr is not None and prof.traffic.plain > 0:      # both kinds of clearnet noise
                from .transports import ClearnetTransport
                plain_tr = ClearnetTransport(proxy=prof.transports["clearnet"].proxy)
            lanes = build_lanes(prof, words, tor_gate=gate, clearnet_transport=browser_tr,
                                plain_transport=plain_tr)
            if not lanes:
                raise ConfigError("Nothing to run: enable clearnet noise or Tor/onion noise in Settings.")
            import random as _r
            self.runner = Runner(profile=prof, lanes=lanes, wordlist=words, log=self.log,
                                 stop_event=self._stop, sleep_fn=lambda s: self._stop.wait(s),
                                 queries=self._query_source(words), domains=self._domain_pool(_r),
                                 rate=RatePlanner(_r.Random(), prof.rate),
                                 dns_plan=DnsPlan(_r.Random(), prof.dns, self.feeds.items("opennic")),
                                 mix=True)
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="cricket-runner")
            self._thread.start()
            self.log.info("app", "Started: lanes = " + ", ".join(
                f"{l.name}[{'/'.join(l.engines)}]" for l in lanes))

    def _run_loop(self) -> None:
        try:
            self.runner.run_forever()
        except Exception as exc:
            self.log.error("scheduler", f"Runner crashed: {exc!r}")

    def stop(self, stop_tor: bool = False) -> None:
        with self._lock:
            self._stop.set()
            t = self._thread
        if t:
            t.join(timeout=8)
        self.log.info("app", "Stopped.")
        if stop_tor and self.tor:
            self.tor.stop()
        wipe_old_log_files()
        self.log.clear()                          # nothing from this run is kept after quitting

    def pause(self, paused: bool) -> None:
        if self.runner:
            self.runner.paused = paused
            self.log.info("app", "Paused." if paused else "Resumed.")

    def test_once(self) -> None:
        """One immediate round (no pacing delay), for the 'Send one test hit' button."""
        def _go():
            if self.running and self.runner:
                self.runner.run_once(wait=False)
                return
            prof = self.profile
            words = load_wordlist(next((e.wordlist for e in prof.engines.values() if e.enabled and e.wordlist), None))
            lanes = [l for l in build_lanes(prof, words) if l.name == "clearnet"] or build_lanes(prof, words)
            import random as _r
            r = Runner(profile=prof, lanes=lanes, wordlist=words, log=self.log, sleep_fn=lambda s: None,
                       local_hour_fn=lambda: 12.0, queries=self._query_source(words),
                       domains=self._domain_pool(_r),
                       dns_plan=DnsPlan(_r.Random(), prof.dns, self.feeds.items("opennic")))
            try:
                r.run_once(wait=False)
            except MaxFailuresExceeded as exc:
                self.log.error("scheduler", str(exc))
            finally:
                r.close()
        threading.Thread(target=_go, daemon=True, name="cricket-once").start()

    # ── state / diagnostics ─────────────────────────────────────────────────
    def state(self) -> dict:
        r = self.runner
        stats = r.stats() if r else {"lanes": [], "paused": False, "uptime": 0}
        if not self.running:
            stats["paused"] = False
        return {
            "version": __version__, "first_run": self.first_run, "running": self.running,
            "paused": stats.get("paused", False), "uptime": stats.get("uptime", 0) if self.running else 0,
            "lanes": stats["lanes"], "app": self.app,
            "profile": profile_to_dict(self.profile)["profile"],
            "tor": self.tor_state(), "log_last_id": self.log.last_id(),
            "builtin_onion_seeds": [{"name": n, "url": u} for n, u in BUILTIN_ONION_SEEDS],
            "config_path": self.profile_path,
            "presets": PRESETS, "browser": self.browser_info(), "feeds": self.feeds.status(),
            "feeds_refreshing": self.feeds.refreshing,
            "rate_info": [describe_rate(lv, self._search_share()) for lv in range(1, 11)],
            "hour_target": round(self.runner.hour_target) if self.runner and self.running else None,
            "opennic": [x.split("|") for x in self.feeds.items("opennic")],
        }

    def _search_share(self) -> float:
        e = self.profile.engines
        on = {n: c.budget_per_day for n, c in e.items() if c.enabled and (n != "onion" or self._tor_cfg().enabled)}
        return (on.get("search", 0) / sum(on.values())) if sum(on.values()) else 0.3

    def hits(self, after: int = 0) -> list[dict]:
        if not self.runner:
            return []
        return [h for h in self.runner.hit_log if h["seq"] > after]

    def doctor(self) -> list[dict]:
        """Self-checks with plain-language results: status ∈ ok | warn | fail | info."""
        checks: list[dict] = []

        def add(name, status, detail):
            checks.append({"name": name, "status": status, "detail": detail})

        for lib in ("requests", "yaml", "socks"):
            try:
                __import__(lib)
                add(f"Python library: {lib}", "ok", "installed")
            except ImportError:
                add(f"Python library: {lib}", "fail", "missing — reinstall noisyCricket")
        wl = next((e.wordlist for e in self.profile.engines.values() if e.enabled and e.wordlist), None)
        if wl:
            path = resolve_resource(wl)
            if Path(path).exists():
                n = sum(1 for ln in open(path, encoding="utf-8", errors="ignore") if ln.strip())
                add("Word list", "ok", f"{path} ({n:,} words)")
            else:
                add("Word list", "warn", f"{wl} not found — built-in topics will be used")
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=4).close()
            add("Internet (clearnet)", "ok", "reachable")
        except OSError as exc:
            add("Internet (clearnet)", "warn", f"cannot reach 1.1.1.1:443 ({exc})")
        cfg = self._tor_cfg()
        if not cfg.enabled:
            add("Tor", "info", "Tor is turned off (optional). Enable it in Settings or on the Tor tab.")
        else:
            binp = find_tor_binary()
            add("Tor program", "ok" if binp else "warn",
                binp or "not installed — it will be downloaded automatically on start")
            tm = self.tor_manager()
            add("Tor status", "ok" if tm.ready else "warn",
                f"{tm.state} ({tm.bootstrap_pct}%)" + (f" — {tm.error}" if tm.error else ""))
            seeds_ok, bad = clean_seed_list(cfg.onion_seeds or [u for _, u in BUILTIN_ONION_SEEDS])
            add("Onion seeds", "ok" if not bad else "fail",
                f"{len(seeds_ok)} valid v3 addresses" + (f"; {len(bad)} invalid" if bad else ""))
            if cfg.clearnet_via_tor:
                add("Clearnet over Tor", "warn",
                    "enabled — not recommended: adds load to exit relays and is a distinctive pattern")
            if port_open("127.0.0.1", 9150) or port_open("127.0.0.1", 9050):
                add("Other Tor on this PC", "info",
                    "A system Tor/Tor Browser is running. noisyCricket's own Tor uses different ports and won't touch it.")
        bi = self.browser_info()
        add("Real browser", "ok" if bi["active"] else ("warn" if bi["setting"] != "off" else "info"),
            (f"in use: {bi['detail']}" if bi["active"] else
             "turned off in Settings" if bi["setting"] == "off" else
             f"not available — {bi['detail']}. Plain HTTP is used, which looks far less human."))
        for f in self.feeds.status():
            st = "ok" if f["count"] and not f["stale"] else ("warn" if f["error"] or not f["count"] else "info")
            add(f"Source: {f['name']}", st, f"{f['count']:,} items" + (" (stale)" if f["stale"] and f["count"] else "")
                + (f" — {f['error']}" if f["error"] else ""))
        if self.profile.relay.enabled:
            add("Relay contribution", "info", f"{self.profile.relay.mode} relay '{self.profile.relay.nickname}' (never an exit)")
        add("Data folder", "ok", str(app_dir()))
        return checks
