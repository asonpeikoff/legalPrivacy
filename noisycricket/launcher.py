"""noisyCricket launcher — just run this, no flags needed."""
from __future__ import annotations

import os
import platform
import sys
import time

# ── optional colour (safe fallback) ──────────────────────────────────────────
_TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
def _c(code: str, t: str) -> str: return f"\033[{code}m{t}\033[0m" if _TTY else t
def green(t):  return _c("32;1", t)
def yellow(t): return _c("33;1", t)
def cyan(t):   return _c("36;1", t)
def red(t):    return _c("31;1", t)
def bold(t):   return _c("1",    t)
def dim(t):    return _c("2",    t)

BANNER = """\
  ╔══════════════════════════════════════════╗
  ║   🦗  noisyCricket  v2.2                ║
  ║   Privacy noise generator               ║
  ╚══════════════════════════════════════════╝
"""

def _clear():
    os.system("cls" if platform.system() == "Windows" else "clear")

def _ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        v = input(f"  {prompt}{hint}: ").strip()
        return v or default
    except (EOFError, KeyboardInterrupt):
        return default

def _pause():
    try: input(dim("\n  Press Enter to return to menu…"))
    except (EOFError, KeyboardInterrupt): pass

# ── Tor check ────────────────────────────────────────────────────────────────
def _tor_running() -> bool:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 9050), timeout=1); s.close(); return True
    except OSError:
        return False

# ── runners ───────────────────────────────────────────────────────────────────
def _run(once: bool = False, verbose: bool = False) -> None:
    import argparse
    try:
        from noisycricket.cli import cmd_run
        from noisycricket.scheduler import MaxFailuresExceeded
    except ImportError as e:
        print(red(f"\n  Import error: {e}"))
        print(dim("  Try:  pip install -e ."))
        _pause(); return

    args = argparse.Namespace(
        profile=None, once=once, dry_run=False,
        verbose=verbose, reconnect_tor=False,
    )
    if once:
        print(green("\n  Sending one noise round…\n"))
    else:
        print(green("\n  Running — press Ctrl+C to stop.\n"))
    try:
        cmd_run(args)
        if once:
            print(green("  ✓ Done."))
    except KeyboardInterrupt:
        print(yellow("\n\n  Stopped."))
    except MaxFailuresExceeded as e:
        print(red(f"\n  Stopped after too many failures: {e}"))
    except Exception as e:
        print(red(f"\n  Error: {e}"))
    _pause()

def _health() -> None:
    _clear()
    print(bold("\n  Health check\n"))

    # Tor
    tor = _tor_running()
    print(f"  {'✓' if tor else '⚠'}  Tor (port 9050): " +
          (green("running") if tor else yellow("not detected — clearnet only")))

    # required libs
    for lib in ("requests", "yaml"):
        try:
            __import__(lib); ok = True
        except ImportError:
            ok = False
        print(f"  {'✓' if ok else '✗'}  {lib}: " +
              (green("OK") if ok else red(f"missing — pip install {'pyyaml' if lib=='yaml' else lib}")))

    # full doctor
    try:
        import argparse
        from noisycricket.cli import cmd_doctor
        print()
        cmd_doctor(argparse.Namespace(profile=None))
    except Exception as e:
        print(dim(f"\n  (detailed check unavailable: {e})"))
    _pause()

def _tor_help() -> None:
    _clear()
    sys_name = platform.system()
    print(bold("\n  Tor setup\n"))
    print("  Tor makes your noise traffic anonymous.\n")
    if sys_name == "Linux":
        print(dim("    Install:  sudo apt install tor   (or dnf / pacman)"))
        print(dim("    Start:    sudo systemctl start tor"))
    elif sys_name == "Darwin":
        print(dim("    Install:  brew install tor"))
        print(dim("    Start:    brew services start tor"))
    elif sys_name == "Windows":
        print(dim("    Download: https://www.torproject.org/download/"))
        print(dim("    Run:      tor.exe"))
    else:
        print(dim("    See: https://www.torproject.org/download/"))

    print(f"\n  Status: " + (green("Tor is running ✓") if _tor_running()
                              else yellow("Tor not detected on port 9050")))
    print(dim("\n  Bridges (bypass censorship):"))
    print(dim("    cricket relay gen-client --type snowflake"))
    print(dim("    tor -f torrc.client"))
    _pause()

# ── main menu ─────────────────────────────────────────────────────────────────
def main() -> None:
    while True:
        _clear()
        print(cyan(BANNER) if _TTY else BANNER)

        tor_line = green("Tor: active 🧅") if _tor_running() else dim("Tor: not detected")
        print(f"  {tor_line}\n")

        print(f"  {bold('[1]')}  Start now              {dim('(continuous, recommended)')}")
        print(f"  {bold('[2]')}  Quick test             {dim('(one round, then exit)')}")
        print(f"  {bold('[3]')}  Health check")
        print(f"  {bold('[4]')}  Tor setup guide")
        print(f"  {bold('[Q]')}  Quit\n")

        choice = _ask("Choice (or press Enter to start)", "1").upper()

        if choice in ("1", ""):
            _clear(); _run(once=False)
        elif choice == "2":
            _clear(); _run(once=True)
        elif choice == "3":
            _health()
        elif choice == "4":
            _tor_help()
        elif choice == "Q":
            print(green("\n  Goodbye 🦗\n")); break
        else:
            time.sleep(0.5)   # bad input — just redraw


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(yellow("\n  Interrupted.\n"))
