"""The entry point imports and its helpers exist.

Nothing else here imports main.py, so a call to a function that was
never defined passed every test and then took the add-on down on
startup with a NameError. This is the cheapest guard against that.
"""
import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

MAIN = SRC / "main.py"


@pytest.fixture(scope="module")
def tree():
    return ast.parse(MAIN.read_text())


def test_main_imports():
    import main  # noqa: F401


def test_every_function_it_calls_at_module_scope_exists(tree):
    """Catches a call inserted without its definition."""
    defined = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update((a.asname or a.name).split(".")[0] for a in node.names)

    import builtins
    known = defined | imported | set(dir(builtins))
    missing = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in known:
                missing.add(node.func.id)
    assert not missing, f"called but never defined: {sorted(missing)}"


def test_it_re_applies_a_panel_assignment(tree):
    """A panel's processor lives in the panel store, so startup is the
    only thing that can bring it back after a restart."""
    import main
    assert hasattr(main, "_attach_panels")
