"""Profile loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from .rate import RateConfig

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
    browser_offscreen: bool = False   # clearnet: a real window, but parked off-screen (real graphics, not seen)
    browser_engine: str = "auto"      # clearnet: auto (match your browser) | chromium | firefox
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
        if self.browser_engine not in ("auto", "chromium", "firefox"):
            raise ConfigError(f"transport '{name}': browser_engine must be auto, chromium or firefox")
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
class DnsConfig:
    """Which resolver the DNS noise uses. See noisycricket/dnsplan.py."""
    mode: str = "mix"             # mix (default: blocks on your normal DNS + OpenNIC) | system | secure
    opennic: bool = True          # include OpenNIC no-log servers in the "secure" set
    quad9: bool = False           # include Quad9 (9.9.9.9 / 149.112.112.112) in the "secure" set
    custom: list[str] = field(default_factory=list)   # extra resolver IPs, your choice
    system_share: float = 0.5     # mix mode: share of time on your normal DNS
    block_minutes: list[int] = field(default_factory=lambda: [10, 60])   # mix mode: switch every 10-60 min

    def validate(self) -> None:
        import ipaddress
        if self.mode not in ("system", "secure", "mix"):
            raise ConfigError("dns.mode must be system, secure or mix")
        for ip in self.custom:
            try:
                ipaddress.ip_address(ip.strip())
            except ValueError as exc:
                raise ConfigError(f"dns.custom: '{ip}' is not an IP address") from exc
        if not 0 <= self.system_share <= 1:
            raise ConfigError("dns.system_share must be between 0 and 1")


@dataclass
class IdentityConfig:
    """Which user agent the plain-HTTP noise sends. See noisycricket/useragents.py."""
    ua_mode: str = "pin"          # pin (default: your own browser's, recommended) | rotate
    rotate_every: str = "hour"    # hour | day   (rotate mode: how often the choice changes)
    ua_pin: str = "own"           # pin mode: "own" or a library id. "own" with no UA yet = rotate until known
    own_ua: str = ""              # your browser's user agent (stored only on this computer)

    def validate(self) -> None:
        if self.ua_mode not in ("rotate", "pin"):
            raise ConfigError("identity.ua_mode must be rotate or pin")
        if self.rotate_every not in ("hour", "day"):
            raise ConfigError("identity.rotate_every must be hour or day")
        if len(self.own_ua) > 600:
            raise ConfigError("identity.own_ua is too long")


@dataclass
class ExtrasConfig:
    """Optional extras (most adopted from similar projects; see docs/COMPETITORS.md)."""
    active_hours: bool = False    # only make noise between active_from and active_to (local time)
    active_from: int = 8
    active_to: int = 23
    search_engines: dict = field(default_factory=dict)   # engine -> relative weight 0..10; {} = defaults
    typos: bool = False           # occasionally misspell a word in a search, as people do
    low_data: bool = False        # real browser skips images, video and fonts
    daily_data_mb: float = 0.0    # stop for the day after this much data; 0 = no cap
    polite: bool = False          # at least a minute between visits to the same site
    browser_dns: bool = True      # look names up like a browser: A, plus AAAA and HTTPS records
    interests: bool = False       # interest personas: a few steady themes at a time
    interest_list: list = field(default_factory=list)   # themes allowed ([] = all)
    interest_active: int = 3      # how many themes at once
    interest_rotate_days: int = 10
    interest_share: float = 0.3   # share of visits and searches that follow the themes
    interest_salt: str = ""       # random, set on first use: keeps your themes yours
    activity: str = "off"         # your own activity: off | backoff | follow
    busy_kbps: int = 400          # backoff: pause while your own traffic is above this
    metrics: bool = False         # Prometheus metrics at /metrics (needs the dashboard token)
    site_list: str = "both"       # both = Tranco re-ranked by Chrome's real browsing (CrUX), default | tranco
    household: bool = False       # noise from a virtual household of several people
    household_size: int = 3       # 2..5 people, member 1 = you

    def validate(self) -> None:
        from .engines import ENGINE_NAMES
        for k in ("active_from", "active_to"):
            if not 0 <= int(getattr(self, k)) <= 23:
                raise ConfigError(f"extras.{k} must be an hour from 0 to 23")
        if self.daily_data_mb < 0:
            raise ConfigError("extras.daily_data_mb must be 0 or more")
        for name, w in self.search_engines.items():
            if name not in ENGINE_NAMES:
                raise ConfigError(f"extras.search_engines: unknown engine '{name}'")
            if float(w) < 0:
                raise ConfigError("extras.search_engines weights must be 0 or more")
        if self.search_engines and not any(float(w) > 0 for w in self.search_engines.values()):
            raise ConfigError("Leave at least one search engine on.")
        from .interests import CATEGORIES
        for c in self.interest_list:
            if c not in CATEGORIES:
                raise ConfigError(f"extras.interest_list: unknown theme '{c}'")
        if not 1 <= int(self.interest_active) <= len(CATEGORIES):
            raise ConfigError("extras.interest_active is out of range")
        if not 1 <= int(self.interest_rotate_days) <= 365:
            raise ConfigError("extras.interest_rotate_days must be 1 to 365")
        if not 0 <= float(self.interest_share) <= 1:
            raise ConfigError("extras.interest_share must be between 0 and 1")
        if self.activity not in ("off", "backoff", "follow"):
            raise ConfigError("extras.activity must be off, backoff or follow")
        if not 2 <= int(self.household_size) <= 5:
            raise ConfigError("extras.household_size must be 2 to 5")
        if self.site_list not in ("both", "tranco"):
            raise ConfigError("extras.site_list must be both or tranco")
        if int(self.busy_kbps) < 10:
            raise ConfigError("extras.busy_kbps must be at least 10")


@dataclass
class TrafficConfig:
    """What kinds of noise run, in what proportions, and how the mix changes over time.

    Clearnet noise never goes through Tor in these mixes; .onion noise always does.
    """
    pattern: str = "drift"        # drift (default) | blend | alternate
    browser: float = 100.0        # clearnet pages in a real browser (relative weight)
    plain: float = 0.0            # clearnet pages over plain HTTP (no JavaScript, no scrolling)
    onion: float = 15.0           # Tor .onion sites (only when Tor is on)
    mobile_share: float = 0.55    # share of noise identities that are phones (most browsing is on phones)
    device: str = "iPhone 13"     # phone to emulate, or "mix"

    def validate(self) -> None:
        from .useragents import MOBILE_DEVICES
        if self.pattern not in ("drift", "blend", "alternate"):
            raise ConfigError("traffic.pattern must be drift, blend or alternate")
        for k in ("browser", "plain", "onion"):
            if float(getattr(self, k)) < 0:
                raise ConfigError(f"traffic.{k} must be 0 or more")
        if not 0 <= self.mobile_share <= 1:
            raise ConfigError("traffic.mobile_share must be between 0 and 1")
        if self.device != "mix" and self.device not in MOBILE_DEVICES:
            raise ConfigError("traffic.device: unknown phone '" + self.device + "'")


@dataclass
class MyListConfig:
    """Your own sites (My List). Page visits and DNS lookups come from here first."""
    enabled: bool = False
    sites: list[str] = field(default_factory=list)   # site names, most-visited first
    weights: dict = field(default_factory=dict)        # site -> how often, 1 (rarely) .. 10 (very often)
    random_share: float = 0.3     # share of visits/lookups from the other sources instead
    watch_started: float = 0.0    # "recording" window start (unix time); 0 = not recording
    watch_hours: float = 24.0     # recording window length

    def validate(self) -> None:
        if not 0 <= self.random_share <= 1:
            raise ConfigError("mylist.random_share must be between 0 and 1")
        if not 1 <= self.watch_hours <= 72:
            raise ConfigError("mylist.watch_hours must be between 1 and 72")
        for s in self.sites:
            if not s or " " in s or "/" in s or "." not in s or len(s) > 253:
                raise ConfigError(f"mylist: '{s}' is not a site name like example.com")


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
    rate: "RateConfig" = field(default_factory=lambda: RateConfig())
    dns: "DnsConfig" = field(default_factory=lambda: DnsConfig())
    identity: "IdentityConfig" = field(default_factory=lambda: IdentityConfig())
    mylist: "MyListConfig" = field(default_factory=lambda: MyListConfig())
    traffic: "TrafficConfig" = field(default_factory=lambda: TrafficConfig())
    extras: "ExtrasConfig" = field(default_factory=lambda: ExtrasConfig())

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
        "rate": asdict(profile.rate),
        "dns": asdict(profile.dns),
        "identity": asdict(profile.identity),
        "mylist": asdict(profile.mylist),
        "traffic": asdict(profile.traffic),
        "extras": asdict(profile.extras),
    }}


def save_profile(profile: Profile, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{__import__('threading').get_ident()}.tmp"
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
    if p.get("rate"):
        merged = profile.rate.__dict__ | p["rate"]
        profile.rate = RateConfig(**{k: v for k, v in merged.items() if k in RateConfig.__dataclass_fields__})
        profile.rate.level = float(profile.rate.level)
        profile.rate.randomness = float(profile.rate.randomness)
        try:
            profile.rate.validate()
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    if p.get("dns"):
        merged = profile.dns.__dict__ | p["dns"]
        if isinstance(merged.get("custom"), str):
            merged["custom"] = [x.strip() for x in merged["custom"].replace(",", "\n").splitlines() if x.strip()]
        profile.dns = DnsConfig(**{k: v for k, v in merged.items() if k in DnsConfig.__dataclass_fields__})
        profile.dns.system_share = float(profile.dns.system_share)
        profile.dns.validate()
    if p.get("identity"):
        merged = profile.identity.__dict__ | p["identity"]
        profile.identity = IdentityConfig(**{k: str(v) for k, v in merged.items()
                                             if k in IdentityConfig.__dataclass_fields__})
        profile.identity.validate()
    if p.get("extras"):
        m = profile.extras.__dict__ | p["extras"]
        profile.extras = ExtrasConfig(
            active_hours=bool(m["active_hours"]), active_from=int(m["active_from"]), active_to=int(m["active_to"]),
            search_engines={str(k): float(v) for k, v in (m.get("search_engines") or {}).items()},
            typos=bool(m["typos"]), low_data=bool(m["low_data"]), daily_data_mb=float(m["daily_data_mb"]),
            polite=bool(m["polite"]), browser_dns=bool(m["browser_dns"]),
            interests=bool(m["interests"]), interest_list=[str(x) for x in (m.get("interest_list") or [])],
            interest_active=int(m["interest_active"]), interest_rotate_days=int(m["interest_rotate_days"]),
            interest_share=float(m["interest_share"]), interest_salt=str(m.get("interest_salt") or ""),
            activity=str(m["activity"]), busy_kbps=int(m["busy_kbps"]), metrics=bool(m["metrics"]),
            site_list="tranco" if m.get("site_list") == "tranco" else "both",
            household=bool(m.get("household")), household_size=int(m.get("household_size") or 3))
        if (profile.extras.interests or profile.extras.household) and not profile.extras.interest_salt:
            import secrets as _s
            profile.extras.interest_salt = _s.token_hex(8)
        profile.extras.validate()
    if p.get("traffic"):
        merged = profile.traffic.__dict__ | p["traffic"]
        profile.traffic = TrafficConfig(
            pattern=str(merged["pattern"]), browser=float(merged["browser"]), plain=float(merged["plain"]),
            onion=float(merged["onion"]), mobile_share=float(merged["mobile_share"]), device=str(merged["device"]))
        profile.traffic.validate()
    if p.get("mylist"):
        merged = profile.mylist.__dict__ | p["mylist"]
        sites = merged.get("sites") or []
        if isinstance(sites, str):
            sites = sites.replace(",", "\n").splitlines()
        clean = []
        for x in sites:
            x = str(x).strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
            x = x.removeprefix("www.")
            if x and x not in clean:
                clean.append(x)
        merged["sites"] = clean
        weights = {str(k).lower().removeprefix("www."): float(v)
                   for k, v in (merged.get("weights") or {}).items()}
        profile.mylist = MyListConfig(
            enabled=bool(merged.get("enabled")), sites=clean,
            weights={k: min(10.0, max(1.0, v)) for k, v in weights.items() if k in clean},
            random_share=float(merged.get("random_share", 0.3)),
            watch_started=float(merged.get("watch_started") or 0),
            watch_hours=float(merged.get("watch_hours", 24)))
        profile.mylist.validate()
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
