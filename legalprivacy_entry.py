"""PyInstaller entry point (a script outside the package so relative imports work)."""
from legalprivacy.launcher import main

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()      # a frozen app must never re-launch itself from a child process
    raise SystemExit(main())
