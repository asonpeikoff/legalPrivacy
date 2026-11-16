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

## Architecture at a glance

```text
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

### The shape of the traffic

```text
simulated day: one profile, both lanes, 360 hits/day total budget
each bar = expected hits that local hour ('#'+clearnet, 'c'+tor lane)

00:00 |#################ccccccccccccccccc                        |  15.4
01:00 |#############ccccccccccccc                                |  11.5
02:00 |#########ccccccccc                                        |   8.6
03:00 |#######ccccccc                                            |   6.8
04:00 |#######ccccccc                                            |   6.0
05:00 |######cccccc                                              |   5.8
06:00 |#######ccccccc                                            |   6.1
07:00 |########cccccccc                                          |   6.9
08:00 |#########ccccccccc                                        |   8.1
09:00 |##########cccccccccc                                      |   9.5
10:00 |############cccccccccccc                                  |  11.0
11:00 |##############cccccccccccccc                              |  12.6
12:00 |################cccccccccccccccc                          |  14.2
13:00 |#################ccccccccccccccccc                        |  15.6
14:00 |###################ccccccccccccccccccc                    |  16.9
15:00 |####################cccccccccccccccccccc                  |  18.2
16:00 |#####################ccccccccccccccccccccc                |  19.6
17:00 |#######################ccccccccccccccccccccccc            |  21.3
18:00 |##########################cccccccccccccccccccccccccc      |  23.5
19:00 |############################cccccccccccccccccccccccccccc  |  25.6
20:00 |#############################ccccccccccccccccccccccccccccc|  26.4
21:00 |#############################ccccccccccccccccccccccccccccc|  26.4
22:00 |##########################cccccccccccccccccccccccccc      |  24.0
23:00 |######################cccccccccccccccccccccc              |  19.9
```

```text
inter-hit delay distribution (Pacer, 4000 samples per pace persona)
seconds between hits; lognormal body, burst-suppressed at min_gap

  fast pace (0.6x):
      0-4  s |################################################|  1916
      5-9  s |#############################                   |  1147
     10-14 s |############                                    |   466
     15-24 s |########                                        |   324
     25-39 s |###                                             |   111
     40-64 s |#                                               |    28
     65-99 s |                                                |     5
    100-159s |                                                |     3
    160-249s |                                                |     0
    250-400s |                                                |     0

  normal pace (1.0x):
      0-4  s |###########################                     |  1084
      5-9  s |#############################                   |  1155
     10-14 s |#################                               |   669
     15-24 s |################                                |   621
     25-39 s |########                                        |   302
     40-64 s |###                                             |   129
     65-99 s |#                                               |    30
    100-159s |                                                |     7
    160-249s |                                                |     3
    250-400s |                                                |     0

  slow pace (1.8x):
      0-4  s |###########                                     |   422
      5-9  s |#####################                           |   840
     10-14 s |################                                |   654
     15-24 s |######################                          |   875
     25-39 s |###############                                 |   616
     40-64 s |#########                                       |   368
     65-99 s |####                                            |   163
    100-159s |#                                               |    46
    160-249s |                                                |    12
    250-400s |                                                |     4
```

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

Per-identity consistent fingerprints, refreshed against live traffic in 2026:
a real frequency-weighted UA pool (several Chrome majors circulate at once in
real populations — 152/156/157 — plus ESR Firefox stragglers, not just the
newest version), Accept-Language pinned, low-entropy **client hints
(`Sec-CH-UA`, `Sec-CH-UA-Platform`) emitted only on Chromium personas** —
Firefox and Safari never send them, so hinting on their UAs would be an
instant tell — fetch-metadata navigation headers (`Sec-Fetch-*`), **no DNT**
(removed from Firefox in v135 and Safari; still sending it marks you),
`Sec-GPC` opt-in at realistic low rates pinned per identity. On top of that:
diurnal activity curve peaking around 20:00–21:00 local (below), lognormal hit cadence with
burst suppression, exponential backoff on 403/429/timeouts, proxy-health
dimming, and hard `daily_max_failures` stops.

```text
noisyCricket diurnal activity model
activity weight by local hour (scheduler divides hit delays by this)

00:00 |##################################                        | 0.93
01:00 |#########################                                 | 0.69
02:00 |###################                                       | 0.52
03:00 |###############                                           | 0.41
04:00 |#############                                             | 0.36
05:00 |#############                                             | 0.35
06:00 |#############                                             | 0.37
07:00 |###############                                           | 0.42
08:00 |##################                                        | 0.49
09:00 |#####################                                     | 0.57
10:00 |########################                                  | 0.67
11:00 |############################                              | 0.76
12:00 |###############################                           | 0.86
13:00 |##################################                        | 0.95
14:00 |#####################################                     | 1.02
15:00 |########################################                  | 1.10
16:00 |###########################################               | 1.18
17:00 |###############################################           | 1.29
18:00 |###################################################       | 1.42
19:00 |########################################################  | 1.55
20:00 |##########################################################| 1.60  <-- peak
21:00 |##########################################################| 1.60  <-- peak
22:00 |#####################################################     | 1.45
23:00 |############################################              | 1.21
```

## Roadmap — local AI for human-indistinguishable activity (planned, not yet built)

The largest remaining statistical tell in v2 is that queries and clicks are
random draws from word lists: the distributions mix well, but sequences lack
human semantic structure — refiners, follow-ups, typos, and the relationship
between what a page shows and what gets clicked next. The future work is
**local AI models** used for:
- **Query generation**: a small quantized LLM (1–8B via llama.cpp/Ollama,
  run entirely on-device) turns each identity's query taste into coherent,
  locally-plausible query sequences with natural refiners and misspellings.
- **Session plausibility**: dwell time, link choice, and crawl depth sampled
  from learned human browsing patterns — including page-content reactivity —
  instead of hand-tuned curves.
- **Persona memory**: durable per-identity local interest profiles so each
  identity's long-run history looks like a person's topical life, not
  independent random draws.

Hard constraints for anything in this lane: inference is fully offline and
local (routing noise queries through a hosted LLM would add a fresh, highly
reportable signal and defeat the point), no telemetry, and model downloads
themselves should go over the tor lane.

Tests: `python -m pytest`. Run: `pip install . && cricket --help`.
Legacy Java prototype preserved in `legacy-src/`.

The plots above live in `docs/plots/*.txt` and are generated from the
actual package code (seeded, offline): `python3 docs/plots/gen_plots.py`.
