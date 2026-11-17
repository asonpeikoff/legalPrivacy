# Bundled source snapshot

`snapshot.json.gz` is fetched **once by the maintainer** with
`python3 tools/build_feed_snapshot.py`, so users never have to contact these
sites themselves. Refresh it every few months and commit it.

| Key | Source | Notes |
|---|---|---|
| `trends` | Google Trends daily-search RSS (US) | short topic strings |
| `wiki` | Wikimedia pageviews API, en.wikipedia most-read | article titles |
| `news` | BBC News RSS headlines | turned into keyword searches at run time |
| `dataset` | MS MARCO v2.1 queries (Microsoft) and GooAQ questions (AI2), via the Hugging Face datasets-server | random sample of real search queries, safety-filtered |
| `tranco` | Tranco top-sites list | top 4,000 after safety/infrastructure filtering |
| `deny` | StevenBlack hosts, `fakenews-gambling-porn` variant | used only as a filter |

Each source has its own terms; check them before reusing this data elsewhere.
MS MARCO, for example, is published by Microsoft for non-commercial research use.
noisyCricket uses the data only to generate privacy noise and does not sell or
re-publish it as a dataset.

`opennic`: OpenNIC Tier-2 resolvers from `api.opennicproject.org/geoip/?json&ipv=4&anon=true&pct=95&res=50`
(only servers with anonymized logs and ≥95% reliability). Used only when you choose
"secure" or "mix" DNS. You can add your own resolver IPs in Settings.
