"""Tor/onion tests — all offline (fake control server, injected probes)."""
import os
import socket
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CRICKET_HOME", tempfile.mkdtemp(prefix="cricket-test-"))

from noisycricket.config import (Profile, apply_overrides, load_profile,  # noqa: E402
                                 profile_to_dict, save_profile)
from noisycricket.onion import (BUILTIN_ONION_SEEDS, OnionTargetFactory,  # noqa: E402
                                clean_seed_list, is_onion_url, is_valid_v3_onion)
from noisycricket.scheduler import Runner, build_lanes  # noqa: E402
from noisycricket.transports import HitResult, TOR_BROWSER_HEADERS  # noqa: E402
from noisycricket.torcontrol import TorControl  # noqa: E402

WORDS = ["weather", "news", "books"]
GOOD = BUILTIN_ONION_SEEDS[0][1]


def test_builtin_seeds_all_valid_v3():
    assert BUILTIN_ONION_SEEDS
    assert all(is_onion_url(u) for _, u in BUILTIN_ONION_SEEDS)


def test_builtin_seeds_unique_and_lawful_only():
    urls = [u for _, u in BUILTIN_ONION_SEEDS]
    assert len(urls) == len(set(urls)) >= 15
    banned = ("market", "drug", "dread", "exploit", "porn", "pirate", "chan", "recon", "leak", "cia", "fbi", "police", "ncide", "gov")
    assert not any(b in n.lower() for n, _ in BUILTIN_ONION_SEEDS for b in banned)


def test_onion_checksum_rejects_typos_and_old_v2():
    host = GOOD.split("//")[1].rstrip("/")
    assert is_valid_v3_onion(host)
    flipped = host[:10] + ("a" if host[10] != "a" else "b") + host[11:]
    assert not is_valid_v3_onion(flipped)
    assert not is_valid_v3_onion("expyuzz4wqqyqhjn.onion")      # v2-length
    assert not is_valid_v3_onion("example.com")


def test_clean_seed_list_splits_good_and_bad():
    good, bad = clean_seed_list([GOOD, "http://nope.onion", "# c", "", "example.com"])
    assert good == [GOOD] and len(bad) == 2


def test_onion_walk_never_leaves_onion_space():
    import random
    f = OnionTargetFactory(rng=random.Random(1))
    html = f'<a href="https://evil.example/x">c</a><a href="{BUILTIN_ONION_SEEDS[1][1]}a">o</a>'
    for _ in range(20):
        nxt = f.next_from(html, GOOD)
        assert nxt is not None and is_onion_url(nxt.url)
    assert f.next_from('<a href="https://evil.example/">c</a>', GOOD) is None


def test_tor_is_off_by_default_and_clearnet_over_tor_off():
    p = Profile(name="d")
    tor = p.transports["tor"]
    assert tor.enabled is False and tor.onion is True and tor.clearnet_via_tor is False
    assert p.active_engines("tor") == ["onion"]          # even when later enabled
    assert "onion" not in p.active_engines("clearnet")
    assert build_lanes(p, WORDS) and [l.name for l in build_lanes(p, WORDS)] == ["clearnet"]


def test_enabling_tor_adds_onion_lane_only():
    p = Profile(name="d")
    apply_overrides(p, {"transports": {"tor": {"enabled": True}}})
    lanes = {l.name: l for l in build_lanes(p, WORDS)}
    assert lanes["tor"].engines == ["onion"]
    assert lanes["clearnet"].budgets["search"].per_day == 120      # not halved: nothing shared


def test_clearnet_via_tor_opt_in_splits_budget():
    p = Profile(name="d")
    apply_overrides(p, {"transports": {"tor": {"enabled": True, "clearnet_via_tor": True}}})
    lanes = {l.name: l for l in build_lanes(p, WORDS)}
    assert set(lanes["tor"].engines) == {"search", "crawl", "dns", "onion"}
    assert lanes["clearnet"].budgets["search"].per_day == 60
    assert lanes["tor"].budgets["onion"].per_day == 40             # onion never split


def test_profile_roundtrip(tmp_path=None):
    p = Profile(name="rt")
    apply_overrides(p, {"transports": {"tor": {"enabled": True, "bridges": ["obfs4 1.2.3.4:1 AA cert=x"]}}})
    path = os.path.join(tempfile.mkdtemp(), "c.yaml")
    save_profile(p, path)
    q = load_profile(path)
    assert q.transports["tor"].enabled and q.transports["tor"].bridges == ["obfs4 1.2.3.4:1 AA cert=x"]
    assert profile_to_dict(q) == profile_to_dict(p)


def _run_onion_runner(gate_open):
    p = Profile(name="t")
    apply_overrides(p, {"transports": {"clearnet": {"enabled": False}, "tor": {"enabled": True}}})
    seen = []

    def probe(lane, ident, target):
        seen.append((lane.name, target.url))
        return HitResult(200, '<a href="%s">x</a>' % BUILTIN_ONION_SEEDS[2][1])

    lanes = build_lanes(p, WORDS, tor_gate=lambda: gate_open[0])
    r = Runner(profile=p, lanes=lanes, wordlist=WORDS, clock=lambda: 0.0, local_hour_fn=lambda: 12.0,
               sleep_fn=lambda s: None, probe_fn=probe, max_iterations=50)
    return r, seen


