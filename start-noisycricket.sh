#!/usr/bin/env bash
# Double-click (macOS: .command; Linux: "Run in terminal") to run noisyCricket from source.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python) || { echo "Python 3.10+ is required."; read -r -p "Press Enter…"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting things up, one moment…"
  "$PY" -m venv .venv && .venv/bin/python -m pip install --quiet -e . || { echo "Install failed"; read -r -p "Press Enter…"; exit 1; }
fi
exec .venv/bin/python -m noisycricket "$@"
