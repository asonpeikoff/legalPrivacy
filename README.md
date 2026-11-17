# noisyCricket v2.5

Conducts random searches, crawls, and DNS lookups so that mass traffic and
bulk analysis of any one person's browsing is less useful. Fully automated,
with **separate, appropriate logic for Tor and clearnet traffic**, an
**optional Tor/onion mode**, and an optional **community contribution** mode
that runs a Tor *middle/bridge* relay — never an exit.

## Quick start — double-click

| You have | Do this |
|---|---|
| A release download | Double-click `cricket-launch-windows.exe` / `cricket-launch-macos` / `cricket-launch-linux` |
| The source (Python 3.10+) | Double-click `Start-noisyCricket.bat` (Windows) or `start-noisycricket.command` (macOS/Linux) — first run makes a private venv |
| pip | `pip install .` then `cricket-launch` (or `python -m noisycricket`) |

Your browser opens the **dashboard**. First run asks 3 questions, then starts:

1. How much noise? **Randomized** (recommended) or **Steady**, plus a 1–10 slider that shows what each level means in searches per hour
2. Add Tor darknet (.onion) noise? (default: No)
3. Start automatically whenever you open it? (Yes / No)

Nothing else to configure. Everything it does is visible in the dashboard.

## Tor is optional — two independent switches

| Mode | Default | What it does |
|---|---|---|
| **Tor off** | ✅ | Clearnet noise only. No Tor installed or started. |
| **🧅 Onion sites** | on when Tor is enabled | The `onion` engine visits real v3 `.onion` addresses (official mirrors of public sites, plus links discovered on them) through Tor. Never touches the clearnet. Uses Tor Browser's uniform fingerprint, not a per-identity UA, so it blends in. |
| **Clearnet through Tor** | **off — not recommended** | Also routes search/crawl/DoH noise via Tor exits. Burdens exit relays and is a distinctive pattern, so it's a separate, explicit opt-in (Tor tab, with a confirmation). |

**Tor is fully automatic** when enabled in managed mode (default): the app finds a
`tor` already on the machine or downloads the official *Tor Expert Bundle* from
`dist.torproject.org`, verifies its SHA-256 against the sums file published in the
same release directory, and runs a **private** Tor on its own ports and data
folder (`SOCKS 19050`, control `19051` by default) — your system Tor / Tor Browser is never touched.
Prefer your own Tor? Untick "Let noisyCricket manage Tor" and give its address.

