"""Network checks for the Tools tab: which DNS resolvers your computer uses, a DNS
leak check, and a speed test.

Everything here is user-initiated and runs over the clearnet (never over Tor).
The speed test moves a fixed, modest amount of data (10 MB down, 2 MB up) once per
click, through the clearnet proxy if you set one, so the number reflects that route.
"""
from __future__ import annotations

import ipaddress
import os
import platform
import re
import secrets
import subprocess
import time
import urllib.request
from typing import Callable

DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
DOWN_BYTES = 10_000_000
UP_BYTES = 2_000_000
LEAK_TEST_URL = "https://www.dnsleaktest.com/"


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _valid_ips(items) -> list[str]:
    out = []
    for x in items:
        try:
            ip = str(ipaddress.ip_address(x.strip()))
        except ValueError:
            continue
        if ip not in out:
            out.append(ip)
    return out


def system_resolvers() -> dict:
    """Resolvers the operating system is configured to send lookups to."""
    system = platform.system()
    ips: list[str] = []
    note = ""
    if system == "Windows":
        out = _run(["ipconfig", "/all"])
        found = re.findall(r"DNS Servers[ .]*:\s*([0-9a-fA-F:.]+)", out)
        found += re.findall(r"^\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s*$", out, re.M)
        ips = _valid_ips(found)
    elif system == "Darwin":
        out = _run(["scutil", "--dns"])
        ips = _valid_ips(re.findall(r"nameserver\[\d+\]\s*:\s*(\S+)", out))
    else:
        try:
            with open("/etc/resolv.conf", encoding="utf-8") as fh:
                ips = _valid_ips(re.findall(r"^\s*nameserver\s+(\S+)", fh.read(), re.M))
        except OSError:
            ips = []
        if "127.0.0.53" in ips or "127.0.0.1" in ips:
            note = ("Your system uses a local stub resolver (systemd-resolved or similar). "
                    "Run `resolvectl status` to see the upstream servers it forwards to.")
    return {"system": system, "resolvers": ips, "note": note}


def dns_leak_check(secure_ips: list[str]) -> dict:
    """Which resolvers your computer uses, and whether any of them is outside the
    resolvers you chose for noise. Also times one fresh, random lookup.

    Honest limit: telling whether a lookup leaked to a third party needs a name
    server you control. This check works from your computer's own configuration, and
    the external test page below does the authoritative check.
    """
    info = system_resolvers()
    chosen = set(_valid_ips(secure_ips))
    rows = []
    for ip in info["resolvers"]:
        if ip in ("127.0.0.53", "127.0.0.1"):
            verdict = "local stub: forwards upstream"
        elif chosen and ip in chosen:
            verdict = "one of your chosen secure resolvers"
        else:
            verdict = "set by your system (not one of your chosen secure resolvers)"
        rows.append({"ip": ip, "verdict": verdict})
    name = f"{secrets.token_hex(6)}.example.com"      # random, never used, so never cached
    import socket
    t0 = time.time()
    try:
        socket.getaddrinfo(name, 443)
        answer = "resolved"
    except socket.gaierror as exc:
        answer = f"answered: no such name ({exc.strerror or 'NXDOMAIN'})"   # a real reply from a resolver
    except OSError as exc:
        answer = f"no answer: {exc}"
    lookup = {"ms": round((time.time() - t0) * 1000), "result": answer}
    return {"system": info["system"], "resolvers": rows, "note": info["note"],
            "sample_lookup": lookup, "external_test": LEAK_TEST_URL}


def _mbps(nbytes: int, seconds: float) -> float:
    return round(nbytes * 8 / max(seconds, 1e-6) / 1_000_000, 2)


def speed_test(proxy: str | None = None, progress: Callable[[str], None] = lambda s: None,
               down_bytes: int = DOWN_BYTES, up_bytes: int = UP_BYTES, timeout: float = 60) -> dict:
    """Download then upload once. Returns Mbps for each, or an error message."""
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    result: dict = {"down_mbps": None, "up_mbps": None, "errors": [], "via": proxy or "direct"}
    progress("Downloading…")
    try:
        t0 = time.time()
        got = 0
        with opener.open(DOWN_URL.format(n=down_bytes), timeout=timeout) as r:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
        result["down_mbps"] = _mbps(got, time.time() - t0)
        result["down_bytes"] = got
    except Exception as exc:  # network errors vary widely; report, don't crash
        result["errors"].append(f"download: {exc}")
    progress("Uploading…")
    try:
        payload = os.urandom(up_bytes)
        req = urllib.request.Request(UP_URL, data=payload, method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
        t0 = time.time()
        with opener.open(req, timeout=timeout) as r:
            r.read()
        result["up_mbps"] = _mbps(up_bytes, time.time() - t0)
        result["up_bytes"] = up_bytes
    except Exception as exc:
        result["errors"].append(f"upload: {exc}")
    return result
