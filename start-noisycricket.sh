#!/usr/bin/env bash
# Double-click (macOS: .command; Linux: "Run in terminal") to run noisyCricket from source.
# Sets up a private environment on first run, and updates it whenever the requirements change.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python) || { echo "Python 3.10+ is required."; read -r -p "Press Enter…"; exit 1; }
NEED=0
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting things up, one moment…"
  "$PY" -m venv .venv || { echo "Could not create the environment"; read -r -p "Press Enter…"; exit 1; }
  NEED=1
fi
SUM=$(cksum pyproject.toml | cut -d' ' -f1)
[ "$(cat .venv/.installed 2>/dev/null)" = "$SUM" ] || NEED=1
if [ "$NEED" = 1 ]; then
  echo "Installing or updating what noisyCricket needs…"
  .venv/bin/python -m pip install --quiet -e . || { echo "Install failed"; read -r -p "Press Enter…"; exit 1; }
  echo "$SUM" > .venv/.installed
fi
exec .venv/bin/python -m noisycricket "$@"
