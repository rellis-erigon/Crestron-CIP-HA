"""Install the Home Assistant stand-ins before any test module imports."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import _ha_stub  # noqa: E402

_ha_stub.install()
