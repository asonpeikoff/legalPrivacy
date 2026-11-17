"""Controller + web UI API tests (offline; no Tor, no network)."""
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CRICKET_HOME", tempfile.mkdtemp(prefix="cricket-test-"))

from noisycricket.config import ConfigError  # noqa: E402
from noisycricket.controller import Controller  # noqa: E402
from noisycricket.webui import UIServer  # noqa: E402

GOOD = "http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/"


def _ctl():
    return Controller(os.path.join(tempfile.mkdtemp(), "config.yaml"))


def test_wizard_three_answers_persist_and_tor_defaults_off():
    c = _ctl()
    assert c.first_run
    st = c.wizard({"rate_mode": "random", "level": 3, "tor": "off", "autostart": True})
    assert not st["first_run"] and st["profile"]["rate"] == {"mode": "random", "level": 3.0}
    assert st["profile"]["transports"]["tor"]["enabled"] is False
    assert st["profile"]["transports"]["tor"]["clearnet_via_tor"] is False
    c2 = Controller(c.profile_path)                       # reload from disk
    assert c2.app["autostart"] is True and not c2.first_run


def test_wizard_onion_choice_never_enables_clearnet_over_tor():
    c = _ctl()
    st = c.wizard({"level": 5, "tor": "onion"})
    tor = st["profile"]["transports"]["tor"]
    assert tor["enabled"] and tor["onion"] and not tor["clearnet_via_tor"]


def test_apply_config_validates_onion_seeds_and_bridges():
    c = _ctl()
    try:
        c.apply_config({"transports": {"tor": {"onion_seeds": "http://bogus.onion"}}})
        raise AssertionError("expected ConfigError")
    except ConfigError:
        pass
    st = c.apply_config({"transports": {"tor": {"onion_seeds": GOOD, "bridges": "obfs4 1.1.1.1:1 AA\n\n"}}})
    assert st["profile"]["transports"]["tor"]["onion_seeds"] == [GOOD]
    assert st["profile"]["transports"]["tor"]["bridges"] == ["obfs4 1.1.1.1:1 AA"]


def test_start_stop_with_nothing_enabled_errors():
    c = _ctl()
    c.apply_config({"transports": {"clearnet": {"enabled": False}}})
    try:
        c.start()
        raise AssertionError("expected ConfigError")
    except ConfigError:
        pass


def _call(url, token, path, body=None, headers=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Token": token, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_web_api_requires_token_and_serves_state():
    import threading
    c = _ctl()
    srv = UIServer(c, port=0)
    threading.Thread(target=srv.httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        assert _call(base, "wrong", "/api/state")[0] == 401
        code, st = _call(base, srv.token, "/api/state")
        assert code == 200 and st["first_run"] and st["tor"]["enabled"] is False
        assert _call(base, srv.token, "/api/doctor")[0] == 200
        assert _call(base, srv.token, "/api/config", {"daily_max_failures": 5})[1]["profile"]["daily_max_failures"] == 5
        assert _call(base, srv.token, "/api/tor/bogus", {})[0] == 400
        # cross-site POST rejected
        assert _call(base, srv.token, "/api/stop", {}, {"Origin": "http://evil.example"})[0] == 403
        # DNS-rebinding style Host header rejected
        assert _call(base, srv.token, "/api/state", None, {"Host": "evil.example"})[0] == 403
        # page itself needs the token in the URL
        try:
            urllib.request.urlopen(base + "/", timeout=5)
            raise AssertionError("index served without token")
        except urllib.error.HTTPError as e:
            assert e.code == 403
        with urllib.request.urlopen(base + "/?t=" + srv.token, timeout=5) as r:
            assert b"noisyCricket" in r.read()
    finally:
        srv.httpd.shutdown()
