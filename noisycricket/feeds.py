"""Live data sources for realistic noise, cached on disk, refreshed in the background.

  trends   Google Trends daily-search RSS        (what people are searching today)
  wiki     Wikipedia most-viewed articles        (what people are reading today)
  news     BBC News RSS headlines                (what is in the news today)
  dataset  real search queries: MS MARCO (Bing) and GooAQ (Google autocomplete),
           sampled at random over the Hugging Face datasets-server — fetched at
           runtime for personal use, never bundled or redistributed
  tranco   Tranco top-sites ranking              (real, popular domains)
  deny     StevenBlack adult/gambling/fake-news host list (used only as a filter)

Everything is best-effort: any failure leaves the previous cache (or the small
built-in fallbacks) in place and is reported through the event log.
"""
from __future__ import annotations

import csv
import html
import io
import json
import random
import re
import threading
import time
import zipfile
from datetime import date, timedelta
from typing import Callable

import requests

from .events import EventLog
from .paths import app_dir
from .safety import safe_domain, safe_query

UA = "noisyCricket/2.4 (privacy noise generator; +https://github.com/asonpeikoff/noisyCricket)"
TRENDS_URL = "https://trends.google.com/trending/rss?geo={geo}"
WIKI_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{d}"
NEWS_FEEDS = [f"https://feeds.bbci.co.uk/news/{p}rss.xml" for p in
              ("", "world/", "technology/", "science_and_environment/", "health/", "business/",
               "entertainment_and_arts/")]
TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
DENY_URL = "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/fakenews-gambling-porn/hosts"
DS_ROWS = "https://datasets-server.huggingface.co/rows"
DATASETS = [  # (dataset, config, split, field, conservative max row index)
    ("microsoft/ms_marco", "v2.1", "train", "query", 800_000),
    ("sentence-transformers/gooaq", "pair", "train", "question", 3_000_000),
]
TTL = {"trends": 6 * 3600, "wiki": 12 * 3600, "news": 6 * 3600,
       "dataset": 5 * 86400, "tranco": 7 * 86400, "deny": 14 * 86400}
_SKIP_WIKI = re.compile(r"^(Main_Page|Special:|Wikipedia:|Portal:|File:|Help:|Category:|Template:|Talk:|User:|-$)")


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=kw.pop("timeout", 25), **kw)
    r.raise_for_status()
    return r


