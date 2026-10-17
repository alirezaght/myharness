"""Diagram scenes (P2-04): one JSON scene per relevant diagram type of a stored model.

A scene is {type, title, width, height, items}; items come frames, lifelines, boxes, arrows.
Every item carries its model element's stable id, so the page and later comments can address
it. The same component id may appear in several scenes; ids are unique within a scene.
"""

from typing import Any

from harness.diagram_layout import (
    BOX_H,
    H_GAP,
    LOOP,
    MARGIN,
    PAD,
    V_GAP,
    Box,
    box_loop,
    box_width,
    fit,
    graph_layout,
    label_at,
    route,
    self_loop,
)
from harness.diagram_model import TITLES, relevant_types

ROW = 44
FRAME_HEAD = 28
Item = dict[str, Any]


def _item(record: dict[str, Any], kind: str, shape: str, label: str, **extra: Any) -> Item:
    item = {"id": record["id"], "kind": kind, "shape": shape, "label": label, "text": None}
    return item | {"sources": list(record["sources"]), "detail": {}} | extra


def _rect(box: Box) -> dict[str, int]:
    x, y, w, h = box
    return {"x": x, "y": y, "width": w, "height": h}


def _component_box(component: dict[str, Any], box: Box) -> Item:
    detail = {"declared": component["declared"], "descriptions": component["descriptions"]}
    return _item(component, "component", "box", component["name"], detail=detail) | _rect(box)


def _arrow(
    record: dict[str, Any], kind: str, label: str, text: str | None, points, along: float = 0.5
) -> Item:
    item = _item(record, kind, "arrow", label, points=points)
    item["text"] = text
    if text:
        item["text_at"] = label_at(points, along)
    return item


def _edge_points(
    edges: list[dict[str, Any]], boxes: dict[str, Box], waypoints: dict
) -> list[tuple[list[list[int]], float]]:
    """Each edge's points and where along them its label sits (apart for opposite pairs)."""
    pairs = {(e["from"], e["to"]) for e in edges}
    placed = []
    for e in edges:
        a, b = e["from"], e["to"]
        if a == b:
            placed.append((box_loop(boxes[a]), 0.5))
        else:
            opposite = (b, a) in pairs
            points = route(boxes[a], boxes[b], waypoints.get((a, b), []), opposite)
            placed.append((points, 0.3 if opposite else 0.5))
    return placed


def _graph(
    components: list[dict[str, Any]], edges: list[dict[str, Any]], kind: str, x0: int, y0: int
) -> list[Item]:
    names = {c["id"]: c["name"] for c in components}
    ids = [c["id"] for c in components]
    widths = {c["id"]: box_width(c["name"]) for c in components}
    boxes, waypoints = graph_layout(ids, [(e["from"], e["to"]) for e in edges], widths, x0, y0)
    items = [_component_box(c, boxes[c["id"]]) for c in components]
    placed = _edge_points(edges, boxes, waypoints)
    for edge, (points, along) in zip(edges, placed, strict=True):
        label = f"{names[edge['from']]} -> {names[edge['to']]}"
        text = "; ".join(edge["labels"]) or None
        detail = {"from": names[edge["from"]], "to": names[edge["to"]], "labels": edge["labels"]}
        items.append(_arrow(edge, kind, label, text, points, along) | {"detail": detail})
    return items


def _component_scene(data: dict[str, Any]) -> list[Item]:
    ends = {end for c in data["connections"] for end in (c["from"], c["to"])}
    shown = [c for c in data["components"] if c["declared"] or c["id"] in ends]
    return _graph(shown, data["connections"], "connection", MARGIN, MARGIN)


def _data_flow_scene(data: dict[str, Any]) -> list[Item]:
    ends = {end for f in data["flows"] for end in (f["from"], f["to"])}
    shown = [c for c in data["components"] if c["id"] in ends]
    return _graph(shown, data["flows"], "flow", MARGIN, MARGIN)


