"""Run the pytest-style panel tests without pytest.

pip cannot run in the Claude Code add-on container, so pytest is not
available there. The panel tests use nothing from it but `raises`; this
supplies that and runs every `test_` function. CI uses real pytest.
"""
import contextlib
import importlib
import os
import sys
import traceback
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "pytest" not in sys.modules:
    try:
        import pytest  # noqa: F401
    except ImportError:
        shim = types.ModuleType("pytest")

        class _Info:
            value = None

        @contextlib.contextmanager
        def raises(kind, match=None):
            info = _Info()
            try:
                yield info
            except kind as exc:
                info.value = exc
                if match is not None:
                    import re
                    assert re.search(match, str(exc)), f"{exc!r} does not match {match!r}"
                return
            raise AssertionError(f"did not raise {kind.__name__}")

        shim.raises = raises
        sys.modules["pytest"] = shim

passed = failed = 0
for name in sys.argv[1:] or ["test_panel_reader", "test_panel_faceplate"]:
    module = importlib.import_module(name)
    for test in [v for k, v in vars(module).items() if k.startswith("test_") and callable(v)]:
        try:
            test()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAIL {name}.{test.__name__}")
            traceback.print_exc(limit=3)
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
