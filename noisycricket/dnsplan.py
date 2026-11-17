"""Which DNS resolver each noise lookup goes to.

  system  (recommended) your computer's normal resolver: whatever your router, ISP,
          VPN or OS-level encrypted DNS already uses. Noise then lands exactly where
          your real lookups land, which is the point: whoever can see your real DNS
          sees the noise mixed in. Sending noise to resolvers you never otherwise
          use would only add a distinctive extra pattern.
  secure  OpenNIC servers that anonymize logs, and optionally Quad9, plus any IPs you add.
  mix     alternates between the two in randomly sized blocks (10-60 min by default),
          spending `system_share` of the time on your normal DNS.
"""
from __future__ import annotations

import random
import time

QUAD9 = [("9.9.9.9", "Quad9"), ("149.112.112.112", "Quad9")]   # docs.quad9.net/services (Secure)


class DnsPlan:
    def __init__(self, rng: random.Random, cfg, opennic: list[str] | None = None, clock=time.time):
        self.rng, self.cfg, self.clock = rng, cfg, clock
        self.opennic = []                      # [(ip, label)]
        for item in opennic or []:
            ip, _, rest = item.partition("|")
            name = rest.split("|")[0] if rest else ""
            self.opennic.append((ip, f"OpenNIC {name}".strip()))
        self._block_until = 0.0
        self._block_mode = "system"

    def secure_servers(self) -> list[tuple[str, str]]:
        out = list(self.opennic) if self.cfg.opennic else []
        if self.cfg.quad9:
            out += QUAD9
        out += [(ip.strip(), "your server") for ip in self.cfg.custom if ip.strip()]
        return out

    def current_mode(self) -> str:
        mode = self.cfg.mode
        if mode != "mix":
            return mode
        now = self.clock()
        if now >= self._block_until:
            lo, hi = (list(self.cfg.block_minutes) + [10, 60])[:2]
            self._block_until = now + self.rng.uniform(lo, max(lo, hi)) * 60
            self._block_mode = "system" if self.rng.random() < self.cfg.system_share else "secure"
        return self._block_mode

    def pick(self) -> tuple[str, str]:
        """(server, label). server == "system" means use the OS resolver."""
        if self.current_mode() == "secure":
            servers = self.secure_servers()
            if servers:
                ip, label = self.rng.choice(servers)
                return ip, f"{label} {ip}".strip()
        return "system", "your normal DNS"
