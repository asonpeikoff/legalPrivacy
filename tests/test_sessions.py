"""Human session model: sessions, breaks, themed browsing (offline, simulated clock)."""
import os
import random
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CRICKET_HOME", tempfile.mkdtemp(prefix="cricket-test-"))

from noisycricket.config import Profile, apply_overrides  # noqa: E402
from noisycricket.scheduler import Runner, build_lanes  # noqa: E402
from noisycricket.sessions import SessionPlanner, active_minutes, related_query  # noqa: E402
from noisycricket.transports import HitResult  # noqa: E402

SERP = ('<html><title>results</title><a href="/l/?uddg=https%3A%2F%2Fgarden.example.org%2Fdrip">'
        'Best drip irrigation for raised beds</a><a href="/l/?uddg=https%3A%2F%2Fwww.yard.example.com%2Fguide">'
        'Raised bed watering guide for beginners</a></html>')
ARTICLE = ('<html><title>Ultimate drip irrigation for raised beds - Garden HQ</title><h1>Drip irrigation</h1>'
           '<a href="/related/soaker-hoses">Soaker hoses compared</a><a href="/login">Log in</a></html>')


def test_first_activity_starts_within_seconds():
    sp = SessionPlanner(random.Random(1))
    first = sp.next_block(12, None)
    assert first.kind == "break" and first.seconds <= 15
    assert sp.next_block(12, "break").kind == "session"


def test_steady_mode_hits_its_share_of_active_time():
    sp = SessionPlanner(random.Random(2), mode="steady", level=5)
    sp.next_block(12, None)
    active = idle = 0.0
    last = "break"
    for _ in range(400):
        b = sp.next_block(12, last)
        active += b.seconds if b.kind == "session" else 0
        idle += b.seconds if b.kind == "break" else 0
        last = b.kind
    share = active / (active + idle)
    assert abs(share - active_minutes(5) / 60) < 0.05


def test_sessions_last_minutes_and_breaks_vary():
    sp = SessionPlanner(random.Random(3), mode="random", level=5)
    sp.next_block(20, None)
    blocks, last = [], "break"
    for _ in range(200):
        b = sp.next_block(20, last)
        blocks.append(b)
        last = b.kind
    sess = [b.seconds for b in blocks if b.kind == "session"]
    brk = [b.seconds for b in blocks if b.kind == "break"]
    assert min(sess) >= 180 and max(sess) <= 1800
    assert max(brk) > 3 * min(brk)                           # not a metronome


def test_reading_time_is_long_for_articles_short_for_results():
    sp = SessionPlanner(random.Random(4))
    arts = sorted(sp.dwell("page") for _ in range(500))
    serps = sorted(sp.dwell("search") for _ in range(500))
    assert arts[250] > 25 and serps[250] < 12


def test_related_query_comes_from_the_page_not_generic_titles():
    assert related_query(random.Random(1), ARTICLE, "drip irrigation") == \
        "ultimate drip irrigation for raised beds"
    assert related_query(random.Random(1), "<title>Help Center - Instagram</title>", "x") is None


def test_simulated_session_searches_clicks_reads_and_stays_on_topic():
    p = Profile(name="t")
    apply_overrides(p, {"engines": {"crawl": {"enabled": True}, "dns": {"enabled": False}}})
    calls = []
    clock = [0.0]
    stop = threading.Event()

    def sleep(s):
        clock[0] += s
        if clock[0] > 3 * 3600:
            stop.set()

    def probe(lane, ident, target):
        calls.append((target.role, target.url, target.referer, target.dwell))
        return HitResult(200, SERP if target.role == "search" else ARTICLE)

    lanes = build_lanes(p, ["w"])
    r = Runner(profile=p, lanes=lanes, wordlist=["w"], clock=lambda: clock[0], sleep_fn=sleep,
               probe_fn=probe, stop_event=stop, sessions=SessionPlanner(random.Random(5), level=6))
    r.rng = random.Random(5)
    r.run_sessions()
    roles = [c[0] for c in calls]
    assert "search" in roles and "result" in roles
    i = roles.index("result")
    assert calls[i][2] and calls[i][2] == calls[i - 1][1]            # clicked from the results page
    assert any("ultimate+drip+irrigation" in c[1] for c in calls)    # searched something it just read about
    assert all(c[3] for c in calls)                                  # every page had a planned reading time
    assert r.session_no >= 3                                         # several sessions with breaks between
    assert not any("/login" in c[1] for c in calls)
