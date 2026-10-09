"""Every button on the add-on's page must reach a route that exists.

This is here because four features — panel import, the mixer card
builder, bulk configure and autogroup — were dead for a long time and
nothing caught it. Half the call sites in the page wrote the path as
`/api/joins/bulk` and half as `joins/bulk`, while the helper already
prepends `api/`. The first form produced `.../api//api/joins/bulk`, which
matches no route, so the request fell through to the catch-all GET that
serves the page and came back `405 Method Not Allowed`.

Nothing failed at build time, no test touched it, and the error named
neither the path nor the feature. The only way to find it was to click
the button and know what 405 meant.

These tests read the page and the server and insist they agree. They are
deliberately crude — a regex over both files — because the alternative is
starting a browser, and the failure being guarded against is a typo in a
string.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PAGE = (ROOT / "static" / "index.html").read_text()
SERVER = (ROOT / "src" / "server.py").read_text()


def routes() -> dict[str, set[str]]:
    """Every API route the server registers, as path -> {methods}."""
    found: dict[str, set[str]] = {}
    for pattern in (r'web\.(get|post)\("(/api/[^"]+)"',
                    r'add_(get|post)\("(/api/[^"]+)"'):
        for match in re.finditer(pattern, SERVER):
            found.setdefault(match.group(2), set()).add(match.group(1).upper())
    return found


def call_sites() -> list[tuple[str, str]]:
    """Every request the page makes, as (path, method)."""
    sites = []
    for match in re.finditer(r'call\("([^"]+)"(.{0,160})', PAGE, re.S):
        path = match.group(1).split("?")[0]
        method = "POST" if 'method: "POST"' in match.group(2) else "GET"
        sites.append((path, method))
    for match in re.finditer(r'EventSource\(api\("([^"]+)"\)\)', PAGE):
        sites.append((match.group(1).split("?")[0], "GET"))
    return sites


def test_the_page_makes_requests_at_all():
    # If the regex stops matching, every other test here passes vacuously.
    assert len(call_sites()) >= 10


def test_the_server_registers_routes_at_all():
    assert len(routes()) >= 10


@pytest.mark.parametrize("path,method", sorted(set(call_sites())))
def test_every_call_reaches_a_route(path, method):
    known = routes()
    full = "/api/" + path
    assert full in known, (
        f"the page calls {path!r}, which is no route. The helper prepends "
        f"'api/', so the path must not start with it."
    )
    assert method in known[full], (
        f"the page calls {path!r} with {method}; the server registers "
        f"{sorted(known[full])}. A path that exists for another method "
        f"returns 405, not 404, because the catch-all GET matches it."
    )


def test_no_call_site_repeats_the_api_prefix():
    # The helper tolerates it now, but writing it is still a mistake and
    # the tolerance is a safety net rather than a second spelling.
    offenders = re.findall(r'call\("(/?api/[^"]*)"', PAGE)
    assert not offenders, (
        f"these call sites include the api prefix the helper adds: {offenders}"
    )


def test_the_helper_strips_a_stray_prefix():
    # Belt and braces: the one line that makes the old spelling harmless.
    helper = re.search(r"const api = \(path\) =>([^;]+);", PAGE)
    assert helper, "the api() helper has moved or changed shape"
    assert "replace(" in helper.group(1), (
        "api() no longer normalises its path, so '/api/x' would once again "
        "produce 'api//api/x' and 405"
    )
