"""Profile loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

DEFAULT_TOR_SOCKS = "127.0.0.1:9050"
DEFAULT_TOR_CONTROL = "127.0.0.1:9051"
DEFAULT_WORDLIST = "data/wordlists/words.txt"
DEFAULT_SEEDS = [
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://en.wikipedia.org/wiki/Randomness",
]


class ConfigError(ValueError):
    """Raised for invalid or unsafe profile configuration."""


@dataclass
class EngineConfig:
    enabled: bool = True
    budget_per_day: int = 50
    queries_per_identity: int = 6
    wordlist: str = DEFAULT_WORDLIST
    seeds: list[str] = field(default_factory=lambda: list(DEFAULT_SEEDS))
    max_depth: int = 4
    same_site_only: bool = True

    def validate(self, name: str) -> None:
        if self.budget_per_day < 0 or self.queries_per_identity < 1:
            raise ConfigError(f"engine '{name}': budget/queries must be positive")
        if name == "crawl" and self.enabled and not self.seeds:
            raise ConfigError("engine 'crawl' enabled but seeds list is empty")


@dataclass
class TransportConfig:
    enabled: bool = True
    proxy: str | None = None          # http:// or socks5:// URL
    socks: str = DEFAULT_TOR_SOCKS    # tor lane only
    control: str | None = None        # tor lane only, enables NEWNYM
    identities_per_circuit: int = 8   # tor lane only
    control_password: str | None = None

    def validate(self, name: str) -> None:
        if self.identities_per_circuit < 1:
            raise ConfigError(f"transport '{name}': identities_per_circuit >= 1")


@dataclass
class RelayConfig:
    enabled: bool = False
    mode: str = "middle"              # middle | bridge — NEVER an exit
    nickname: str = "noisycricket"
    contact: str | None = None
    bandwidth_rate: str = "5Mbit"
    or_port: int = 9001
    dir_port: int = 9030

    def validate(self) -> None:
        if self.mode not in ("middle", "bridge"):
            raise ConfigError(
                "contribute.relay.mode must be 'middle' or 'bridge'; "
                "noisyCricket v2 never runs an exit node"
            )


@dataclass
class Profile:
    name: str
    realtime_scale: float = 1.0
    engines: dict[str, EngineConfig] = field(default_factory=lambda: {
        "search": EngineConfig(budget_per_day=120),
        "crawl": EngineConfig(budget_per_day=40),
        "dns": EngineConfig(budget_per_day=200),
    })
    transports: dict[str, TransportConfig] = field(default_factory=lambda: {
        "clearnet": TransportConfig(),
        "tor": TransportConfig(socks=DEFAULT_TOR_SOCKS, control=DEFAULT_TOR_CONTROL),
    })
    relay: RelayConfig = field(default_factory=RelayConfig)
    daily_max_failures: int = 30

    def active_engines(self, transport: str) -> list[str]:
        return [name for name, eng in self.engines.items() if eng.enabled] \
            if transport in ("clearnet", "tor") else []


def _engine(raw: dict[str, Any] | None) -> EngineConfig:
    return EngineConfig(**{k: v for k, v in (raw or {}).items() if k in
                           EngineConfig.__dataclass_fields__})


def _transport(raw: dict[str, Any] | None) -> TransportConfig:
    return TransportConfig(**{k: v for k, v in (raw or {}).items() if k in
                              TransportConfig.__dataclass_fields__})


def load_profile(path: str | None) -> Profile:
    """Load a YAML profile, merged over defaults."""
    profile = Profile(name="default")
    if path is None:
        return profile
    if not os.path.exists(path):
        raise ConfigError(f"profile not found: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    p = raw.get("profile", raw)
    profile.name = p.get("name", profile.name)
    profile.realtime_scale = float(p.get("realtime_scale", 1.0))
    for name, raw_eng in (p.get("engines") or {}).items():
        if name in profile.engines:
            merged = profile.engines[name].__dict__ | (raw_eng or {})
            valid = {k: v for k, v in merged.items() if k in EngineConfig.__dataclass_fields__}
            profile.engines[name] = EngineConfig(**valid)
    for name, raw_tr in (p.get("transports") or {}).items():
        if name in profile.transports:
            merged = profile.transports[name].__dict__ | (raw_tr or {})
            valid = {k: v for k, v in merged.items() if k in TransportConfig.__dataclass_fields__}
            profile.transports[name] = TransportConfig(**valid)
    contribute = p.get("contribute") or {}
    if "relay" in contribute:
        profile.relay = RelayConfig(**{k: v for k, v in contribute["relay"].items()
                                       if k in RelayConfig.__dataclass_fields__})
    profile.daily_max_failures = int(p.get("daily_max_failures", 30))
    for name, eng in profile.engines.items():
        eng.validate(name)
    for name, tr in profile.transports.items():
        tr.validate(name)
    profile.relay.validate()
    if profile.realtime_scale <= 0:
        raise ConfigError("realtime_scale must be > 0")
    return profile
