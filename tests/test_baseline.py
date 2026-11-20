"""Baseline selection: HAR first, then noise-off minutes, then history; honest 'not enough' otherwise."""
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LEGALPRIVACY_HOME", tempfile.mkdtemp(prefix="legalprivacy-test-"))

from legalprivacy import baseline as bl  # noqa: E402
from legalprivacy.observe import TrafficObserver  # noqa: E402

T0 = 1_700_000_000 // 60 * 60


def _har(active=40, span_min=120, hosts=(("example.com", 30), ("news.org", 10))):
    rng = random.Random(2)
    mins = {T0 + m * 60: [rng.randint(1, 9), rng.randint(1, 4)] for m in rng.sample(range(span_min), active)}
    return {"entries": sum(v[0] for v in mins.values()), "first": min(mins), "last": max(mins) + 30,
            "minutes": mins, "top_hosts": [list(h) for h in hosts]}


def _obs_rows(active_off=0, active_on=0):
    rows = [{"t": T0 + i * 60, "on": False, "you": 3, "noise": 0, "tor": 0, "req": 0, "bytes": 0} for i in range(active_off)]
    rows += [{"t": T0 + 86400 + i * 60, "on": True, "you": 0, "noise": 2, "tor": 0, "req": 2, "bytes": 0} for i in range(active_on)]
    return rows


def _history(days=12, per_day=40):
    rng = random.Random(5)
    return sorted((T0 + d * 86400 + rng.uniform(8, 22) * 3600, rng.choice(["a.com", "b.org", "c.net"]))
                  for d in range(days) for _ in range(per_day))


def test_har_wins_over_everything_when_sufficient():
    sel = bl.select(_har(), _obs_rows(200), _history())
    assert sel["source"] == "har" and sel["enough"] and "from your HAR file" in sel["status"]
    assert {s["id"]: s["state"] for s in sel["sources"]} == {"har": "in use", "observer": "not needed", "history": "not needed"}


def test_observer_then_history_fallbacks():
    sel = bl.select(None, _obs_rows(120), _history())
    assert sel["source"] == "observer" and "noise off" in sel["label"]
    sel = bl.select(_har(active=5), _obs_rows(10), _history())          # tiny HAR and little observed: history
    assert sel["source"] == "history" and "days of browser history" in sel["label"]
    assert sel["sources"][0]["state"] == "short"


def test_nothing_sufficient_or_erased_history_says_not_enough_and_how_to_fix():
    for hist in (None, [], [(T0, "a.com")] * 3):
        sel = bl.select(None, _obs_rows(10), hist)
        assert sel["source"] == "none" and not sel["enough"] and sel["rows"] == []
        assert "Not enough data" in sel["status"] and "Save all as HAR" in sel["status"]
        assert "noise off" in sel["status"]
    erased = bl.select(None, [], [])
    assert [s for s in erased["sources"] if s["id"] == "history"][0]["detail"].startswith("empty, erased")


def test_span_wording():
    assert bl.human_span(2 * 3600) == "2 h" and bl.human_span(20 * 60) == "20 min" and bl.human_span(12 * 86400) == "12 days"


def test_har_events_are_site_level_and_only_use_saved_names():
    har = _har()
    ev = bl.har_events(har)
    assert ev and {s for _, s in ev} <= {"example.com", "news.org"}
    assert all(har["first"] - 60 <= t <= har["last"] + 60 for t, _ in ev)
    har["top_hosts"] = []
    assert bl.har_events(har) == []


def _observer():
    t = [1_700_000_000.0]
    return TrafficObserver(noise_on=lambda: False, noise_requests=lambda m: 0, clock=lambda: t[0],
                           path=Path(tempfile.mkdtemp()) / "o.json")


def _fill_noise(ob, days, seed=3):
    rng = random.Random(seed)
    base = T0 + 5 * 86400
    for d in range(days):
        for i in range(0, 1440):
            if 8 * 60 <= i < 22 * 60 and rng.random() < 0.5:
                n = rng.randint(1, 5)
                ob.minutes[base + d * 86400 + i * 60] = {"you": 0, "noise": n, "tor": 0, "on": True, "req": n, "bytes": 0}


def test_compare_uses_har_baseline_and_reports_the_source():
    ob = _observer()
    _fill_noise(ob, 3)
    har = _har(active=60, span_min=300)
    sel = bl.select(har, ob.rows(), None)
    c = ob.compare(sel)
    assert c["baseline"]["source"] == "har" and "rows" not in c["baseline"]
    assert c["verdict"] in ("similar", "somewhat", "different")
    # time-of-day is withheld: a few hours of recording can't be compared across the whole day
    assert [m for m in c["metrics"] if m["name"] == "Time of day"][0]["similarity"] is None


def test_compare_not_enough_baseline_says_collecting_with_status():
    ob = _observer()
    _fill_noise(ob, 3)
    sel = bl.select(None, ob.rows(), [])
    c = ob.compare(sel)
    assert c["verdict"] == "collecting" and c["baseline"]["source"] == "none"
    assert c["needed"]["baseline_active"] == 60 and "Not enough data" in c["baseline"]["status"]


def test_compare_without_any_baseline_argument_keeps_old_behaviour():
    ob = _observer()
    assert ob.compare()["verdict"] == "collecting"


def test_selftest_uses_har_then_explains_when_nothing_is_there():
    sys.path.insert(0, os.path.dirname(__file__))
    from test_controller import _ctl
    from legalprivacy.har import HarStore
    c = _ctl()
    c.har = HarStore(Path(tempfile.mkdtemp()) / "har.json")
    c._history_events = lambda: []                       # history erased
    r = c.selftest(14)
    assert "error" in r and "Save all as HAR" in r["error"] and r["baseline"]["source"] == "none"
    rng = random.Random(9)
    mins = {T0 + m * 60: [rng.randint(1, 6), rng.randint(2, 5)] for m in range(0, 360, 2)}
    c.har.save({"entries": 900, "first": T0, "last": T0 + 359 * 60, "minutes": mins,
                "top_hosts": [["example.com", 50], ["news.org", 30], ["shop.net", 20], ["wiki.io", 10]]})
    r = c.selftest(14)
    assert r["baseline"]["source"] == "har" and "HAR file" in r["baseline"]["status"]
    assert any("recording" in w for w in r.get("warnings", []))
