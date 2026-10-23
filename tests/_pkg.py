"""Make custom_components/crestron_cip importable without Home Assistant.

The package's __init__ imports Home Assistant; the protocol, client and
project loader do not. Registering the package by path skips __init__, so
these tests run on plain Python.
"""
import os
import sys
import types

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "custom_components", "crestron_cip")

if "crestron_cip" not in sys.modules:
    _pkg = types.ModuleType("crestron_cip")
    _pkg.__path__ = [_DIR]
    sys.modules["crestron_cip"] = _pkg
