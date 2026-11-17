# Changelog

## v2.8.1

- **Proof tab map and timeline fixed for installs made before v2.8.0.** The double-click start scripts only
  installed what noisyCricket needs on the very first run, so the new `psutil` requirement was never added and the
  Proof tab stayed empty. They now update the environment whenever the requirements change.
- **`pip install` from GitHub now includes the map.** The map's script file wasn't packaged, so the map showed
  "Map unavailable".
- **Health tab** says whether connection watching and the map are available, and what to do if not.

## v2.8.0

- **Proof tab:** a live EtherApe-style map of every machine this computer talks to (your apps, noisyCricket, Tor),
  6 hours of traffic per minute with the noise-on periods shaded, an off-vs-on table, and a score for how much the
  noise's rhythm looks like your own. Local only; your own connections are never named or saved.
- **Tor bridges:** built-in obfs4, Snowflake and meek bridges from the official Tor bundle, your own saved bridges,
  an automatic fallback when Tor can't connect directly, "get bridges that work here" from the Tor Project's
  bridge service, and shortcuts to request private bridges. Snowflake now works with the current Tor bundle.
- **Patient Tor:** failed onion pages are sometimes reloaded a minute or two later, sometimes skipped; a new identity
  after a varying run of failures; circuits change by themselves every 10–40 minutes. **Test Tor** button and
  plain-English Tor errors.
- **DNS:** your three OpenNIC servers (ns2.de, ns3.de, ns4.de) as primaries with Quad9 as a fallback, in an editable
  table with speed checks and detection of networks that secretly answer DNS themselves.
- **Search engines** that show an "are you a robot?" check rest for a few hours; the browser runs as an off-screen
  real window by default where there is a display.
- **Browsing fixes:** results-page junk links ignored, correct scroll depth on sites that scroll an inner panel,
  back to the results to open another one.
- Tor tab: the onion site list is shown and editable; routing ordinary noise through Tor moved under Advanced.

## v2.7.0

- ML tab: how easily your real browsing can be told apart from the noise (ROC AUC, giveaways, advice).
- Starts within seconds; human time on page and results-page behaviour; quiet handling of offline onion sites.
- Browser engine choice (match your browser, Chromium or Firefox); Docker image; household masking;
  My List imports; popular sites re-ranked by Chrome's real browsing.

## v2.3 – v2.6

- Double-click dashboard with a 3-question setup, optional managed Tor with onion sites, real-browser mode with
  scrolling, reading and clicking, real search queries and real popular sites shipped as a snapshot, hourly
  randomized amount with a slider.

## v2.2.0 and earlier

- Command-line noise generator with separate clearnet and Tor lanes and community relay generation.
