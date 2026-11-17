import os, time
from noisycricket import browser as B
from noisycricket.config import TransportConfig


def _bt(tmp_path, monkeypatch, days):
    monkeypatch.setattr("noisycricket.paths.app_dir", lambda: tmp_path)
    t = B.BrowserTransport.__new__(B.BrowserTransport)
    t.cookie_days, t.engine, t.on_event = days, "chromium", lambda *a: None
    return t


def test_default_keeps_a_week():
    assert TransportConfig().cookie_days == 7


def test_jar_kept_then_wiped(tmp_path, monkeypatch):
    t = _bt(tmp_path, monkeypatch, 7)
    jar = t._jar(False); jar.parent.mkdir(parents=True); jar.write_text("{}")
    B._born_file(jar).write_text(str(int(time.time()) - 86400))
    assert t._load_jar(False) == str(jar)                       # 1 day old: reused
    B._born_file(jar).write_text(str(int(time.time()) - 8 * 86400))
    assert t._load_jar(False) is None and not jar.exists()     # 8 days old: wiped


def test_zero_never_keeps(tmp_path, monkeypatch):
    t = _bt(tmp_path, monkeypatch, 0)
    assert t._load_jar(False) is None
