"""Transports: neutral targets dispatched over clearnet or Tor.

Engines emit Target objects; transports own connection handling, IP
diversity, and failure semantics. Tor and clearnet do NOT share code paths
beyond this interface — isolation semantics differ too much.
"""

from __future__ import annotations

import binascii
import os
import socket
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise ImportError("noisyCricket v2 needs the 'requests' package (pip install requests)") from exc

from .identity import Identity


@dataclass
class Target:
    url: str
    kind: str = "html"            # html | doh
    timeout: float = 25.0

    @property
    def host(self) -> str:
        return urllib.parse.urlparse(self.url).netloc


class HitResult:
    def __init__(self, status: int | None, html: str = "", error: str | None = None):
        self.status = status
        self.html = html
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status is not None and self.status < 400

    @property
    def dim_transport(self) -> bool:
        """429 or network error dims the lane (proxy dead / circuit burned)."""
        return self.status in (403, 429) or self.error is not None


class Transport:
    """Common interface: scheduler dispatches Targets; transports answer hits."""

    dimmed: bool = False

    def hit(self, identity, target: Target) -> HitResult:
        raise NotImplementedError


def test_proxy(proxy_url: str, test_url: str = "http://1.1.1.1", timeout: float = 2.5) -> float | None:
    """Test a proxy for connectivity and latency in milliseconds; returns None if bad/slow."""
    try:
        t0 = time.time()
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.head(test_url, proxies=proxies, timeout=timeout, allow_redirects=False)
        latency = (time.time() - t0) * 1000.0
        return latency if resp.status_code < 500 else None
    except Exception:
        return None


class ClearnetTransport(Transport):
    """Direct or proxied clearnet. Proxies are strictly optional and tested for health."""

    def __init__(self, proxy: str | None = None, proxy_pool: list[str] | None = None,
                 test_proxies: bool = False, max_latency: float = 3.0):
        self.proxy = proxy
        self.proxy_pool = list(proxy_pool) if proxy_pool else ([proxy] if proxy else [])
        self.max_latency = max_latency
        self.dimmed = False
        if test_proxies and self.proxy_pool:
            self.filter_healthy_proxies()

    def filter_healthy_proxies(self) -> list[str]:
        """Test and retain only fast, responsive proxies; discard slow or dead ones."""
        healthy = []
        for p in self.proxy_pool:
            lat = test_proxy(p, timeout=self.max_latency)
            if lat is not None:
                healthy.append(p)
        self.proxy_pool = healthy
        self.proxy = healthy[0] if healthy else None
        return healthy

    def hit(self, identity: Identity, target: Target) -> HitResult:
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        headers = _identity_headers(identity)
        if target.kind == "doh":
            headers["Accept"] = "application/dns-json"
        try:
            resp = requests.get(
                target.url,
                timeout=target.timeout,
                allow_redirects=True,
                proxies=proxies,
                # Identity headers injected here; requests also sets Host/Content-Length.
                headers=headers,
            )
            return HitResult(resp.status_code, resp.text[:2_000_000])
        except requests.RequestException as exc:
            return HitResult(None, error=str(exc))


