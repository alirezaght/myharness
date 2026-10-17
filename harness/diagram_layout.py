"""Deterministic diagram geometry (P2-04): layered graphs, clipped arrows and scene extents.

Pure functions over names and integer pixels; the same input always gives the same layout.
No size caps (DEC-0015): a large design gives a large scene and the dashboard scrolls it.
"""

import math
import unicodedata
from collections import deque
from typing import Any

CHAR_W = 7
PAD = 12
MIN_W = 80
BOX_H = 36
H_GAP = 40
V_GAP = 60
MARGIN = 20
OFFSET = 8
LOOP = 24
SWEEPS = 4
DUMMY = "\x00"
DUMMY_W = 16

Box = tuple[int, int, int, int]
Point = list[int]


def display_len(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def box_width(label: str) -> int:
    return max(MIN_W, 2 * PAD + CHAR_W * display_len(label))


def _dedupe(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return list(dict.fromkeys(edges))


def _acyclic(nodes: list[str], edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Reverse the back edges of an iterative depth-first search (roots in node order)."""
    succ: dict[str, list[int]] = {v: [] for v in nodes}
    for index, (a, _) in enumerate(edges):
        succ[a].append(index)
    state, back = dict.fromkeys(nodes, 0), set()
    for root in nodes:
        if state[root]:
            continue
        state[root], stack = 1, [(root, 0)]
        while stack:
            v, i = stack[-1]
            if i < len(succ[v]):
                stack[-1] = (v, i + 1)
                index = succ[v][i]
                w = edges[index][1]
                if state[w] == 0:
                    state[w] = 1
                    stack.append((w, 0))
                elif state[w] == 1:
                    back.add(index)
            else:
                state[v] = 2
                stack.pop()
    return _dedupe([(b, a) if i in back else (a, b) for i, (a, b) in enumerate(edges)])


def _layers(nodes: list[str], dag: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path layering by Kahn's algorithm."""
    succ: dict[str, list[str]] = {v: [] for v in nodes}
    indegree = dict.fromkeys(nodes, 0)
    for a, b in dag:
        succ[a].append(b)
        indegree[b] += 1
    layer = dict.fromkeys(nodes, 0)
    queue = deque(v for v in nodes if indegree[v] == 0)
    processed = 0
    while queue:
        v = queue.popleft()
        processed += 1
        for w in succ[v]:
            layer[w] = max(layer[w], layer[v] + 1)
            indegree[w] -= 1
            if indegree[w] == 0:
                queue.append(w)
    assert processed == len(nodes), "cycle left after cycle breaking"
    return layer


def _sweep(rows: list[list[str]], links: dict[str, list[str]], order: range) -> None:
    pos = {v: i for row in rows for i, v in enumerate(row)}
    for index in order:
        row = rows[index]

        def bary(v: str) -> float:
            near = links[v]
            return sum(pos[u] for u in near) / len(near) if near else float(pos[v])

        row.sort(key=lambda v: (bary(v), pos[v], v))
        for i, v in enumerate(row):
            pos[v] = i


def _ordered(
    nodes: list[str], edges: list[tuple[str, str]]
) -> tuple[list[list[str]], dict[tuple[str, str], list[str]]]:
    """Rows of nodes and dummy nodes, plus the dummy chain of every edge spanning 2+ layers."""
    known = set(nodes)
    assert all(a in known and b in known for a, b in edges), "edge endpoint is not a node"
    assert not any(v.startswith(DUMMY) for v in known), "node name is reserved"
    nodes = list(dict.fromkeys(nodes))
    dag = _acyclic(nodes, _dedupe([(a, b) for a, b in edges if a != b]))
    layer = _layers(nodes, dag)
    every, links, chains = list(nodes), [], {}
    for a, b in dag:
        previous, chain = a, []
        for step in range(1, layer[b] - layer[a]):
            dummy = f"{DUMMY}{a}{DUMMY}{b}{DUMMY}{step}"
            layer[dummy] = layer[a] + step
            every.append(dummy)
            chain.append(dummy)
            links.append((previous, dummy))
            previous = dummy
        links.append((previous, b))
        if chain:
            chains[(a, b)] = chain
    rows: list[list[str]] = [[] for _ in range(max(layer.values(), default=-1) + 1)]
    for v in every:
        rows[layer[v]].append(v)
    preds: dict[str, list[str]] = {v: [] for v in every}
    succs: dict[str, list[str]] = {v: [] for v in every}
    for a, b in links:
        succs[a].append(b)
        preds[b].append(a)
    for _ in range(SWEEPS):
        _sweep(rows, preds, range(1, len(rows)))
        _sweep(rows, succs, range(len(rows) - 2, -1, -1))
    return rows, chains


def layered(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """{node: (layer, index)}: cycles broken, longest-path layers, barycenter ordering."""
    rows, _ = _ordered(nodes, edges)
    return {
        v: (i, j)
        for i, row in enumerate(rows)
        for j, v in enumerate(row)
        if not v.startswith(DUMMY)
    }


def place(rows: list[list[str]], widths: dict[str, int], x0: int, y0: int) -> dict[str, Box]:
    """Rows top to bottom, each centred against the widest row; boxes never overlap."""
    spans = [sum(widths[v] for v in row) + H_GAP * max(len(row) - 1, 0) for row in rows]
    widest = max(spans, default=0)
    boxes: dict[str, Box] = {}
    for index, (row, span) in enumerate(zip(rows, spans, strict=True)):
        x, y = x0 + (widest - span) // 2, y0 + index * (BOX_H + V_GAP)
        for v in row:
            boxes[v] = (x, y, widths[v], BOX_H)
            x += widths[v] + H_GAP
    return boxes


def graph_layout(
    nodes: list[str], edges: list[tuple[str, str]], widths: dict[str, int], x0: int, y0: int
) -> tuple[dict[str, Box], dict[tuple[str, str], list[Point]]]:
    """Boxes of the nodes and, per edge spanning 2+ layers, the waypoints it is routed along."""
    rows, chains = _ordered(nodes, edges)
    sizes = {v: DUMMY_W if v.startswith(DUMMY) else widths[v] for row in rows for v in row}
    placed = place(rows, sizes, x0, y0)
    waypoints = {}
    for (a, b), chain in chains.items():
        centres = [[x + w // 2, y + h // 2] for x, y, w, h in (placed[d] for d in chain)]
        waypoints[(a, b)] = centres
        waypoints.setdefault((b, a), list(reversed(centres)))
    boxes = {v: box for v, box in placed.items() if not v.startswith(DUMMY)}
    return boxes, waypoints


def graph_boxes(
    nodes: list[str], edges: list[tuple[str, str]], widths: dict[str, int], x0: int, y0: int
) -> dict[str, Box]:
    return graph_layout(nodes, edges, widths, x0, y0)[0]


def _centre(box: Box) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


def clip(box: Box, start: tuple[float, float], dx: float, dy: float) -> Point:
    """Where the ray from start (inside box) along (dx, dy) leaves the box."""
    _, _, w, h = box
    cx, cy = _centre(box)
    limits = []
    if dx:
        limits.append(((cx + math.copysign(w / 2, dx)) - start[0]) / dx)
    if dy:
        limits.append(((cy + math.copysign(h / 2, dy)) - start[1]) / dy)
    t = min(limits) if limits else 0.0
    return [round(start[0] + t * dx), round(start[1] + t * dy)]


def route(a: Box, b: Box, waypoints: list[Point] = (), offset: bool = False) -> list[Point]:
    """An arrow from border to border through waypoints; offset shifts it OFFSET px aside.

    The shift is perpendicular to the a-to-b direction, so opposite arrows move apart.
    """
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if not length:
        return [[round(ax), round(ay)], [round(bx), round(by)]]
    px, py = (-dy / length * OFFSET, dx / length * OFFSET) if offset else (0.0, 0.0)
    path = [(ax + px, ay + py), *((x + px, y + py) for x, y in waypoints), (bx + px, by + py)]
    (sx, sy), (nx, ny) = path[0], path[1]
    (ex, ey), (qx, qy) = path[-1], path[-2]
    middle = [[round(x), round(y)] for x, y in path[1:-1]]
    return [clip(a, path[0], nx - sx, ny - sy), *middle, clip(b, path[-1], qx - ex, qy - ey)]


def arrow_points(a: Box, b: Box, offset: bool = False) -> list[Point]:
    """A straight arrow between box borders (route without waypoints)."""
    return route(a, b, [], offset)


def self_loop(x: int, y: int, height: int) -> list[Point]:
    """A four-point loop off a right edge at x, from y down to y + height."""
    return [[x, y], [x + LOOP, y], [x + LOOP, y + height], [x, y + height]]


def box_loop(box: Box) -> list[Point]:
    x, y, w, h = box
    return self_loop(x + w, y + h // 4, h // 2)


def label_at(points: list[Point], along: float = 0.5) -> Point:
    """The point `along` the middle segment (default its midpoint), 4 px up.

    Opposite arrows put their labels at different fractions so the two never coincide.
    """
    i = (len(points) - 2) // 2
    (x1, y1), (x2, y2) = points[i], points[i + 1]
    return [round(x1 + (x2 - x1) * along), round(y1 + (y2 - y1) * along) - 4]


def extent(items: list[dict[str, Any]]) -> tuple[int, int]:
    """Scene width and height: every rectangle, point and text plus MARGIN."""
    right = bottom = 0
    for item in items:
        if "points" in item:
            for x, y in item["points"]:
                right, bottom = max(right, x), max(bottom, y)
        else:
            right = max(right, item["x"] + item["width"])
            bottom = max(bottom, item.get("bottom", item["y"] + item["height"]))
        if item.get("text_at"):
            x, y = item["text_at"]
            half = (CHAR_W * display_len(item["text"]) + 1) // 2
            right, bottom = max(right, x + half), max(bottom, y)
    return right + MARGIN, bottom + MARGIN


TEXT_H = 12


def _text_left(item: dict[str, Any]) -> int:
    return item["text_at"][0] - (CHAR_W * display_len(item["text"]) + 1) // 2


def fit(items: list[dict[str, Any]]) -> tuple[int, int]:
    """Shift every item so the top-left content sits at MARGIN; then the scene's extent."""
    lefts, tops = [], []
    for item in items:
        if "points" in item:
            lefts += [x for x, _ in item["points"]]
            tops += [y for _, y in item["points"]]
        else:
            lefts.append(item["x"])
            tops.append(item["y"])
        if item.get("text_at"):
            lefts.append(_text_left(item))
            tops.append(item["text_at"][1] - TEXT_H)
    dx, dy = MARGIN - min(lefts, default=MARGIN), MARGIN - min(tops, default=MARGIN)
    for item in items:
        if "points" in item:
            item["points"] = [[x + dx, y + dy] for x, y in item["points"]]
        else:
            item["x"], item["y"] = item["x"] + dx, item["y"] + dy
            if "bottom" in item:
                item["bottom"] += dy
        if item.get("text_at"):
            item["text_at"] = [item["text_at"][0] + dx, item["text_at"][1] + dy]
    return extent(items)
