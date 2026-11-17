"""Proof tab: traffic observer (offline, fake connection lists and clock)."""
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CRICKET_HOME", tempfile.mkdtemp(prefix="cricket-test-"))

from noisycricket.observe import TrafficObserver, ks_similarity, runs  # noqa: E402


def test_runs_and_ks():
    assert runs([True, True, False, True, False, False]) == ([2, 1], [1, 2])
    assert ks_similarity([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == 1.0
    assert ks_similarity([1] * 10, [9] * 10) == 0.0
    assert ks_similarity([1, 2], [1, 2]) is None                 # too little data to judge


def _observer(conns, own_pid, on=True, clock=None):
    t = clock or [1_700_000_000.0]
    ob = TrafficObserver(noise_on=lambda: on, noise_requests=lambda m: 2, conn_fn=lambda: conns,
                         clock=lambda: t[0], path=Path(tempfile.mkdtemp()) / "o.json")
    ob._own_pids = lambda: {own_pid}
    ob.available = True
    return ob, t


def test_connections_are_split_by_owner_and_private_ones_ignored():
    conns = [("93.184.216.34", 999), ("151.101.1.69", 42), ("192.168.1.1", 42), ("127.0.0.1", 42),
             ("104.16.1.1", 7)]
    ob, _ = _observer(conns, own_pid=42)
    ob.tor_pid = lambda: 7
    ob.learn("151.101.1.69", "www.reddit.com")
    ob.sample()
    kinds = {n["id"]: (n["kind"], n["label"]) for n in ob.nodes()}
    assert kinds == {"93.184.216.34": ("you", ""), "151.101.1.69": ("noise", "www.reddit.com"),
                     "104.16.1.1": ("tor", "104.16.1.1")}          # your own traffic is never labelled
    row = ob.timeline(0.05)[-1]
    assert row["you"] == 1 and row["noise"] == 1 and row["tor"] == 1 and row["on"]


def test_saved_file_has_counts_not_addresses():
    ob, _ = _observer([("93.184.216.34", 999)], own_pid=42)
    ob.sample()
    ob._save()
    text = ob.path.read_text()
    assert "93.184" not in text and '"you": 1' in text


def test_compare_needs_baseline_then_scores_similarity():
    rng = random.Random(1)
    ob, t = _observer([], own_pid=42)
    base = 1_700_000_000
    # 3 days of minutes: OFF on even days (your traffic), ON on odd days (noise) — same bursty rhythm
    for d in range(4):
        on = d % 2 == 1
        busy = False
        for i in range(1440):
            if rng.random() < (0.08 if not busy else 0.15):
                busy = not busy
            n = rng.randint(1, 6) if busy else 0
            ob.minutes[base + d * 86400 + i * 60] = {"you": n if not on else 0, "noise": n if on else 0,
                                                    "tor": 0, "on": on, "req": n if on else 0, "bytes": 0}
    ob.noise_requests = lambda m: ob.minutes.get(m, {}).get("req", 0)
    c = ob.compare()
    assert c["verdict"] in ("similar", "somewhat") and c["score"] >= 0.6, c
    assert {m["name"] for m in c["metrics"]} == {"Time of day", "Burst length", "Quiet gaps", "Busyness"}
    empty, _ = _observer([], own_pid=42)
    assert empty.compare()["verdict"] == "collecting"
