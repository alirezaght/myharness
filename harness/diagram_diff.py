"""The difference between two stored diagram models (P2-06): added, removed, changed elements.

Elements are matched by their stable id. An element in both models is changed when one of the
attributes the diagrams show differs; `sources` (which design entries stated it) never counts,
so withdrawing an entry and re-stating the same notation is no change. Messages are matched by
(from, to, text), occurrences paired in order: their id carries an ordinal that shifts when an
earlier message of the pair is added or removed, so a message is only ever added or removed.
Marks are per side: "from" holds removed and changed ids, "to" holds added and changed ids.
"""

from collections import defaultdict, deque
from collections.abc import Iterator, Mapping
from typing import Any

ATTRS = {
    "component": ("name", "declared", "descriptions"),
    "connection": ("from", "to", "labels"),
    "flow": ("from", "to", "labels"),
    "machine": ("name",),
    "state": ("name",),
    "transition": ("from", "to", "events"),
    "node": ("name",),
    "deployment": ("component", "node"),
}
LISTS = {
    "components": "component",
    "connections": "connection",
    "flows": "flow",
    "nodes": "node",
    "deployments": "deployment",
}
ORDER = ("removed", "changed", "added")


def _freeze(value: Any) -> Any:
    return tuple(_freeze(v) for v in value) if isinstance(value, list) else value


def _records(data: Mapping[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for key, kind in LISTS.items():
        for record in data.get(key, []):
            yield kind, record
    for machine in data.get("machines", []):
        yield "machine", machine
        for state in machine.get("states", []):
            yield "state", state
        for transition in machine.get("transitions", []):
            yield "transition", transition


def _flatten(data: Mapping[str, Any]) -> dict[str, tuple[str, tuple]]:
    """{id: (kind, shown attributes)} for every element except messages."""
    return {
        record["id"]: (kind, tuple(_freeze(record.get(a)) for a in ATTRS[kind]))
        for kind, record in _records(data)
    }


def _messages(old: Mapping[str, Any], new: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(removed old message ids, added new message ids), matched as a multiset."""
    pool: dict[tuple, deque[str]] = defaultdict(deque)
    for message in new.get("messages", []):
        pool[(message["from"], message["to"], message["text"])].append(message["id"])
    removed = []
    for message in old.get("messages", []):
        waiting = pool[(message["from"], message["to"], message["text"])]
        if waiting:
            waiting.popleft()
        else:
            removed.append(message["id"])
    left = {mid for ids in pool.values() for mid in ids}
    added = [m["id"] for m in new.get("messages", []) if m["id"] in left]
    return removed, added


def diff_models(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    old_labels: Mapping[str, str] | None = None,
    new_labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """{"changes": [...], "marks": {"from": {...}, "to": {...}}, "counts": {...}}."""
    a, b = _flatten(old), _flatten(new)
    found = [(i, a[i][0], "removed") for i in a.keys() - b.keys()]
    found += [(i, b[i][0], "added") for i in b.keys() - a.keys()]
    found += [(i, b[i][0], "changed") for i in a.keys() & b.keys() if a[i] != b[i]]
    gone, came = _messages(old, new)
    found += [(i, "message", "removed") for i in gone]
    found += [(i, "message", "added") for i in came]
    found.sort(key=lambda f: (ORDER.index(f[2]), f[0]))
    labels = {"removed": old_labels or {}, "changed": new_labels or {}, "added": new_labels or {}}
    changes = [
        {"id": i, "kind": kind, "label": labels[change].get(i, i), "change": change}
        for i, kind, change in found
    ]
    marks: dict[str, dict[str, str]] = {"from": {}, "to": {}}
    for i, _, change in found:
        if change != "added":
            marks["from"][i] = change
        if change != "removed":
            marks["to"][i] = change
    counts = {
        change: sum(f[2] == change for f in found) for change in ("added", "removed", "changed")
    }
    return {"changes": changes, "marks": marks, "counts": counts}