class FeedStore:
    """Disk-cached feed data + background refresh. Thread-safe reads."""

    def __init__(self, log: EventLog | None = None, path=None, fetchers: dict | None = None):
        self.log = log
        self.path = path or (app_dir() / "feeds.json")
        self._lock = threading.Lock()
        self.data: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        self.fetchers: dict[str, Callable[[], list]] = fetchers or {
            "deny": self._fetch_deny, "trends": self._fetch_trends, "wiki": self._fetch_wiki,
            "news": self._fetch_news, "dataset": self._fetch_dataset, "tranco": self._fetch_tranco}
        self.refreshing = False
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def _save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def items(self, name: str) -> list:
        with self._lock:
            return list(self.data.get(name, {}).get("items", []))

    def denylist(self) -> set[str]:
        return set(self.items("deny"))

    def status(self) -> list[dict]:
        out = []
        for name in self.fetchers:
            ent = self.data.get(name, {})
            out.append({"name": name, "count": len(ent.get("items", [])), "updated": ent.get("ts"),
                        "stale": (time.time() - ent.get("ts", 0)) > TTL[name] if ent else True,
                        "error": self.errors.get(name)})
        return out

    # -- refresh -----------------------------------------------------------
    def refresh(self, force: bool = False, only: list[str] | None = None) -> None:
        """Blocking refresh of stale sources (call from a background thread)."""
        self.refreshing = True
        try:
            for name, fn in self.fetchers.items():
                if only and name not in only:
                    continue
                ent = self.data.get(name)
                if not force and ent and time.time() - ent.get("ts", 0) < TTL[name]:
                    continue
                try:
                    items = fn()
                    if not items:
                        raise RuntimeError("source returned nothing")
                    with self._lock:
                        self.data[name] = {"ts": time.time(), "items": items}
                    self.errors.pop(name, None)
                    self._log("info", f"Feed '{name}' refreshed: {len(items):,} items")
                except Exception as exc:
                    self.errors[name] = str(exc)[:200]
                    self._log("warn", f"Feed '{name}' unavailable ({str(exc)[:120]}); using "
                                      f"{'cached data' if ent else 'built-in fallback'}")
            self._save()
        finally:
            self.refreshing = False

    def refresh_async(self, force: bool = False) -> None:
        if self.refreshing:
            return
        threading.Thread(target=self.refresh, args=(force,), daemon=True, name="feeds").start()

    def _log(self, level: str, msg: str) -> None:
        if self.log:
            self.log.add(level, "feeds", msg)

    # -- fetchers ----------------------------------------------------------
    def _fetch_trends(self) -> list[str]:
        txt = _get(TRENDS_URL.format(geo="US")).text
        titles = [html.unescape(t) for block in txt.split("<item>")[1:]
                  for t in re.findall(r"<title>(.*?)</title>", block, re.S)[:1]]
        return [t.strip().lower() for t in titles if safe_query(t)]

    def _fetch_wiki(self) -> list[str]:
        d = date.today() - timedelta(days=2)
        j = _get(WIKI_URL.format(d=d.strftime("%Y/%m/%d"))).json()
        arts = j["items"][0]["articles"]
        out = []
        for a in arts[:400]:
            name = a["article"]
            if _SKIP_WIKI.match(name):
                continue
            q = name.replace("_", " ")
            if safe_query(q):
                out.append(q)
        return out[:200]

    def _fetch_news(self) -> list[str]:
        out: list[str] = []
        for url in NEWS_FEEDS:
            try:
                txt = _get(url, timeout=15).text
            except requests.RequestException:
                continue
            for block in txt.split("<item>")[1:]:
                m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.S)
                if m:
                    t = html.unescape(m.group(1)).strip()
                    t = re.sub(r"[‘’]", "'", t)
                    if safe_query(t):
                        out.append(t.lower())
        return list(dict.fromkeys(out))

    def _fetch_dataset(self, per_source: int = 1200) -> list[str]:
        rng = random.Random()
        out: list[str] = []
        for ds, cfg, split, field, max_row in DATASETS:
            got, tries = 0, 0
            while got < per_source and tries < 40:
                tries += 1
                try:
                    r = _get(DS_ROWS, params={"dataset": ds, "config": cfg, "split": split,
                                               "offset": rng.randrange(0, max_row - 100), "length": 100},
                             timeout=30)
                    for row in r.json().get("rows", []):
                        q = str(row["row"].get(field, "")).strip()
                        if safe_query(q) and len(q.split()) >= 2:
                            out.append(q.lower() if not q.isupper() else q)
                            got += 1
                except (requests.RequestException, ValueError, KeyError):
                    time.sleep(0.5)
        return list(dict.fromkeys(out))

    def _fetch_tranco(self, keep: int = 4000) -> list[str]:
        raw = _get(TRANCO_URL, timeout=90).content
        deny = self.denylist()
        out: list[str] = []
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            with z.open(z.namelist()[0]) as fh:
                for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                    if len(row) == 2 and safe_domain(row[1], deny):
                        out.append(row[1].lower())
                        if len(out) >= keep:
                            break
                    if row and row[0].isdigit() and int(row[0]) > 25_000:
                        break
        return out

    def _fetch_deny(self) -> list[str]:
        txt = _get(DENY_URL, timeout=60).text
        out = []
        for ln in txt.splitlines():
            if ln.startswith("0.0.0.0 "):
                host = ln.split()[1].lower()
                if host not in ("0.0.0.0", "localhost"):
                    out.append(host)
        return out
