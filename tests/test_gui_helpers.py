"""More pure logic for the desktop app's Dashboard and Proof tabs. No display needed."""
from legalprivacy import gui_logic as L


def test_dashboard_helpers():
    now = 10_000
    hits = [{"ts": now - 5}, {"ts": now - 70}, {"ts": now - 70}, {"ts": now - 4000}]
    out = L.hits_per_minute(hits, now, 30)
    assert len(out) == 30 and out[-1] == 1 and out[-2] == 2 and sum(out) == 3
    assert L.fmt_ago(0) == "—" and L.fmt_ago(now - 30, now) == "30 s ago" and L.fmt_ago(now - 600, now) == "10 min ago"
    assert L.hit_detail({"note": "click", "detail": "scrolled"}) == "click · scrolled" and L.hit_detail({}) == ""
    assert L.hit_ms({"latency_ms": 123.4}) == "123" and L.hit_ms({}) == ""


def test_proof_helpers():
    rows = L.onoff_rows({"off": {"minutes": 10, "you": 2.5, "noise": 0, "bytes": 2048}, "on": {"minutes": 5, "req": 1.5}})
    assert rows[0] == ("", "Noise off", "Noise on") and rows[1] == ("Minutes observed", "10", "5")
    assert rows[-1] == ("All traffic: KB / min", "2", "—")
    lines = L.compare_lines({"verdict": "collecting", "needed": {"baseline_active": 5, "noise_active": 7}})
    assert "5 more busy minutes" in lines[0][0] and lines[0][2] is None
    lines = L.compare_lines({"verdict": "similar", "score": 0.82, "weakest": "Busyness", "baseline_active": 90,
                             "noise_active": 40, "metrics": [{"name": "Time of day", "similarity": 0.9},
                                                             {"name": "Busyness", "similarity": None}]})
    assert lines[0][0].startswith("82%  Looks like you") and lines[0][1] == "ok"
    assert lines[1] == (("Time of day", "90%"), "ok", 0.9) and lines[2][0] == ("Busyness", "later")
    assert L.compare_lines({})[0][0] == "No data yet."
    assert L.tor_test_text(None) == "" and "Testing" in L.tor_test_text({"running": True})
    t = L.tor_test_text({"summary": "Tor works", "exit": {"ok": True, "ip": "1.2.3.4", "ms": 90},
                         "onion": {"ok": False, "error": "timeout"}})
    assert "exit 1.2.3.4" in t and "✗ timeout" in t
    row = L.trace_row({"ts": 0, "url": "https://www.example.com/a?b", "why": "page opened"})
    assert row[1:] == ("page opened", "www.example.com")


def test_icons_are_well_formed():
    import pytest
    pytest.importorskip("tkinter")
    from legalprivacy.gui_theme import ICONS
    assert len(ICONS) >= 15
    for name, rows in ICONS.items():
        assert len(rows) == 12 and all(len(r) == 12 for r in rows), name
        assert any("#" in r for r in rows), name
