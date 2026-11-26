"""One contract for how the dashboard page loads its static assets.

It replaces the overlapping per-feature checks that each re-tested a subset of scripts
(comments, diagrams and the redesign scripts) and keeps their union: every script and
stylesheet the page references is served verbatim with its MIME type, the scripts that
depend on each other load in order, app.js loads last, and no two classic scripts
declare the same global (they share one global scope, so a collision is a load error).
"""

import re

from harness.dashboard.server import STATIC_DIR
from tests.test_dashboard import fetch, serve

# Top-level declarations of a classic script; they all share the page's global scope.
DECLARED = re.compile(r"^(?:const|let|var|class|(?:async )?function)\s+(\w+)", re.MULTILINE)
ANY_STATIC = re.compile(r'(?:src|href)="/static/([^"]+)"')
SCRIPT_TAG = re.compile(r'<script src="/static/([\w.]+\.js)" defer></script>')
STYLE_TAG = re.compile(r'<link rel="stylesheet" href="/static/([\w.]+\.css)">')
MIME = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
# Load-order dependencies: each script uses globals declared by the ones before it.
ORDERED = (
    "diagrams.js",
    "comments.js",
    "comment_actions.js",
    "compare.js",
    "approvals.js",
    "execution.js",
    "app.js",
)


def test_every_page_asset_is_served_verbatim_in_order_without_duplicate_globals(db_path):
    with serve(db_path, None) as server:
        page = fetch(server, "/")[2].decode("utf-8")
        scripts = SCRIPT_TAG.findall(page)
        styles = STYLE_TAG.findall(page)
        served = {name: fetch(server, f"/static/{name}") for name in [*scripts, *styles]}

    # Every /static/ reference must be one of the two recognised tag forms, so an asset
    # cannot drop out of this inventory by being written differently.
    assert sorted(ANY_STATIC.findall(page)) == sorted([*scripts, *styles])
    assert scripts and styles
    assert len(scripts) == len(set(scripts)) and len(styles) == len(set(styles))
    assert set(ORDERED) <= set(scripts), sorted(set(ORDERED) - set(scripts))
    assert [name for name in scripts if name in ORDERED] == list(ORDERED)
    assert scripts[-1] == "app.js"
    for name, (status, headers, body) in served.items():
        assert status == 200, name
        assert headers["Content-Type"] == MIME[name[name.rindex(".") :]], name
        assert body == (STATIC_DIR / name).read_bytes(), name

    seen: dict[str, str] = {}
    for name in scripts:
        for declared in DECLARED.findall(served[name][2].decode("utf-8")):
            assert declared not in seen, f"{declared} in {name} and {seen[declared]}"
            seen[declared] = name
