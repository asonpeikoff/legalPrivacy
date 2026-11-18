# Changelog

## v2.10.1

- **Fix: the noise browser popped up windows.** It was meant to run hidden by default, but it opened an "off-screen"
  window instead, which Linux desktops using Wayland (such as Fedora) put on screen. It now runs hidden by default,
  and older settings go back to hidden too. "Off-screen" is still an option on Windows, macOS and X11; elsewhere it
  runs hidden.
- **One window, tabs:** when you choose to watch it, the noise browser keeps one window and opens pages in tabs.
  Pop-ups and "open in new window" links stay in the same tab.
- **Always the user agent it's given:** every tab, phone or desktop, hidden or visible, uses the identity's own
  user agent and matching client hints, never the browser's. If that can't be set, nothing is visited.
- **English-language sites only:** sites on country domains such as .de, .fr or .jp, language sections such as
  de.example.com or example.com/fr/, and pages that say they're in another language are skipped. A page that turns
  out not to be in English gets a quick look, and then the site is left alone for about a week.

## v2.10.0

- **Data scientist is the default persona**, followed 90% of the time (the rest is everyday browsing); "Nobody in
  particular (neutral)" turns it off. Its searches are mostly real people's: about 8,000 data-science searches from
  MS MARCO (Bing) and GooAQ (Google), advanced only: basic statistics is left out. Its sites are a list of ~115 data-science sites, and more popular ones come up
  more often. Sections of sites (such as reddit.com/r/datascience) are visited as listed.
- **Package installs** (Settings → Extras): real `pip install`, `pip download`, `pip index versions` and
  `pip uninstall` of popular packages, plus `npm view` / `npm install` if npm is installed, so the noise also talks to
  PyPI and npm like someone who codes. Automatic by default: on for the Data scientist and Developer personas.
  It all happens in legalPrivacy's own separate environment, never yours: only ready-built packages, nothing installed
  is ever run, installs are removed again, downloads are deleted, and the environment is rebuilt daily. Never over Tor.

## v2.9.0

- **Personas** (Settings → Extras): pick who the noise mostly looks like: Data scientist, Developer, Sports fan,
  Student, Home cook and gardener, or Traveller. It's followed mostly, not perfectly (60% by default); the rest stays
  varied. New Data science theme with its own sites and searches.
- **Clear logs between restarts** (Settings → General, on by default): the Live Log and Activity start empty each
  time noise starts; untick to keep them across Stop → Start. Both are memory-only and always end when the app quits.
- **Fix:** after Stop → Start, new requests didn't appear in Activity until the count passed the previous run's.
- **Login walls:** pages that require an account (Facebook, Instagram, LinkedIn, X and the like) get a short look,
  then the noise moves on and rests that site for a few hours, like a logged-out visitor. Public pages are browsed
  as usual. It never counts as a failure, and the noise never logs in.
- **New name: legalPrivacy** (formerly noisyCricket), a homage to the blog *Legalizing Privacy*. Commands are now
  `legalprivacy` and `legalprivacy-launch`, start scripts `Start-legalPrivacy.bat` and `start-legalprivacy.sh` /
  `.command`, downloads `legalprivacy-launch-*`, and the Docker service `legalprivacy`.
- **Nothing to redo after updating:** an existing settings folder (`~/.noisycricket`, `%APPDATA%\noisyCricket`) keeps
  being used, the old commands `cricket` and `cricket-launch` still work, and so do `CRICKET_*` environment variables
  (the new `LEGALPRIVACY_*` names take priority).

## v2.8.1

- **Network map explains itself** when it has nothing to draw (psutil missing, watching off, nothing seen yet,
  or only local/private connections such as through a VPN or proxy), and shows any drawing error instead of
  staying blank.
- **Cookies:** the noise browser keeps its own cookies for about an hour by default (was a week), then starts
  fresh, with each set living 60–140% of the chosen time. Choices: don't keep, 1 hour, 6 hours, 1 day, 1 week,
  30 days. Earlier day-based settings carry over.
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
