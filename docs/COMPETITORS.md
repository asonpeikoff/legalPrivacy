# Similar projects, and what noisyCricket takes from them

Reviewed November 2026 from each project's README: 14 open-source noise and obfuscation tools.
Nothing here replaces an existing noisyCricket feature: everything we adopt is an
**optional** addition that can be turned off.

| Project | Stars | What it is |
|---|---|---|
| [1tayH/noisy](https://github.com/1tayH/noisy) (+ forks [madereddy/noisy](https://github.com/madereddy/noisy), [ingestbot/noisier](https://github.com/ingestbot/noisier), GoNoisy, noisyJS) | 1.8k | Random HTTP/DNS crawler from a root-URL list |
| [essandess/isp-data-pollution](https://github.com/essandess/isp-data-pollution) (+ [hvalev/isp-pollution-docker](https://github.com/hvalev/isp-pollution-docker)) | 614 | Headless Chrome crawler sized to a monthly bandwidth target |
| [vtoubiana/TrackMeNot](https://github.com/vtoubiana/TrackMeNot) | 522 | Browser add-on: decoy search queries (unmaintained) |
| [dhowe/AdNauseam](https://adnauseam.io) | 381 | Ad blocker that also clicks every ad |
| [davideolgiati/PartyLoud](https://github.com/davideolgiati/PartyLoud) | 285 | Bash, thread-per-URL crawler, random DNS server per request |
| [noiszy/noiszy](https://github.com/noiszy/noiszy) | 123 | Browser add-on: navigates around a site list |
| [umutcamliyurt/MockTraffic](https://github.com/umutcamliyurt/MockTraffic) | 104 | Android DNS + HTTPS noise |
| [jankais3r/DNoiSe](https://github.com/jankais3r/DNoiSe) | 99 | Pi-hole add-on: DNS noise that follows your real query volume |
| [FascinatedBox/RuinMyHistory](https://github.com/FascinatedBox/RuinMyHistory) | 64 | A web page that cycles through top sites |
| [soufianetahiri/phantom-noise](https://github.com/soufianetahiri/phantom-noise) | 9 | Chrome extension: decoy tabs with human behaviour |
| [Socold/NoisyCompany](https://github.com/Socold/NoisyCompany) | 1 | Low-bandwidth noise with personas, household masking, a separability study |

## What noisyCricket already has that most of them don't

A real browser that scrolls, reads, moves the mouse and clicks; phone emulation (iPhone 13 by
default); Tor .onion noise with a managed private Tor; relay contribution; searches from live
trends and real past queries; human sittings and day/night rhythm; My List (your own sites from
history, bookmarks or a recording window); user-agent library with pinning; DNS through your own
or filtered OpenNIC resolvers; a local dashboard with live activity.

## Features found elsewhere, and what we do with them

| Feature | Where | noisyCricket | Status |
|---|---|---|---|
| Active hours (only run between set times) | phantom-noise, NoisyCompany | Optional schedule in Settings | **added** |
| Search engine on/off and relative weights | phantom-noise | Optional, in Settings | **added** |
| Occasional typos in queries | phantom-noise | Already ~3% of searches; optional extra raises it to ~1 in 9 | **added** |
| Hover and text selection while reading | phantom-noise | Part of human behaviour | **added** |
| Low-data mode: no images or video | isp-data-pollution, NoisyCompany | Optional, off by default (real browsers load images) | **added** |
| Daily data cap and data-used counter | NoisyCompany, isp-data-pollution (GB/month) | Optional cap; usage on the dashboard | **added** |
| Export activity as JSON / CSV | phantom-noise | Activity tab download | **added** |
| Polite mode: per-site minimum gap, robots.txt | madereddy/noisy, NoisyCompany, isp-data-pollution | Optional, off by default | **added** |
| Browser-like DNS (AAAA and HTTPS records too) | NoisyCompany | Optional, on by default | **added** |
| Interest personas (coherent themes, rotated) | NoisyCompany, phantom-noise categories | Optional, alongside My List | **added** |
| Household masking (2–5 virtual people with their own hours) | NoisyCompany | Optional | planned |
| Follow your real activity (blend with your traffic volume, or back off when you're busy) | DNoiSe, NoisyCompany netwatch | Optional | **added** |
| Pi-hole / AdGuard Home integration | DNoiSe, NoisyCompany | Optional | planned |
| Separability self-test (how easily noise is told apart) | NoisyCompany study | Tools tab | **added** |
| Data / energy estimate before running | NoisyCompany | Tools tab | **added** |
| Local privacy check with advice | NoisyCompany | Tools tab | **added** |
| Prometheus metrics | noisier, NoisyCompany | Optional endpoint | **added** |
| Docker image, Raspberry Pi, Home Assistant | many | Packaging | planned |
| Drive your real Firefox, not just Chromium | NoisyCompany companion | Optional | planned |
| CrUX top-sites list (per country) as a site source | madereddy/noisy | Optional source next to Tranco | planned |
| Clicking ads | AdNauseam | **Not adopted**: it bills advertisers for fake clicks | declined |
| Random DNS server per request | PartyLoud | Already: DNS Mix mode | have |
| Poisson / exponential timing | phantom-noise, NoisyCompany | Already: sittings model | have |
| Fresh, isolated browser profile per decoy | phantom-noise | Already: every identity gets a fresh browser context | have |
| HTTP/SOCKS proxy | PartyLoud, noisy | Already | have |
