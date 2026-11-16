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
    """One transport lane. Tor-only fields are ignored by the clearnet lane.

    Tor is OPTIONAL and off by default. When enabled it does two independent,
    individually switchable things:
      * onion          — visit .onion sites (darknet noise)        [default on]
      * clearnet_via_tor — also route clearnet noise through Tor   [default OFF,
        discouraged: it burdens exit relays and clearnet-over-Tor is a
        distinctive pattern]
    """
    enabled: bool = True
    proxy: str | None = None          # clearnet: http:// or socks5:// URL
    browser: str = "auto"             # clearnet: auto | on | off — drive a real browser like a person
    browser_visible: bool = False     # clearnet: show the browser window instead of running headless
    # --- tor lane only ---
    socks: str = DEFAULT_TOR_SOCKS
    control: str | None = None
    identities_per_circuit: int = 8
    control_password: str | None = None
    manage: bool = True               # True: app runs its own private Tor; False: use existing Tor
    auto_install: bool = True         # download official Tor bundle if none is installed
    onion: bool = True                # run the onion engine on the Tor lane
    clearnet_via_tor: bool = False    # route clearnet engines through Tor (not recommended)
    onion_seeds: list[str] = field(default_factory=list)   # extra seeds; empty = built-ins
    onion_follow_offsite: bool = True
    bridges: list[str] = field(default_factory=list)       # bridge lines for censored networks

    def validate(self, name: str) -> None:
        if self.browser not in ("auto", "on", "off"):
            raise ConfigError(f"transport '{name}': browser must be auto, on or off")
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
class QuerySources:
    """Where search queries come from (mixed; none dominates). See queries.py."""
    live: bool = True        # Google Trends, Wikipedia most-read, BBC headlines (refreshed daily)
    dataset: bool = True     # real past searches: MS MARCO (Bing) + GooAQ (Google autocomplete)
    builtin: bool = True     # small hand-written topic list (kept a minority on purpose)
    words: bool = True       # occasional dictionary word ("define …", "… meaning")


@dataclass
class Profile:
    name: str
    realtime_scale: float = 1.0
    engines: dict[str, EngineConfig] = field(default_factory=lambda: {
        "search": EngineConfig(budget_per_day=120),
        "crawl": EngineConfig(budget_per_day=40),
        "dns": EngineConfig(budget_per_day=200),
        "onion": EngineConfig(budget_per_day=40, max_depth=3),
    })
    transports: dict[str, TransportConfig] = field(default_factory=lambda: {
        "clearnet": TransportConfig(),
        "tor": TransportConfig(enabled=False, socks=DEFAULT_TOR_SOCKS, control=DEFAULT_TOR_CONTROL),
    })
    relay: RelayConfig = field(default_factory=RelayConfig)
    daily_max_failures: int = 30
    queries: "QuerySources" = field(default_factory=lambda: QuerySources())

    def active_engines(self, transport: str) -> list[str]:
        """Engines a lane runs. Onion is Tor-only; clearnet engines run on Tor
        only when the user explicitly opted into clearnet_via_tor."""
        on = [n for n, e in self.engines.items() if e.enabled]
        if transport == "clearnet":
            return [n for n in on if n != "onion"]
        if transport == "tor":
            tor = self.transports.get("tor")
            out = []
            if tor and tor.clearnet_via_tor:
                out += [n for n in on if n != "onion"]
            if tor and tor.onion and "onion" in on:
                out.append("onion")
            return out
        return []

    def clearnet_lane_count(self) -> int:
        """How many lanes share the clearnet engines' daily budgets."""
        c, t = self.transports.get("clearnet"), self.transports.get("tor")
        return (1 if c and c.enabled else 0) + (1 if t and t.enabled and t.clearnet_via_tor else 0) or 1


def _engine(raw: dict[str, Any] | None) -> EngineConfig:
    return EngineConfig(**{k: v for k, v in (raw or {}).items() if k in
                           EngineConfig.__dataclass_fields__})


def _transport(raw: dict[str, Any] | None) -> TransportConfig:
    return TransportConfig(**{k: v for k, v in (raw or {}).items() if k in
                              TransportConfig.__dataclass_fields__})


def profile_to_dict(profile: Profile) -> dict[str, Any]:
    from dataclasses import asdict
    return {"profile": {
        "name": profile.name,
        "realtime_scale": profile.realtime_scale,
        "engines": {n: asdict(e) for n, e in profile.engines.items()},
        "transports": {n: asdict(t) for n, t in profile.transports.items()},
        "daily_max_failures": profile.daily_max_failures,
        "contribute": {"relay": asdict(profile.relay)},
        "queries": asdict(profile.queries),
    }}


def save_profile(profile: Profile, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(profile_to_dict(profile), fh, sort_keys=False)
    os.replace(tmp, path)


def apply_overrides(profile: Profile, raw: dict[str, Any]) -> Profile:
    """Merge a (possibly partial) profile dict over an existing Profile, validating."""
    p = raw.get("profile", raw)
    profile.name = p.get("name", profile.name)
    if "realtime_scale" in p:
        profile.realtime_scale = float(p["realtime_scale"])
    for name, raw_eng in (p.get("engines") or {}).items():
        if name in profile.engines:
            merged = profile.engines[name].__dict__ | (raw_eng or {})
            profile.engines[name] = EngineConfig(
                **{k: v for k, v in merged.items() if k in EngineConfig.__dataclass_fields__})
    for name, raw_tr in (p.get("transports") or {}).items():
        if name in profile.transports:
            merged = profile.transports[name].__dict__ | (raw_tr or {})
            profile.transports[name] = TransportConfig(
                **{k: v for k, v in merged.items() if k in TransportConfig.__dataclass_fields__})
    contribute = p.get("contribute") or {}
    if "relay" in contribute:
        merged = profile.relay.__dict__ | contribute["relay"]
        profile.relay = RelayConfig(**{k: v for k, v in merged.items() if k in RelayConfig.__dataclass_fields__})
    if p.get("queries"):
        merged = profile.queries.__dict__ | p["queries"]
        profile.queries = QuerySources(**{k: bool(v) for k, v in merged.items()
                                          if k in QuerySources.__dataclass_fields__})
    if "daily_max_failures" in p:
        profile.daily_max_failures = int(p["daily_max_failures"])
    for name, eng in profile.engines.items():
        eng.validate(name)
    for name, tr in profile.transports.items():
        tr.validate(name)
    profile.relay.validate()
    if profile.realtime_scale <= 0:
        raise ConfigError("realtime_scale must be > 0")
    return profile


def load_profile(path: str | None) -> Profile:
    """Load a YAML profile, merged over defaults."""
    profile = Profile(name="default")
    if path is None:
        return profile
    if not os.path.exists(path):
        raise ConfigError(f"profile not found: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return apply_overrides(profile, raw)
