import sys

try:
    import pytest
except ImportError:
    class _RaisesContext:
        def __init__(self, exc):
            self.exc = exc

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                raise AssertionError(f"DID NOT RAISE {self.exc}")
            return issubclass(exc_type, self.exc)

    import types
    shim = types.ModuleType("pytest")
    shim.raises = _RaisesContext
    sys.modules["pytest"] = shim
