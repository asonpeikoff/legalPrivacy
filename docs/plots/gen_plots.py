"""Generate ASCII .txt plots for the noisyCricket README.

All data comes from the real noisycricket package (seeded, offline).
Run from repo root: python3 docs/plots/gen_plots.py
"""

from __future__ import annotations
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from noisycricket.pacing import Pacer, diurnal_factor  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))
WIDTH = 58
PaceNames = {"fast": "0.6x", "normal": "1.0x", "slow": "1.8x"}


def hbar(value: float, max_value: float, width: int = WIDTH, char: str = "#") -> str:
    n = int(round(value / max_value * width)) if max_value else 0
    return char * n


def diurnal_plot() -> str:
    lines = [
        "noisyCricket diurnal activity model",
        "activity weight by local hour (scheduler divides hit delays by this)",
        "",
    ]
    maxf = max(diurnal_factor(float(h)) for h in range(24))
    for h in range(24):
        f = diurnal_factor(float(h))
        peak = "  <-- peak" if f == maxf else ""
        lines.append(f"{h:02d}:00 |{hbar(f, maxf):<{WIDTH}}| {f:.2f}{peak}")
    return "\n".join(lines) + "\n"


def delay_distribution_plot() -> str:
    lines = [
        "inter-hit delay distribution (Pacer, 4000 samples per pace persona)",
        "seconds between hits; lognormal body, burst-suppressed at min_gap",
        "",
    ]
    buckets = [(0, 5), (5, 10), (10, 15), (15, 25), (25, 40),
               (40, 65), (65, 100), (100, 160), (160, 250), (250, 401)]
    labels = [f"{lo:>3}-{hi - 1:<3}" for lo, hi in buckets]

    data = {}
    for pace, mult in [("fast", 0.6), ("normal", 1.0), ("slow", 1.8)]:
        p = Pacer(random.Random(7), pace_multiplier=mult)
        counts = [0] * len(buckets)
        for _ in range(4000):
            d = p.next_delay(now_local_hour=14.0)
            for i, (lo, hi) in enumerate(buckets):
                if lo <= d < hi:
                    counts[i] += 1
                    break
        data[pace] = counts

    overall_max = max(max(c) for c in data.values())
    for pace in ("fast", "normal", "slow"):
        lines.append(f"  {pace} pace ({PaceNames[pace]}):")
        for lab, c in zip(labels, data[pace]):
            lines.append(f"    {lab}s |{hbar(c, overall_max, WIDTH - 10):<{WIDTH - 10}}| {c:5d}")
        lines.append("")
    return "\n".join(lines) + "\n"


def daily_timeline_plot() -> str:
    lines = [
        "simulated day: one profile, both lanes, 360 hits/day total budget",
        "each bar = expected hits that local hour ('#'+clearnet, 'c'+tor lane)",
        "",
    ]
    hour_w = [diurnal_factor(float(h)) for h in range(24)]
    total = sum(hour_w)
    hits_hours = [360 * w / total for w in hour_w]
    maxh = max(hits_hours)
    for h in range(24):
        n = hits_hours[h]
        nc = n * 0.5
        nt = n - nc
        bar = hbar(nc, maxh) + hbar(nt, maxh, char="c")
        lines.append(f"{h:02d}:00 |{bar:{WIDTH}}| {n:5.1f}")
    return "\n".join(lines) + "\n"


ARCHITECTURE = r"""
noisyCricket v2 - one scheduler, two transports, three engines

              ----------------------------
              |        cricket CLI       |
              |   run / doctor / relay   |
              ----------------------------
                        |
                ------------------
                |    Scheduler   |   budgets (360/day)
                |    (Runner)    |-------------.
                ------------------             |
                 |            |            diurnal curve
        identity lifetime  identity lifetime  (peaks 20-21h)
        (pinned per proxy) (pinned per circuit)
                 |            |
        ----------------   ----------------
        |   Identity   |   |   Identity   |    pinned: UA + low-entropy
        | (fingerprint)|   | (fingerprint)|    client hints + language
        ----------------   ----------------    + pace + query taste
                 |            |
     lane: CLEARNET      lane: TOR
     ----------------    ------------------
     |  Clearnet     |    |  TorTransport |   SOCKS5h user:pass per
     |  Transport    |    |               |--- identity => 1 circuit
     |  direct /     |    |  127.0.0.1:9050   remote DNS only
     |  proxy pool   |    |  control:9051 |   (NEWNYM on rotation)
     ----------------    ------------------
         |         |          |
         v         v          v
    _______________________________________
   |               ENGINES                 |    <- emit neutral Targets
   |   search         crawl        dns     |       only; no transport
   |   queries        same-site     udp    |       knowledge here
   |   (7 endpoints)  link walk     / DoH  |
   |_______________________________________|
"""

with open(f"{OUT}/diurnal-activity.txt", "w") as fh:
    fh.write(diurnal_plot())
with open(f"{OUT}/inter-hit-distribution.txt", "w") as fh:
    fh.write(delay_distribution_plot())
with open(f"{OUT}/daily-timeline.txt", "w") as fh:
    fh.write(daily_timeline_plot())
with open(f"{OUT}/architecture.txt", "w") as fh:
    fh.write(ARCHITECTURE.lstrip("\n"))
print("wrote 4 plots to", OUT)
