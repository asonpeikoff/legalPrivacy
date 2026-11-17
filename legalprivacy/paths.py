"""Filesystem locations: per-user app dir and bundled resources (PyInstaller-aware)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def getenv(name: str, default: str | None = None) -> str | None:
    """LEGALPRIVACY_<name>, or the name it had before the rename (CRICKET_<name>)."""
    return os.environ.get(f"LEGALPRIVACY_{name}") or os.environ.get(f"CRICKET_{name}") or default


def app_dir() -> Path:
    """Per-user writable directory (config, managed Tor, browser cookies). Created on demand.
    An existing folder from before the rename (noisyCricket) keeps being used, so nothing is lost."""
    override = getenv("HOME")
    if override:
        base = Path(override)
    else:
        if sys.platform == "win32":
            parent = Path(os.environ.get("APPDATA", Path.home()))
            base, old = parent / "legalPrivacy", parent / "noisyCricket"
        else:
            base, old = Path.home() / ".legalprivacy", Path.home() / ".noisycricket"
        if not base.exists() and old.exists():
            base = old
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource_root() -> Path:
    """Where bundled read-only files live (repo root, or PyInstaller's _MEIPASS)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[1]


def resolve_resource(rel: str) -> str:
    """Return an existing path for a possibly-relative resource (cwd first, then bundle)."""
    p = Path(rel)
    if p.is_absolute() or p.exists():
        return str(p)
    bundled = resource_root() / rel
    return str(bundled) if bundled.exists() else rel


def config_path() -> Path:
    return app_dir() / "config.yaml"


def tor_dir() -> Path:
    d = app_dir() / "tor"
    d.mkdir(parents=True, exist_ok=True)
    return d