The 19 built-in seeds are official onion mirrors of lawful public sites (news, privacy services, software, search), taken from the curated lists at [dark.fail](https://dark.fail) and [tor.taxi](https://tor.taxi). A seed is only included when two independent sources agree on its address (a guard against phishing clones). Markets, leak/exploit forums, adult, piracy and imageboard and government/law-enforcement sites are deliberately excluded.

Every `.onion` seed is validated offline with the v3 checksum (sha3-256) before use, so a
typo can never be dispatched; add your own seeds on the Tor tab.

## Dashboard tabs

| Tab | Shows / controls |
|---|---|
| **Dashboard** | hits, failures, uptime, hits-per-minute chart, per-lane per-engine budgets, one-click test hit |
| **Activity** | every request: time, lane, engine, URL, where the query came from, what the browser did (scrolls, depth, reading time, click/tap, download), status, latency |
| **Live Log** | one merged log (app, scheduler, clearnet, onion, **Tor's own output**), level + source filters, search, follow, download |
| **🧅 Tor** | status + bootstrap progress, version/ports/PID/traffic, **Start / Stop / Restart / New identity / Install**, live circuits table (onion-service circuits marked `HS_CLIENT_*`), Tor-only log, mode switches, onion seeds, bridges |
| **Settings** | per-engine budgets & presets, speed, failure cap, proxy, autostart, **Realism** (real browser, show window, query sources + their live status) |
| **Health** | plain-language self-checks (libs, internet, Tor program/status, seeds, ports) |

The dashboard listens only on `127.0.0.1`, requires a token in its link (kept between launches so bookmarks work), and rejects
foreign `Host`/`Origin` headers. Config lives in `~/.noisycricket/config.yaml`
(`%APPDATA%\noisyCricket` on Windows); override with `CRICKET_HOME`.

## Command line (optional)

```
cricket run --profile profiles/example.yaml [--verbose] [--reconnect-tor] [--once] [--dry-run]
cricket doctor --profile profiles/example.yaml
cricket relay bridges
cricket relay gen-client --type snowflake [--out torrc.client]
cricket relay gen --bandwidth 5Mbit --nickname mycricket [--bridge]
cricket ui [--no-browser] [--port N]      # the dashboard
cricket tor status|start|install|newnym   # manage the app's private Tor
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

**How much, and when.** You choose a level from 1 to 10. Each hour noisyCricket plans
that hour's activity and spreads it across the hour. It never fires a burst on the hour mark.

| Mode | What happens |
|---|---|
| **Randomized** (recommended) | Every hour's amount varies around your level (log-normal), follows a day/night curve that's busiest in the evening, and about 1 hour in 8 is nearly silent ("away from the computer"). |
| **Steady** | About the same amount every hour. |

| Level | Searches / hour | Over a 15-hour waking day |
|---|---|---|
| 1 | 0.5 | ~8 |
| 3 | 1.5 | ~23 |
| 5 (default) | 4 | ~60 |
| 7 | 9 | ~135 |
| 10 | 30 | ~450 |

For scale: active US desktop users average about 126 Google searches a month,
roughly 4 a day ([SparkToro, 2024](https://sparktoro.com/blog/new-research-how-often-do-americans-search-google-which-search-verticals-do-they-use/)).
Noise only hides you if it's at least comparable to your real activity, so even
the middle levels are well above that. Page visits and DNS lookups come on top of
searches, in proportion to the activity mix in Settings.

| Engine | What a hit is |
|---|---|
| **search** | A realistic query on a weighted mix of engines. About 60% of the time the persona then **clicks a result** (top results more often), and sometimes follows one more link on that site. |
| **crawl** | Opens a popular real site (or a configured seed, or a site reached earlier via search) and wanders a few links deep, skipping anchors, images, logins and edit pages. |
| **dns** | Looks up a **real, popular domain** (Tranco ranking) through the resolver you choose (below). DNS-over-HTTPS on the Tor lane. Invented domains are never used: a stream of failed lookups would itself stand out. |
| **onion** | Tor only. See above. |

### DNS: which resolver answers the noise

| Setting | What it does |
|---|---|
| **Your normal DNS** (default, recommended) | Uses your computer's own resolver: your router, ISP or VPN DNS, or whatever encrypted DNS your system uses. The noise lands exactly where your real lookups do, so whoever can see your real DNS sees the noise mixed in. |
| **Secure resolvers only** | OpenNIC servers that anonymize their logs (11 shipped, ≥95% reliable), optionally Quad9 (`9.9.9.9`, `149.112.112.112`), plus any resolver IPs you add. |
| **Mix** | Switches between the two in random 10–60 minute blocks, spending a share of the time you set (default 70%) on your normal DNS. |

Why normal DNS is the default: sending noise to resolvers you never otherwise use
would hide nothing from whoever sees your real lookups, and would itself be an
unusual pattern on your connection.

Every lookup in Activity reads like `www.reddit.com [via your normal DNS]` or
`www.bbc.com [via OpenNIC ns1.ca (148.113.235.58)]`.

### Behaving like a person (real-browser mode)

When Chrome, Edge, Brave or Chromium is installed (most computers), noisyCricket
drives it in the background instead of sending plain HTTP requests. A plain HTTP
client can't scroll, run JavaScript or load images, and its network fingerprint
isn't a browser's no matter what it claims. With a real browser each visit:

- waits for the page to settle, then **scrolls in bursts** (eased like a wheel or trackpad; real finger **swipes** for phone personas)
- **pauses to read** between bursts (log-normal; now and then a long "got distracted" pause), sometimes **scrolls back up**
- reads to a varied depth: most visits skim the top, some read to the end
- **moves the mouse** along curved paths, hovers, then **clicks** (or **taps**) the search result
- **downloads** a linked document now and then (about 3% of visits), through the browser, then discards it; nothing is saved
- desktop personas use the browser's own, honest User-Agent; about 25% are phone personas with touch input, a phone screen and matching client hints

Settings → Realism has the on/off switch and a "show the browser window" option
so you can watch it work. Activity shows what happened on each visit, for example
`click 🖥, 6 scrolls, 41% depth, 18s reading, 1 scroll-back`.

### Where searches come from

No single list dominates, because any fixed list would be hit far more often
than real users hit it, which is itself detectable.

| Source | Share | What it is |
|---|---|---|
| Live trends | ~40% | Google Trends daily searches, Wikipedia's most-read articles, BBC headlines turned into keyword searches. |
| Real past searches | ~35% | A random sample of real queries from **MS MARCO** (Bing) and **GooAQ** (Google autocomplete). |
| Built-in topics | ~10% | A small hand-written list, also the offline fallback. If a big source runs dry, its share goes to the other real sources, never to this list. |
| Dictionary words | ~4% | Occasional `define …` / `… meaning` lookups. |

On top of that: about 1 in 5 searches is a natural **refinement** of the previous one
(`… reviews`, `… news`), question marks are mostly dropped and text is mostly
lowercase (as people type), there are rare typos, and recent queries aren't repeated.

**Safety filter.** Queries and sites are screened so the noise never goes anywhere
embarrassing or risky: adult, gambling, weapons, drugs, violence, self-harm and
hacking terms are blocked, along with the StevenBlack adult/gambling/fake-news
host list and government domains.

**No lookups on your behalf.** These lists ship with noisyCricket as a snapshot
(`data/feeds/snapshot.json.gz`, about 1 MB: 7,600 real queries, 4,000 sites, current trends and
headlines, and the safety blocklist). The maintainer fetches it once with
`tools/build_feed_snapshot.py`, so your copy never contacts Google Trends, Wikimedia, BBC,
Hugging Face or Tranco unless you choose to. Settings → Realism shows each list's age and
lets you refresh by hand, or opt in to an automatic refresh when lists are about 3 months old.
Sources and their terms: `data/feeds/README.md`.

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

- **tor lane** (only exists when Tor is enabled; carries `onion`, and clearnet
  engines only if you opted in): one local SOCKS daemon; per-identity circuit isolation
  (SOCKS5 user:pass or `NEWNYM` via control port); remote DNS resolution
  only, your local resolver never sees a hostname.
- **clearnet lane**: direct or a proxy pool; identity pinned to a proxy +
  UA + language for its lifetime; dead proxies dim instead of failing a lane.

## Tor visibility, diagnostics & resilience

noisyCricket provides full visibility into Tor health — in the dashboard's Tor tab
(live) and on the CLI:

* **Live Health Check (`cricket doctor`)**: Probes the local Tor SOCKS daemon
  (`127.0.0.1:9050`) and Control port (`127.0.0.1:9051`), reporting bootstrap
  phase, Tor version, and authentication state. If Tor is offline, it provides
  actionable start commands and troubleshooting tips.
* **Circuit Isolation**: Each identity uses distinct SOCKS5 credentials
  (`ident-<id>-<seed>`), leveraging Tor's `IsolateSOCKSAuth` so that different
  personas never share circuits, cookies, or TLS state.
* **Manual & Automated Reconnects**:
  - `cricket run --reconnect-tor`: sends `AUTHENTICATE` + `signal NEWNYM` to the
    control port to build fresh circuits across the board before starting.
  - **Consecutive-Failure Self-Healing**: If an identity encounters 3 consecutive
    network/HTTP blocks (403/429/timeout), noisyCricket automatically retires
    the burned identity, resets circuit seeds, and acquires fresh circuits.
* **Transport Dimming**: When a transport encounters blocks or disconnects, it is
  automatically dimmed (selection weight reduced by 90%) so traffic dynamically
  routes through healthy lanes rather than failing the schedule.

## Censorship circumvention: Tor bridges & Pluggable Transports

If Tor's public directory is blocked or monitored by your ISP/network firewall,
you can run Tor via **Bridges** (unlisted relays):

1. **View Bundled Bridges**:
   ```bash
   cricket relay bridges
   ```
   Inspects collected bridge configurations stored in `data/bridges/bridges.example.txt`,
   including official Tor Project **Snowflake** bridges (WebRTC with Fastly/Cloudflare
   domain fronting, no registration required), **obfs4** templates, and **WebTunnel**
   templates.

2. **Generate a Client Bridge torrc**:
   ```bash
   cricket relay gen-client --type snowflake --out torrc.client
   ```
   Generates a ready-to-run client configuration with Snowflake pluggable transport
   pre-configured. Start Tor with it via:
   ```bash
   tor -f torrc.client
   ```

3. **Verify Connection**:
   ```bash
   cricket doctor --profile profiles/example.yaml
   ```

## Community contribution — never an exit

`cricket relay gen` emits a **middle-relay or bridge** torrc with
`ExitPolicy reject *:*` and `ExitPolicy reject6 *:*` pinned, with `SocksPort 0`
and safe `DirPort` configuration (`DirPort 0` enforced for bridges to prevent IP
enumeration). Tor has no "DNS node" role, so the community-minded equivalent of
"a DNS node" is exactly this non-exit relay: it carries every end-to-end DNS
resolution that exits perform, without ever exiting plaintext. See `docs/DESIGN.md`.

## Stealth strategies

The header-level persona below applies to plain-HTTP mode. In real-browser mode the browser's own headers, TLS and JavaScript fingerprint are used unchanged, so they always agree.

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

Tests: `python3 run_tests.py` (the real-browser test is skipped when no browser is found). CI runs them on every pull request. Run: `pip install . && cricket --help`.
Legacy Java prototype preserved in `legacy-src/`.

The plots above live in `docs/plots/*.txt` and are generated from the
actual package code (seeded, offline): `python3 docs/plots/gen_plots.py`.