def _sequence_scene(data: dict[str, Any]) -> list[Item]:
    components = {c["id"]: c for c in data["components"]}
    order = list(dict.fromkeys(end for m in data["messages"] for end in (m["from"], m["to"])))
    bottom = MARGIN + BOX_H + (len(data["messages"]) + 1) * ROW
    lifelines, centres, x = [], {}, MARGIN
    for cid in order:
        component, width = components[cid], box_width(components[cid]["name"])
        rect = {"x": x, "y": MARGIN, "width": width, "height": BOX_H, "bottom": bottom}
        detail = {"declared": component["declared"], "descriptions": component["descriptions"]}
        lifelines.append(
            _item(component, "component", "lifeline", component["name"], detail=detail) | rect
        )
        centres[cid], x = x + width // 2, x + width + H_GAP
    arrows = []
    for index, message in enumerate(data["messages"]):
        y = MARGIN + BOX_H + (index + 1) * ROW
        a, b = centres[message["from"]], centres[message["to"]]
        points = self_loop(a, y, ROW // 2) if a == b else [[a, y], [b, y]]
        source, target = components[message["from"]]["name"], components[message["to"]]["name"]
        detail = {"from": source, "to": target, "order": index + 1}
        arrow = _arrow(message, "message", f"{source} -> {target}", message["text"], points)
        arrows.append(arrow | {"detail": detail})
    return lifelines + arrows


def _state_scene(data: dict[str, Any]) -> list[Item]:
    frames, inner, y = [], [], MARGIN
    for machine in data["machines"]:
        states = {s["id"]: s for s in machine["states"]}
        ids = list(states)
        widths = {sid: box_width(states[sid]["name"]) for sid in ids}
        edges = [(t["from"], t["to"]) for t in machine["transitions"]]
        top = y + FRAME_HEAD
        boxes, waypoints = graph_layout(ids, edges, widths, MARGIN + PAD, top)
        right = max(bx + bw for bx, _, bw, _ in boxes.values()) + LOOP + PAD
        width = max(right - MARGIN, box_width(machine["name"]))
        height = max(by + bh for _, by, _, bh in boxes.values()) + PAD - y
        frame = _item(machine, "machine", "frame", machine["name"])
        frames.append(frame | {"x": MARGIN, "y": y, "width": width, "height": height})
        for sid in ids:
            inner.append(
                _item(states[sid], "state", "box", states[sid]["name"]) | _rect(boxes[sid])
            )
        arrows = _edge_points(machine["transitions"], boxes, waypoints)
        for t, (points, along) in zip(machine["transitions"], arrows, strict=True):
            source, target = states[t["from"]]["name"], states[t["to"]]["name"]
            detail = {"machine": machine["name"], "from": source, "to": target}
            detail |= {"events": t["events"]}
            text = "; ".join(t["events"]) or None
            arrow = _arrow(t, "transition", f"{source} -> {target}", text, points, along)
            inner.append(arrow | {"detail": detail})
        y += height + V_GAP // 2
    boxes_first = [i for i in inner if i["shape"] == "box"]
    return frames + boxes_first + [i for i in inner if i["shape"] == "arrow"]


def _deployment_scene(data: dict[str, Any]) -> list[Item]:
    components = {c["id"]: c for c in data["components"]}
    frames, boxes, x = [], [], MARGIN
    for node in data["nodes"]:
        placed = [d for d in data["deployments"] if d["node"] == node["id"]]
        names = [components[d["component"]]["name"] for d in placed]
        inner = max([box_width(node["name"]), *(box_width(n) for n in names)])
        height = FRAME_HEAD + len(placed) * (BOX_H + PAD) + PAD
        frames.append(
            _item(node, "node", "frame", node["name"])
            | {"x": x, "y": MARGIN, "width": inner + 2 * PAD, "height": height}
        )
        for index, (deployment, name) in enumerate(zip(placed, names, strict=True)):
            component = components[deployment["component"]]
            rect = (x + PAD, MARGIN + FRAME_HEAD + index * (BOX_H + PAD), box_width(name), BOX_H)
            detail = {"component": name, "node": node["name"], "declared": component["declared"]}
            boxes.append(_item(deployment, "deployment", "box", name, detail=detail) | _rect(rect))
        x += inner + 2 * PAD + H_GAP
    return frames + boxes


SCENES = {
    "component": _component_scene,
    "sequence": _sequence_scene,
    "data_flow": _data_flow_scene,
    "state": _state_scene,
    "deployment": _deployment_scene,
}


def render_scenes(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One scene per relevant type, in DIAGRAM_TYPES order; an empty model gives none."""
    scenes = []
    for kind in relevant_types(data):
        items = SCENES[kind](data)
        width, height = fit(items)
        scenes.append(
            {"type": kind, "title": TITLES[kind], "width": width, "height": height, "items": items}
        )
    return scenes