def test_tor_lane_waits_for_gate_then_runs_onion_only():
    gate = [False]
    r, seen = _run_onion_runner(gate)
    assert r.run_once(wait=False) is False and not seen      # Tor not ready → idle, no leak
    gate[0] = True
    for _ in range(10):
        r.run_once(wait=False)
    assert seen and all(is_onion_url(u) for _, u in seen) and {l for l, _ in seen} == {"tor"}
    assert r.stats()["lanes"][0]["hits"] == len(seen)
    assert r.hit_log and r.hit_log[-1]["engine"] == "onion"


def test_onion_failures_dont_trip_clearnet_failure_cap():
    p = Profile(name="t")
    apply_overrides(p, {"daily_max_failures": 3, "transports": {"clearnet": {"enabled": False},
                                                                "tor": {"enabled": True}}})
    lanes = build_lanes(p, WORDS, tor_gate=lambda: True)
    r = Runner(profile=p, lanes=lanes, wordlist=WORDS, clock=lambda: 0.0, local_hour_fn=lambda: 12.0,
               sleep_fn=lambda s: None, probe_fn=lambda *a: HitResult(None, error="timeout"))
    for _ in range(8):          # 8 > 3 would have raised for a clearnet engine
        r.run_once(wait=False)


def test_tor_browser_headers_are_uniform():
    assert "Firefox" in TOR_BROWSER_HEADERS["User-Agent"] and "Chrome" not in TOR_BROWSER_HEADERS["User-Agent"]


# ── control port against a fake tor ─────────────────────────────────────────
def _fake_tor(handler_lines):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = []

    def serve():
        c, _ = srv.accept()
        f = c.makefile("rw", newline="\r\n")
        for line in iter(f.readline, ""):
            line = line.rstrip("\r\n")
            received.append(line)
            for out in handler_lines(line):
                f.write(out + "\r\n")
            f.flush()
            if line == "QUIT":
                break
        c.close()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return port, received


def test_control_null_auth_getinfo_newnym_and_circuits():
    def h(line):
        if line.startswith("PROTOCOLINFO"):
            return ["250-PROTOCOLINFO 1", "250-AUTH METHODS=NULL", "250 OK"]
        if line == "AUTHENTICATE":
            return ["250 OK"]
        if line == "GETINFO version":
            return ["250-version=0.4.9.99", "250 OK"]
        if line == "GETINFO circuit-status":
            return ["250+circuit-status=", "5 BUILT $AAAA~guard,$BBBB~mid,$CCCC~exit BUILD_FLAGS=NEED_CAPACITY PURPOSE=GENERAL",
                    "7 BUILT $AAAA~guard,$BBBB~mid PURPOSE=HS_CLIENT_REND", ".", "250 OK"]
        if line == "SIGNAL NEWNYM":
            return ["250 OK"]
        return ["250 OK"]
    port, rec = _fake_tor(h)
    with TorControl("127.0.0.1", port) as c:
        assert c.getinfo("version") == "0.4.9.99"
        circ = c.circuits()
        assert circ[0]["path"] == ["guard", "mid", "exit"] and circ[1]["purpose"] == "HS_CLIENT_REND"
        c.newnym()
    assert "SIGNAL NEWNYM" in rec


def test_control_cookie_auth():
    cookie = tempfile.NamedTemporaryFile(delete=False)
    cookie.write(b"\x01\x02\x03" * 10)
    cookie.close()

    def h(line):
        if line.startswith("PROTOCOLINFO"):
            return ["250-PROTOCOLINFO 1", f'250-AUTH METHODS=COOKIE,SAFECOOKIE COOKIEFILE="{cookie.name}"', "250 OK"]
        if line.startswith("AUTHENTICATE "):
            return ["250 OK"] if line.split()[1] == ("010203" * 10) else ["515 bad"]
        return ["250 OK"]
    port, _ = _fake_tor(h)
    with TorControl("127.0.0.1", port):
        pass
    os.unlink(cookie.name)


def test_tor_patience_retries_once_then_new_identity_and_routine_circuits():
    p = Profile(name="t")
    apply_overrides(p, {"transports": {"clearnet": {"enabled": False}, "tor": {"enabled": True}}})
    clock = [1000.0]
    calls = []
    lanes = build_lanes(p, WORDS, tor_gate=lambda: True)
    lane = lanes[0]
    lane.transport.reconnect = lambda: calls.append("newnym") or True
    lane.transport.renew_circuits = lambda: calls.append("renew") or True
    seen = []

    def probe(l, ident, target):
        seen.append(target.url)
        return HitResult(None, error="0x01: General SOCKS server failure")

    r = Runner(profile=p, lanes=lanes, wordlist=WORDS, clock=lambda: clock[0], local_hour_fn=lambda: 12.0,
               sleep_fn=lambda s: None, probe_fn=probe)
    r.run_once(wait=False)
    first = seen[0]
    clock[0] += 150                                 # a couple of minutes later
    r.run_once(wait=False)
    assert seen[1] == first                          # the failed page is reloaded once
    r.run_once(wait=False)
    assert "newnym" in calls                         # three failures in a row: new identity
    clock[0] += 3000                                 # later on, circuits change by themselves
    lane.transport.reconnect = lambda: True
    r._tor_state["streak"] = 0
    probe_ok = lambda l, i, t: HitResult(200, "<html></html>")
    r.probe_fn = probe_ok
    r.run_once(wait=False)
    assert "renew" in calls
