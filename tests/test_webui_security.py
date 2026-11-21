"""Dashboard hardening: odd tokens, bad numbers, oversized bodies, error text, headers (offline)."""
import http.client
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LEGALPRIVACY_HOME", tempfile.mkdtemp(prefix="legalprivacy-test-"))

from legalprivacy.controller import Controller  # noqa: E402
from legalprivacy.webui import UIServer, _load_token, _public_error  # noqa: E402


def _serve():
    c = Controller(os.path.join(tempfile.mkdtemp(), "config.yaml"))
    srv = UIServer(c, port=0)
    threading.Thread(target=srv.httpd.serve_forever, daemon=True).start()
    return c, srv


def _req(srv, method, path, body=None, headers=None, token=True):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    h = {"X-Token": srv.token} if token else {}
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    out = (r.status, dict(r.getheaders()), data)
    conn.close()
    return out


def test_non_ascii_token_is_refused_not_a_crash():
    _, srv = _serve()
    try:
        status, _, _ = _req(srv, "GET", "/api/state", headers={"X-Token": "café"})
        assert status == 401
        assert _req(srv, "GET", "/api/state")[0] == 200            # the server is still alive
    finally:
        srv.httpd.shutdown()


def test_bad_numbers_in_the_query_are_a_400_not_a_500():
    _, srv = _serve()
    try:
        for path in ("/api/logs?after=abc", "/api/hits?after=x", "/api/tools/selftest?days=nope",
                     "/api/ml/predict?site=a.com&hour=zz"):
            status, _, body = _req(srv, "GET", path)
            assert status == 400, path
            assert "Traceback" not in body.decode()
    finally:
        srv.httpd.shutdown()


def test_internal_errors_are_logged_and_not_echoed_to_the_browser():
    c, srv = _serve()

    def boom():
        raise KeyError("/home/someone/.legalprivacy/secret-path")
    c.state = boom
    try:
        status, _, body = _req(srv, "GET", "/api/state")
        assert status == 500
        text = body.decode()
        assert "secret-path" not in text and "Traceback" not in text and "KeyError" in text
        assert any("secret-path" in e["message"] for e in c.log.since(0, "error"))   # details stay in the log
    finally:
        srv.httpd.shutdown()
    assert _public_error(RuntimeError("Tor is not installed.")) == "Tor is not installed."


def test_post_body_limits_and_invalid_json():
    _, srv = _serve()
    try:
        assert _req(srv, "POST", "/api/config", body=b"{not json")[0] == 400
        assert _req(srv, "POST", "/api/config", body=b"[1,2]")[0] == 400
        assert _req(srv, "POST", "/api/config", headers={"Content-Length": "2000000"})[0] == 413
        assert _req(srv, "POST", "/api/har/import", headers={"Content-Length": str(60 * 1024 * 1024)})[0] == 413
    finally:
        srv.httpd.shutdown()


def test_page_headers_lock_down_the_dashboard():
    _, srv = _serve()
    try:
        status, hdr, _ = _req(srv, "GET", "/?t=" + srv.token, token=False)
        assert status == 200
        csp = hdr["Content-Security-Policy"]
        for part in ("default-src 'self'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'none'"):
            assert part in csp
        assert hdr["X-Content-Type-Options"] == "nosniff" and hdr["Referrer-Policy"] == "no-referrer"
        assert hdr["Cache-Control"] == "no-store"
    finally:
        srv.httpd.shutdown()


def test_token_file_is_private():
    if os.name == "nt":
        return
    from legalprivacy.paths import app_dir
    _load_token()
    assert (app_dir() / "ui_token").stat().st_mode & 0o777 == 0o600


def test_saved_settings_and_new_data_folder_are_owner_only():
    if os.name == "nt":
        return
    import legalprivacy.paths as paths
    c = Controller(os.path.join(tempfile.mkdtemp(), "config.yaml"))
    c.apply_config({"daily_max_failures": 7})
    assert os.stat(c.profile_path).st_mode & 0o777 == 0o600
    old = os.environ.get("LEGALPRIVACY_HOME")
    os.environ["LEGALPRIVACY_HOME"] = os.path.join(tempfile.mkdtemp(), "newhome")
    try:
        assert paths.app_dir().stat().st_mode & 0o777 == 0o700
    finally:
        os.environ["LEGALPRIVACY_HOME"] = old
