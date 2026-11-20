import tempfile, os, time
from legalprivacy.controller import Controller


def test_reapply_keeps_log_and_running_state():
    d = tempfile.mkdtemp()
    c = Controller(profile_path=os.path.join(d, "p.json"))
    c.apply_config({"transports": {"clearnet": {"enabled": True, "browser": "off"}}})
    c.start()
    try:
        c.log.info("t", "marker")
        c.apply_config({"traffic": {"mobile_share": 0.3}})
        c.reapply()
        assert c.running
        assert any(e["message"] == "marker" for e in c.log.since(0))     # the log survives an apply
        assert c.app.get("clear_logs_on_restart", True) is True       # setting restored
    finally:
        c.stop()


def test_reapply_when_stopped_is_a_noop():
    c = Controller(profile_path=os.path.join(tempfile.mkdtemp(), "p.json"))
    c.reapply()
    assert not c.running


def test_start_reason_is_logged_and_repeated_starts_are_called_out():
    d = tempfile.mkdtemp()
    c = Controller(profile_path=os.path.join(d, "p.json"))
    c.apply_config({"transports": {"clearnet": {"enabled": True, "browser": "off"}}})
    c.start(reason="autostart on launch")
    try:
        assert any("autostart on launch" in e["message"] for e in c.log.since(0))
        for _ in range(5):
            c.reapply()
        assert any("started" in e["message"] and "times in the last minute" in e["message"] for e in c.log.since(0))
    finally:
        c.stop()
