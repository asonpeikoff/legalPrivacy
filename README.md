# noisyCricket v2

Conducts random searches, crawls, and DNS lookups so that mass traffic and
bulk analysis of any one person's browsing is less useful. Fully automated,
with **separate, appropriate logic for Tor and clearnet traffic** and an
optional **community contribution** mode that runs a Tor *middle/bridge*
relay — never an exit.

```
cricket run --profile profiles/example.yaml [--once] [--dry-run]
cricket doctor --profile profiles/example.yaml
cricket relay gen --bandwidth 5Mbit --nickname mycricket [--bridge]
```

## How it works

A **profile** YAML sets per-engine daily budgets and a diurnal curve. The
scheduler drains budgets by picking one lane (tor / clearnet), building an
identity with a consistent fingerprint, and firing one hit with
lognormal-jitter pacing. Engines emit neutral `Target` objects; transports
own connection semantics.

- **search** — random topical queries (per-identity taste mixed into a global
  spread), rotates across search endpoints.
- **crawl** — seed URLs, extract links, random-walk with same-site
  containment and depth caps.
- **dns** — hand-crafted UDP DNS packets to public resolvers on clearnet;
  DNS-over-HTTPS over Tor on the tor lane (arbitrary UDP cannot traverse
  Tor circuits, so the tor lane resolves remotely via DoH).

## Tor vs clearnet — separate logic on purpose

- **tor lane**: one local SOCKS daemon; per-identity circuit isolation
  (SOCKS5 user:pass or `NEWNYM` via control port); remote DNS resolution
  only, your local resolver never sees a hostname.
- **clearnet lane**: direct or a proxy pool; identity pinned to a proxy +
  UA + language for its lifetime; dead proxies dim instead of failing a lane.

## Community contribution — never an exit

`cricket relay gen` emits a **middle-relay or bridge** torrc with
`ExitPolicy reject *:*` pinned. Tor has no "DNS node" role, so the
community-minded equivalent of "a DNS node" is exactly this non-exit relay:
it carries every end-to-end DNS resolution that exits perform, without ever
exiting plaintext. See `docs/DESIGN.md`.

## Stealth strategies

Per-identity consistent fingerprints (UA / language / pacing persona), a
diurnal S-curve activity model peaking 19:00–23:00 local, lognormal hit
cadence with burst suppression, exponential backoff on 403/429/timeouts,
proxy-health dimming, and hard `daily_max_failures` stops.

Tests: `python -m pytest`. Run: `pip install . && cricket --help`.
Legacy Java prototype preserved in `legacy-src/`.
