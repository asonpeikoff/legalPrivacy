"""Transports: neutral targets dispatched over clearnet or Tor.

Engines emit Target objects; transports own connection handling, IP
diversity, and failure semantics. Tor and clearnet do NOT share code paths
beyond this interface — isolation semantics differ too much.
"""

from __future__ import annotations

import binascii
import os
import socket
import urllib.parse
from dataclasses import dataclass

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


class ClearnetTransport(Transport):
    """Direct or proxied clearnet. Identity pinned to one proxy for its lifetime."""

    def __init__(self, proxy: str | None = None):
        self.proxy = proxy
        self.dimmed = False

    def hit(self, identity: Identity, target: Target) -> HitResult:
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = requests.get(
                target.url,
                timeout=target.timeout,
                allow_redirects=True,
                proxies=proxies,
                # Identity headers injected here; requests also sets Host/Content-Length.
                headers=_identity_headers(identity),
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
        try:
            resp = requests.get(
                target.url,
                timeout=target.timeout,
                allow_redirects=True,
                proxies=self._socks_proxies(identity),
                headers=_identity_headers(identity),
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
                if "250" not in sf.readline():
                    sf.write(f'AUTHENTICATE "{self.control_password}"\r\n'); sf.flush()
                    if "250" not in sf.readline():
                        return False
                sf.write("signal NEWNYM\r\n"); sf.flush()
                ok = "250" in sf.readline()
                sf.write("QUIT\r\n"); sf.flush()
                return ok
        except OSError:
            return False


def _identity_headers(identity: Identity) -> dict[str, str]:
    from .identity import headers_for
    return headers_for(identity)
