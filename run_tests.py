#!/usr/bin/env python3
"""noisyCricket test runner — runs the full test suite without external dependencies."""

import inspect
import sys
import unittest

# Ensure repo root is on python path
sys.path.insert(0, ".")

import tests.test_identity
import tests.test_pacing
import tests.test_relay
import tests.test_scheduler
import tests.test_tor_onion
import tests.test_controller
import tests.test_humanize

MODULES = [
    tests.test_identity,
    tests.test_pacing,
    tests.test_relay,
    tests.test_scheduler,
    tests.test_tor_onion,
    tests.test_controller,
    tests.test_humanize,
]


def run_all() -> int:
    passed = 0
    failed = 0
    errors = []

    for mod in MODULES:
        test_fns = [
            (name, fn)
            for name, fn in inspect.getmembers(mod, inspect.isfunction)
            if name.startswith("test_")
        ]
        print(f"\n[{mod.__name__}] ({len(test_fns)} tests)")
        for name, fn in test_fns:
            try:
                fn()
                passed += 1
                print(f"  ✓ {name}")
            except Exception as exc:
                failed += 1
                errors.append((f"{mod.__name__}.{name}", exc))
                print(f"  ✗ {name}: {exc}")

    print("\n" + "=" * 50)
    print(f"Summary: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for name, exc in errors:
            print(f"  - {name}: {exc}")
        return 1
    print("All tests passed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
