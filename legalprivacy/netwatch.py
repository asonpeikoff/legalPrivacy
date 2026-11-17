"""How busy is this computer's connection? (optional; ideas from NoisyCompany and DNoiSe)

Reads the operating system's own byte counters for all network interfaces (loopback
excluded) and turns them into a rate. Nothing is captured or inspected: only totals.

Modes (Settings → Extras → Your own activity):
  off      ignore it (default)
  backoff  pause noise while you are using the connection heavily (above a threshold)
  follow   make noise mainly while you are active, so it blends into your real activity:
           while the connection has been idle for 10+ minutes, most noise is skipped
"""
from __future__ import annotations

import platform
import re
import subprocess
import time


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def total_bytes() -> int | None:
    """Received + sent bytes on all non-loopback interfaces since boot, or None if unknown."""
    system = platform.system()
    try:
        if system == "Linux":
            total = 0
            with open("/proc/net/dev", encoding="utf-8") as fh:
                for line in fh.readlines()[2:]:
                    name, _, data = line.partition(":")
                    if name.strip() == "lo":
                        continue
                    f = data.split()
                    total += int(f[0]) + int(f[8])
            return total
        if system == "Darwin":
            seen, total = set(), 0
            for line in _run(["netstat", "-ibn"]).splitlines()[1:]:
                f = line.split()
                if len(f) >= 10 and f[0] not in seen and not f[0].startswith("lo") and f[6].isdigit():
                    seen.add(f[0])
                    total += int(f[6]) + int(f[9])
            return total or None
        if system == "Windows":
            m = re.search(r"Bytes\s+(\d+)\s+(\d+)", _run(["netstat", "-e"]))
            return int(m.group(1)) + int(m.group(2)) if m else None
    except (OSError, ValueError, IndexError):
        return None
    return None


class NetWatch:
    """Your own traffic rate, minus what legalPrivacy itself moved."""

    def __init__(self, reader=total_bytes, clock=time.monotonic):
        self.reader, self.clock = reader, clock
        self._last: tuple[float, int] | None = None
        self.rate_kbps = 0.0
        self.last_active = clock()

    def sample(self, own_bytes_since_last: int = 0) -> float | None:
        now, total = self.clock(), self.reader()
        if total is None:
            return None
        if self._last is not None:
            dt = max(0.5, now - self._last[0])
            mine = max(0, total - self._last[1] - own_bytes_since_last)
            self.rate_kbps = mine * 8 / 1000 / dt
            if self.rate_kbps > 20:
                self.last_active = now
        self._last = (now, total)
        return self.rate_kbps

    def idle_for(self) -> float:
        return self.clock() - self.last_active
