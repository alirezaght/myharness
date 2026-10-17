"""The Diagram Service's structured model (P2-04): notation lines of effective design entries.

A line of an effective design element that starts (after an optional `- ` or `* ` bullet) with
one of the lowercase keywords below is a notation line; every other line is prose and is only
kept in the spec. Element ids are derived from the names alone, so they are stable across
versions; names are slugged case-insensitively (decomposed and precomposed Unicode spellings may
get different ids). Messages are the one exception: their id carries the 1-based ordinal of the
message for its sender/receiver pair, in statement order.

    component <name>[: <description>]
    connect <from> -> <to>[: <label>]
    message <from> -> <to>: <text>
    flow <from> -> <to>[: <data>]
    state <machine>: <from> -> <to>[: <event>]
    deploy <component> @ <node>
"""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from harness.design_models import DesignEntry, EntryKind

SCHEMA = 1
DIAGRAM_TYPES = ("component", "sequence", "data_flow", "state", "deployment")
TITLES = {
    "component": "Component diagram",
    "sequence": "Sequence diagram",
    "data_flow": "Data-flow diagram",
    "state": "State diagram",
    "deployment": "Deployment diagram",
}
KEYWORDS = "component|connect|message|flow|state|deploy"
STATEMENT = re.compile(rf"\s*(?:[-*]\s+)?({KEYWORDS})(?:\s+(.*?))?\s*")
_EDGE = r"(?P<a>[^:]+?)\s*->\s*(?P<b>[^:]+?)(?:\s*:\s*(?P<label>.*))?"
FORMS = {
    "component": re.compile(r"(?P<a>[^:]+?)(?:\s*:\s*(?P<label>.*))?"),
    "connect": re.compile(_EDGE),
    "message": re.compile(r"(?P<a>[^:]+?)\s*->\s*(?P<b>[^:]+?)\s*:\s*(?P<label>\S.*)"),
    "flow": re.compile(_EDGE),
    "state": re.compile(
        r"(?P<m>[^:]+?)\s*:\s*(?P<a>[^:]+?)\s*->\s*(?P<b>[^:]+?)(?:\s*:\s*(?P<label>.*))?"
    ),
    "deploy": re.compile(r"(?P<a>[^:@]+?)\s*@\s*(?P<b>[^:@]+?)"),
}
USAGE = {
    "component": "component <name>[: <description>]",
    "connect": "connect <from> -> <to>[: <label>]",
    "message": "message <from> -> <to>: <text>",
    "flow": "flow <from> -> <to>[: <data>]",
    "state": "state <machine>: <from> -> <to>[: <event>]",
    "deploy": "deploy <component> @ <node>",
}


@dataclass(frozen=True)
class NotationIssue:
    ref: str
    line: int
    text: str
    problem: str


def slug(name: str) -> str:
    return re.sub(r"[\W_]+", "-", name.casefold()).strip("-")


def _name(raw: str | None) -> str | None:
    name = " ".join((raw or "").split())
    return None if not name or "->" in name or not slug(name) else name


def _label(raw: str | None) -> str | None:
    return (raw or "").strip() or None


def _add(values: list[str], value: str | None) -> None:
    if value is not None and value not in values:
        values.append(value)


def relevant_types(data: dict[str, Any]) -> list[str]:
    present = {
        "component": any(c["declared"] for c in data["components"]) or bool(data["connections"]),
        "sequence": bool(data["messages"]),
        "data_flow": bool(data["flows"]),
        "state": bool(data["machines"]),
        "deployment": bool(data["deployments"]),
    }
    return [kind for kind in DIAGRAM_TYPES if present[kind]]