class TorTransport(Transport):
    """Tor via a local SOCKS daemon.

    Circuit isolation: each identity gets a distinct SOCKS5 user:pass
    (user 'ident-<id>'), which the tor daemon maps to a distinct circuit.
    Local DNS resolution never happens — hostnames go to the circuit.
    """

    def __init__(self, socks: str = "127.0.0.1:9050", control: str | None = None,
                 identities_per_circuit: int = 8, control_password: str | None = None):
        host, _, port = socks.partition(":")
        self.socks_host = host or "127.0.0.1"
        self.socks_port = int(port) if port else 9050
        self.control = control
        self.identities_per_circuit = identities_per_circuit
        self.control_password = control_password or os.environ.get("CRICKET_TOR_CONTROL_PW")
        self.circuit_seed = binascii.hexlify(os.urandom(8)).decode()
        self.dimmed = False

    def _socks_proxies(self, identity: Identity) -> dict[str, str]:
        user = f"ident-{identity.id}-{self.circuit_seed}"
        uri = f"socks5h://{user}:{user}@{self.socks_host}:{self.socks_port}"
        return {"http": uri, "https": uri}

    def hit(self, identity: Identity, target: Target) -> HitResult:
        headers = _identity_headers(identity)
        if target.kind == "doh":
            headers["Accept"] = "application/dns-json"
        try:
            resp = requests.get(
                target.url,
                timeout=target.timeout,
                allow_redirects=True,
                proxies=self._socks_proxies(identity),
                headers=headers,
            )
            return HitResult(resp.status_code, resp.text[:2_000_000])
        except requests.RequestException as exc:
            return HitResult(None, error=str(exc))

    def renew_circuits(self) -> bool:
        """NEWNYM via tor control port, if configured. Best-effort."""
        if not self.control:
            return False
        host, _, port = self.control.partition(":")
        try:
            with socket.create_connection((host, int(port)), timeout=5) as sock:
                sf = sock.makefile("rw", newline="\r\n")
                sf.write("AUTHENTICATE\r\n"); sf.flush()
                resp = sf.readline()
                if "250" not in resp:
                    if self.control_password:
                        sf.write(f'AUTHENTICATE "{self.control_password}"\r\n'); sf.flush()
                        if "250" not in sf.readline():
                            return False
                    else:
                        cookie_path = os.environ.get("CRICKET_TOR_CONTROL_COOKIE")
                        if not cookie_path:
                            for default_cookie in ("/var/run/tor/control.authcookie",
                                                   os.path.expanduser("~/.tor/control_authcookie")):
                                if os.path.exists(default_cookie):
                                    cookie_path = default_cookie
                                    break
                        if cookie_path:
                            try:
                                with open(cookie_path, "rb") as cf:
                                    cookie_hex = binascii.hexlify(cf.read()).decode()
                                sf.write(f"AUTHENTICATE {cookie_hex}\r\n"); sf.flush()
                                if "250" not in sf.readline():
                                    return False
                            except OSError:
                                return False
                        else:
                            return False
                sf.write("signal NEWNYM\r\n"); sf.flush()
                ok = "250" in sf.readline()
                sf.write("QUIT\r\n"); sf.flush()
                return ok
        except OSError:
            return False

    def check_status(self) -> dict[str, Any]:
        """Inspect Tor daemon reachability and bootstrap state."""
        res: dict[str, Any] = {
            "socks_host": self.socks_host,
            "socks_port": self.socks_port,
            "socks_ok": False,
            "control_ok": False,
            "bootstrap": None,
            "version": None,
            "error": None,
        }
        try:
            with socket.create_connection((self.socks_host, self.socks_port), timeout=2):
                res["socks_ok"] = True
        except OSError as exc:
            res["error"] = f"SOCKS port unreachable: {exc}"

        if self.control:
            chost, _, cport = self.control.partition(":")
            try:
                with socket.create_connection((chost, int(cport)), timeout=2) as sock:
                    sf = sock.makefile("rw", newline="\r\n")
                    sf.write("AUTHENTICATE\r\n"); sf.flush()
                    resp = sf.readline()
                    if "250" not in resp and self.control_password:
                        sf.write(f'AUTHENTICATE "{self.control_password}"\r\n'); sf.flush()
                        resp = sf.readline()
                    if "250" in resp:
                        res["control_ok"] = True
                        sf.write("GETINFO version\r\n"); sf.flush()
                        v_line = sf.readline()
                        if "250-version=" in v_line:
                            res["version"] = v_line.partition("250-version=")[2].strip()
                        sf.write("GETINFO status/bootstrap-phase\r\n"); sf.flush()
                        b_line = sf.readline()
                        if "250-status/bootstrap-phase=" in b_line:
                            res["bootstrap"] = b_line.partition("250-status/bootstrap-phase=")[2].strip()
                        sf.write("QUIT\r\n"); sf.flush()
            except OSError:
                pass
        return res

    def reconnect(self) -> bool:
        """Reset circuit seed and request fresh circuits via NEWNYM."""
        self.circuit_seed = binascii.hexlify(os.urandom(8)).decode()
        self.dimmed = False
        return self.renew_circuits()


def _identity_headers(identity: Identity) -> dict[str, str]:
    from .identity import headers_for
    return headers_for(identity)
