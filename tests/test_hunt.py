import os
import sys
import tempfile
import threading
import types
from unittest import mock

import pytest

from legalprivacy.config import ConfigError
from legalprivacy.controller import Controller


def _ctl():
    return Controller(profile_path=os.path.join(tempfile.mkdtemp(), "p.json"))


def test_start_waits_for_a_stopping_run_instead_of_silently_doing_nothing():
    c = _ctl()
    c.apply_config({"transports": {"clearnet": {"enabled": True, "browser": "off"}}})
    release = threading.Event()
    c._stop = threading.Event()
    c._stop.set()
    c._thread = threading.Thread(target=lambda: release.wait(5), daemon=True)
    c._thread.start()
    threading.Timer(0.3, release.set).start()
    c.start()
    try:
        assert c.running and c._thread.name == "legalprivacy-runner"
    finally:
        c.stop()


def test_start_refuses_when_old_run_never_ends():
    c = _ctl()
    c._stop = threading.Event()
    c._stop.set()
    ev = threading.Event()
    c._thread = threading.Thread(target=ev.wait, daemon=True)
    c._thread.start()
    with mock.patch.object(threading.Thread, "join", lambda self, timeout=None: None):
        with pytest.raises(ConfigError):
            c.start()
    ev.set()


def test_failed_browser_launch_stops_playwright_driver():
    from legalprivacy import browser as b
    stopped = []

    class PW:
        class firefox:
            @staticmethod
            def launch(**k):
                raise RuntimeError("no browser")

        def stop(self):
            stopped.append(1)

    class Ctx:
        def start(self):
            return PW()

    mod = types.ModuleType("playwright.sync_api")
    mod.sync_playwright = lambda: Ctx()
    with mock.patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"), "playwright.sync_api": mod}):
        t = b.BrowserTransport(engine="firefox")
        for _ in range(3):
            with pytest.raises(RuntimeError):
                t._ensure()
    assert len(stopped) == 3 and t._pw is None


def test_second_launch_reuses_running_dashboard(monkeypatch):
    from legalprivacy import webui
    d = tempfile.mkdtemp()
    monkeypatch.setenv("LEGALPRIVACY_HOME", d)
    c = Controller(profile_path=os.path.join(d, "p.json"))
    srv = webui.UIServer(c, 0)
    threading.Thread(target=srv.httpd.serve_forever, daemon=True).start()
    try:
        url = webui.running_instance(srv.port)
        assert url and f":{srv.port}/?t={srv.token}" in url
        opened = []
        monkeypatch.setattr(webui.webbrowser, "open", lambda u: opened.append(u))
        out = []
        assert webui.run_ui(os.path.join(d, "p.json"), srv.port, True, print_fn=lambda *a: out.append(a)) == 0
        assert opened == [url]
    finally:
        srv.httpd.shutdown()
    assert webui.running_instance(srv.port) is None
