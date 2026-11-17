"""Local web dashboard (stdlib only). Binds to 127.0.0.1, token-protected."""
from __future__ import annotations

import json
import secrets
import socket
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import ConfigError
from .controller import Controller
from .paths import resource_root

_INDEX = None


def _index_html() -> bytes:
    global _INDEX
    if _INDEX is None:
        for cand in (Path(__file__).parent / "web" / "index.html",
                     resource_root() / "noisycricket" / "web" / "index.html"):
            if cand.exists():
                _INDEX = cand.read_bytes()
                break
        else:
            _INDEX = b"<h1>noisyCricket UI files missing</h1>"
    return _INDEX


def _load_token() -> str:
    """Per-install dashboard token, kept between launches so a bookmarked link keeps working."""
    from .paths import app_dir
    p = app_dir() / "ui_token"
    try:
        tok = p.read_text(encoding="utf-8").strip()
        if len(tok) >= 20:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(18)
    try:
        p.write_text(tok, encoding="utf-8")
        p.chmod(0o600)
    except OSError:
        pass
    return tok


class UIServer:
    def __init__(self, ctl: Controller, port: int = 8765):
        self.ctl = ctl
        self.token = _load_token()
        self.httpd = self._bind(port)
        self.port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = self._make_handler()
        self.quit_event = threading.Event()

    @staticmethod
    def _bind(port: int) -> ThreadingHTTPServer:
        for p in (port, 0):
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", p), BaseHTTPRequestHandler)
                srv.daemon_threads = True
                return srv
            except OSError:
                continue
        raise OSError("cannot open a local port for the dashboard")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def serve_forever(self) -> None:
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="webui")
        t.start()
        self.quit_event.wait()
        self.httpd.shutdown()

    def open_browser(self) -> bool:
        try:
            return webbrowser.open(self.url)
        except Exception:
            return False

    # ── request handling ───────────────────────────────────────────────────
    def _make_handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            server_version = "noisyCricket"

            def log_message(self, *a):       # silence default stderr logging
                pass

            # -- security
            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").lower()
                return host in (f"127.0.0.1:{srv.port}", f"localhost:{srv.port}")

            def _auth(self, query: dict) -> bool:
                tok = self.headers.get("X-Token") or (query.get("t") or [""])[0]
                return secrets.compare_digest(tok, srv.token)

            def _send(self, code: int, body: bytes, ctype: str = "application/json",
                      extra: dict | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj).encode())

            # -- routes
            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                if not self._host_ok():
                    return self._json({"error": "bad host"}, 403)
                if u.path == "/":
                    if not self._auth(q):
                        return self._send(403, b"<h3>Open noisyCricket using the full link it printed "
                                          b"(it includes a one-time token).</h3>", "text/html")
                    return self._send(200, _index_html(), "text/html; charset=utf-8",
                                      {"Content-Security-Policy":
                                       "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                                       "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"})
                if not self._auth(q):
                    return self._json({"error": "unauthorized"}, 401)
                ctl = srv.ctl
                try:
                    if u.path == "/api/state":
                        return self._json(ctl.state())
                    if u.path == "/api/hits":
                        return self._json({"hits": ctl.hits(int((q.get("after") or ["0"])[0]))})
                    if u.path == "/api/logs":
                        after = int((q.get("after") or ["0"])[0])
                        level = (q.get("level") or ["debug"])[0]
                        return self._json({"events": ctl.log.since(after, level)})
                    if u.path == "/api/logs.txt":
                        import time
                        lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['ts']))} "
                                 f"{e['level'].upper():5s} [{e['source']}] {e['message']}"
                                 for e in ctl.log.since(0, "debug", limit=5000)]
                        return self._send(200, "\n".join(lines).encode(), "text/plain; charset=utf-8")
                    if u.path == "/api/tools/resolvers":
                        return self._json(ctl.resolvers_check())
                    if u.path == "/api/mylist":
                        return self._json(ctl.mylist_state())
                    if u.path == "/api/useragents":
                        return self._json(ctl.ua_state())
                    if u.path == "/api/tools/speed":
                        return self._json(ctl.speed_status())
                    if u.path in ("/api/hits.json", "/api/hits.csv"):
                        rows = ctl.hits(0)
                        if u.path.endswith(".json"):
                            return self._send(200, json.dumps(rows, indent=1).encode(), "application/json",
                                              {"Content-Disposition": "attachment; filename=noisycricket-activity.json"})
                        import csv
                        import io
                        buf = io.StringIO()
                        cols = ["seq", "ts", "lane", "engine", "url", "status", "ok", "latency_ms", "note", "role", "detail", "error"]
                        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
                        w.writeheader()
                        w.writerows(rows)
                        return self._send(200, buf.getvalue().encode(), "text/csv; charset=utf-8",
                                          {"Content-Disposition": "attachment; filename=noisycricket-activity.csv"})
                    if u.path == "/api/tools/estimate":
                        return self._json(ctl.estimate())
                    if u.path == "/api/tools/privacy":
                        return self._json({"checks": ctl.privacy_check()})
                    if u.path == "/api/interests":
                        return self._json(ctl.interests_state())
                    if u.path == "/metrics":
                        if not ctl.profile.extras.metrics:
                            return self._json({"error": "metrics are off (Settings → Extras)"}, 404)
                        st = ctl.state()
                        lines = ["# noisyCricket metrics", f"noisycricket_running {int(bool(st['running']))}",
                                 f"noisycricket_bytes_today {st.get('bytes_today', 0)}"]
                        for ln in st["lanes"]:
                            lines += [f'noisycricket_hits_total{{lane="{ln["name"]}"}} {ln["hits"]}',
                                      f'noisycricket_failures_total{{lane="{ln["name"]}"}} {ln["failures"]}']
                        return self._send(200, ("\n".join(lines) + "\n").encode(), "text/plain; version=0.0.4")
                    if u.path == "/api/doctor":
                        return self._json({"checks": ctl.doctor()})
                except Exception as exc:
                    return self._json({"error": str(exc)}, 500)
                self._json({"error": "not found"}, 404)

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                if not self._host_ok() or not self._auth(q):
                    return self._json({"error": "unauthorized"}, 401)
                origin = self.headers.get("Origin")
                if origin and origin not in (f"http://127.0.0.1:{srv.port}", f"http://localhost:{srv.port}"):
                    return self._json({"error": "bad origin"}, 403)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    return self._json({"error": "invalid JSON"}, 400)
                ctl, path = srv.ctl, u.path
                try:
                    if path == "/api/start":
                        ctl.start()
                    elif path == "/api/stop":
                        ctl.stop()
                    elif path == "/api/pause":
                        ctl.pause(bool(body.get("paused", True)))
                    elif path == "/api/test":
                        ctl.test_once()
                    elif path == "/api/feeds/refresh":
                        ctl.refresh_feeds()
                    elif path.startswith("/api/tor/"):
                        return self._json(ctl.tor_action(path.rsplit("/", 1)[1]))
                    elif path == "/api/config":
                        return self._json(ctl.apply_config(body))
                    elif path == "/api/app":
                        ctl.update_app(body)
                    elif path == "/api/wizard":
                        return self._json(ctl.wizard(body))
                    elif path == "/api/mylist":
                        return self._json(ctl.mylist_save(body))
                    elif path == "/api/mylist/bookmarks":
                        return self._json(ctl.mylist_bookmarks(body.get("urls")))
                    elif path == "/api/mylist/scan":
                        return self._json(ctl.mylist_scan(body.get("days", 30)))
                    elif path == "/api/mylist/watch":
                        return self._json(ctl.mylist_watch(str(body.get("action")), body.get("hours")))
                    elif path == "/api/useragents":
                        return self._json(ctl.ua_save(body))
                    elif path == "/api/useragents/detect":
                        return self._json(ctl.ua_detect())
                    elif path == "/api/tools/speed":
                        return self._json(ctl.speed_start())
                    elif path == "/api/logs/clear":
                        ctl.log.clear()
                        return self._json({"ok": True})
                    elif path == "/api/shutdown":
                        self._json({"ok": True})
                        threading.Thread(target=srv.shutdown, daemon=True).start()
                        return
                    else:
                        return self._json({"error": "not found"}, 404)
                except ConfigError as exc:
                    return self._json({"error": str(exc)}, 400)
                except Exception as exc:
                    return self._json({"error": str(exc)}, 500)
                self._json({"ok": True})

        return H

    def shutdown(self) -> None:
        try:
            self.ctl.stop(stop_tor=True)
        finally:
            self.quit_event.set()


def run_ui(profile_path: str | None = None, port: int | None = None, open_browser: bool | None = None,
           print_fn=lambda *a: print(*a, flush=True)) -> int:
    """Start controller + dashboard; block until Quit. Returns exit code."""
    ctl = Controller(profile_path)
    srv = UIServer(ctl, port or int(ctl.app.get("port", 8765)))
    print_fn(f"\n  🦗 noisyCricket dashboard: {srv.url}\n  (keep this window open; use Quit in the page, or Ctrl+C, to stop)\n")
    ctl.log.info("app", f"Dashboard listening on 127.0.0.1:{srv.port}")
    ctl._start_feed_timer()            # fetch live query/domain sources in the background right away
    if ctl.app.get("autostart") and not ctl.first_run:
        try:
            ctl.start()
        except ConfigError as exc:
            ctl.log.error("app", str(exc))
    if open_browser if open_browser is not None else ctl.app.get("open_browser", True):
        if not srv.open_browser():
            print_fn("  Could not open a browser automatically — copy the link above into one.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop(stop_tor=True)
    return 0