@dataclass(frozen=True)
class DiagramModel:
    data: dict[str, Any]
    issues: tuple[NotationIssue, ...] = field(default=())

    @property
    def types(self) -> list[str]:
        return relevant_types(self.data)

    @property
    def empty(self) -> bool:
        return not self.types

    @property
    def canonical_json(self) -> str:
        return json.dumps(self.data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


class _Builder:
    def __init__(self) -> None:
        self.components: dict[str, dict[str, Any]] = {}
        self.connections: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []
        self.pairs: dict[tuple[str, str], int] = {}
        self.flows: dict[str, dict[str, Any]] = {}
        self.machines: dict[str, dict[str, Any]] = {}
        self.nodes: dict[str, dict[str, Any]] = {}
        self.deployments: dict[str, dict[str, Any]] = {}

    def component(self, name: str, ref: str, declared: bool, description: str | None) -> str:
        cid = f"component:{slug(name)}"
        rec = self.components.setdefault(
            cid, {"id": cid, "name": name, "declared": False, "descriptions": [], "sources": []}
        )
        if declared and not rec["declared"]:
            rec["name"], rec["declared"] = name, True
        _add(rec["descriptions"], description)
        _add(rec["sources"], ref)
        return cid

    def _edge(self, store: dict, kind: str, a: str, b: str, ref: str, label: str | None) -> None:
        src, dst = self.component(a, ref, False, None), self.component(b, ref, False, None)
        eid = f"{kind}:{slug(a)}->{slug(b)}"
        rec = store.setdefault(
            eid, {"id": eid, "from": src, "to": dst, "labels": [], "sources": []}
        )
        _add(rec["labels"], label)
        _add(rec["sources"], ref)

    def message(self, a: str, b: str, ref: str, text: str) -> None:
        src, dst = self.component(a, ref, False, None), self.component(b, ref, False, None)
        pair = (slug(a), slug(b))
        self.pairs[pair] = self.pairs.get(pair, 0) + 1
        mid = f"message:{pair[0]}->{pair[1]}:{self.pairs[pair]}"
        self.messages.append({"id": mid, "from": src, "to": dst, "text": text, "sources": [ref]})

    def transition(self, m: str, a: str, b: str, ref: str, event: str | None) -> None:
        mslug = slug(m)
        machine = self.machines.setdefault(
            mslug,
            {"id": f"machine:{mslug}", "name": m, "sources": [], "states": {}, "transitions": {}},
        )
        _add(machine["sources"], ref)
        ends = []
        for name in (a, b):
            sid = f"state:{mslug}/{slug(name)}"
            state = machine["states"].setdefault(sid, {"id": sid, "name": name, "sources": []})
            _add(state["sources"], ref)
            ends.append(sid)
        tid = f"transition:{mslug}/{slug(a)}->{slug(b)}"
        rec = machine["transitions"].setdefault(
            tid, {"id": tid, "from": ends[0], "to": ends[1], "events": [], "sources": []}
        )
        _add(rec["events"], event)
        _add(rec["sources"], ref)

    def deploy(self, c: str, n: str, ref: str) -> None:
        cid = self.component(c, ref, False, None)
        nid = f"node:{slug(n)}"
        node = self.nodes.setdefault(nid, {"id": nid, "name": n, "sources": []})
        _add(node["sources"], ref)
        did = f"deployment:{slug(c)}@{slug(n)}"
        rec = self.deployments.setdefault(
            did, {"id": did, "component": cid, "node": nid, "sources": []}
        )
        _add(rec["sources"], ref)

    def apply(self, kw: str, match: re.Match, ref: str) -> bool:
        """Record one well-formed statement; False (nothing recorded) for an invalid name."""
        groups = match.groupdict()
        names = {key: _name(groups[key]) for key in ("a", "b", "m") if key in groups}
        if any(name is None for name in names.values()):
            return False
        a, b, label = names["a"], names.get("b"), _label(groups.get("label"))
        if kw == "component":
            self.component(a, ref, True, label)
        elif kw == "connect":
            self._edge(self.connections, "connection", a, b, ref, label)
        elif kw == "flow":
            self._edge(self.flows, "flow", a, b, ref, label)
        elif kw == "message":
            self.message(a, b, ref, label)
        elif kw == "state":
            self.transition(names["m"], a, b, ref, label)
        else:
            self.deploy(a, b, ref)
        return True

    def data(self) -> dict[str, Any]:
        def ordered(store: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
            return [store[key] for key in sorted(store)]

        machines = [
            {**m, "states": ordered(m["states"]), "transitions": ordered(m["transitions"])}
            for m in sorted(self.machines.values(), key=lambda m: m["id"])
        ]
        return {
            "schema": SCHEMA,
            "components": ordered(self.components),
            "connections": ordered(self.connections),
            "messages": list(self.messages),
            "flows": ordered(self.flows),
            "machines": machines,
            "nodes": ordered(self.nodes),
            "deployments": ordered(self.deployments),
        }


def build_model(entries: Iterable[DesignEntry]) -> DiagramModel:
    """The model of the effective design elements, in entry id order, with notation issues."""
    builder, issues = _Builder(), []
    for entry in sorted(entries, key=lambda e: e.id):
        if not entry.effective or entry.kind != EntryKind.ELEMENT:
            continue
        for lineno, line in enumerate(entry.text.splitlines(), 1):
            statement = STATEMENT.fullmatch(line)
            if statement is None:
                continue
            kw, rest = statement.group(1), statement.group(2) or ""
            match = FORMS[kw].fullmatch(rest)
            if match is None or not builder.apply(kw, match, entry.ref):
                problem = f"{kw}: expected {USAGE[kw]}"
                issues.append(NotationIssue(entry.ref, lineno, line.strip(), problem))
    return DiagramModel(builder.data(), tuple(issues))
