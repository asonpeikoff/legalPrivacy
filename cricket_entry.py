"""PyInstaller entry point (a script outside the package so relative imports work)."""
from noisycricket.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
