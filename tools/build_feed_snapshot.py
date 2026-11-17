#!/usr/bin/env python3
"""Maintainer tool: fetch every feed once and write the bundled snapshot.

Users then never have to contact Google Trends / Wikimedia / BBC / Hugging Face /
Tranco themselves. Re-run every few months and commit data/feeds/snapshot.json.gz.
"""
import gzip
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legalprivacy.feeds import FeedStore  # noqa: E402

out = Path(__file__).resolve().parents[1] / "data" / "feeds" / "snapshot.json.gz"
st = FeedStore(None, path=Path(tempfile.mkdtemp()) / "f.json", bundled=False)
st.fetchers["dataset"] = lambda: st._fetch_dataset(per_source=6000)
st.refresh(force=True)
bad = [s for s in st.status() if not s["count"]]
if bad:
    sys.exit(f"refusing to write snapshot, empty sources: {bad}")
data = {"built": time.time(), **st.data}
with gzip.open(out, "wt", encoding="utf-8") as fh:
    json.dump(data, fh)
print(f"wrote {out} ({out.stat().st_size // 1024} KB):",
      {k: len(v["items"]) for k, v in st.data.items()})
