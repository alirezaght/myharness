"""P2-04: deterministic layouts — termination on cycles, no overlaps, frames and bounds."""

import itertools
import random

from harness.design_models import (
    Binding,
    DesignEntry,
    DesignSection,
    EntryKind,
    EntryOrigin,
    EntryStatus,
)
from harness.diagram_layout import (
    BOX_H,
    arrow_points,
    box_width,
    display_len,
    graph_boxes,
    layered,
)
from harness.diagram_model import build_model
from harness.diagram_scenes import render_scenes
from tests.sample_design import (
    COMMENT_STATES,
    COMPONENTS,
    DATA_FLOW,
    DEPLOYMENT,
    INTERACTIONS,
    TYPES,
)


def entry(entry_id: int, text: str) -> DesignEntry:
    return DesignEntry(
        id=entry_id,
        task_id="t-00000001",
        section=DesignSection.COMPONENTS,
        kind=EntryKind.ELEMENT,
        origin=EntryOrigin.DEVELOPER,
        binding=Binding.FIXED,
        statement=text,
        rationale=None,
        status=EntryStatus.STATED,
        response=None,
        created_at="2026-01-01T00:00:00+00:00",
        resolved_at=None,
    )


def sample_data() -> dict:
    texts = (COMPONENTS, INTERACTIONS, DATA_FLOW, COMMENT_STATES, DEPLOYMENT)
    return build_model([entry(i, t) for i, t in enumerate(texts, 1)]).data


def large_data(count: int = 120) -> dict:
    rng = random.Random(4)
    lines = [f"component Service {n}" for n in range(count)]
    lines += [f"connect Service {n} -> Service {n + 1}" for n in range(count - 1)]
    lines += [
        f"connect Service {rng.randrange(count)} -> Service {rng.randrange(count)}"
        for _ in range(count)
    ]
    lines += [f"message Service {n} -> Service {(n * 7) % count}: call {n}" for n in range(count)]
    lines += [f"flow Service {n} -> Service {(n + 3) % count}: rows" for n in range(count)]
    lines += [f"state Machine {n % 3}: s{n} -> s{(n + 1) % 40}: go" for n in range(count)]
    lines += [f"deploy Service {n} @ Host {n % 7}" for n in range(count)]
    return build_model([entry(1, "\n".join(lines))]).data


def overlaps(a: tuple, b: tuple) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def rect(item: dict) -> tuple:
    return item["x"], item["y"], item["width"], item["height"]


def test_layered_terminates_on_cycles_and_places_every_node_once():
    nodes = [f"n{i}" for i in range(8)]
    graphs = {
        "self-loop": (["a"], [("a", "a")]),
        "two-cycle": (["a", "b"], [("a", "b"), ("b", "a")]),
        "three-cycle": (["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")]),
        "complete-8": (nodes, list(itertools.permutations(nodes, 2))),
        "chain-120": ([f"c{i}" for i in range(120)], [(f"c{i}", f"c{i + 1}") for i in range(119)]),
        "empty": ([], []),
    }
    rng = random.Random(7)
    big = [f"r{i}" for i in range(120)]
    graphs["random-120"] = (big, [(rng.choice(big), rng.choice(big)) for _ in range(400)])
    for name, (graph_nodes, edges) in graphs.items():
        positions = layered(graph_nodes, edges)
        assert sorted(positions) == sorted(graph_nodes), name
        assert len(set(positions.values())) == len(graph_nodes), name
        assert layered(graph_nodes, edges) == positions, name
        widths = {v: box_width(v) for v in graph_nodes}
        boxes = list(graph_boxes(graph_nodes, edges, widths, 20, 20).values())
        assert not any(overlaps(a, b) for a, b in itertools.combinations(boxes, 2)), name
    chain = layered(*graphs["chain-120"])
    assert [chain[f"c{i}"][0] for i in range(120)] == list(range(120))


def test_opposite_arrows_are_offset_and_clipped_to_the_borders():
    a, b = (0, 0, 100, BOX_H), (0, 200, 100, BOX_H)
    there, back = arrow_points(a, b, True), arrow_points(b, a, True)
    assert there != [list(reversed(p)) for p in back] and there[0][0] != back[1][0]
    assert there[0][1] == BOX_H and there[1][1] == 200
    plain = arrow_points(a, b)
    assert plain == [[50, BOX_H], [50, 200]]
    assert display_len("数据") == 4 and box_width("数" * 30) > box_width("a" * 30)


def check_scene(scene: dict) -> None:
    items = scene["items"]
    seen = [item["id"] for item in items]
    assert len(seen) == len(set(seen)), scene["type"]
    order = {"frame": 0, "lifeline": 1, "box": 2, "arrow": 3}
    assert [order[i["shape"]] for i in items] == sorted(order[i["shape"]] for i in items)
    boxes = [rect(i) for i in items if i["shape"] in ("box", "lifeline")]
    assert not any(overlaps(a, b) for a, b in itertools.combinations(boxes, 2)), scene["type"]
    for item in items:
        assert {"id", "kind", "shape", "label", "text", "sources", "detail"} <= set(item)
        assert item["sources"], item["id"]
        if item["shape"] == "arrow":
            assert all(0 <= x <= scene["width"] and 0 <= y <= scene["height"]
                       for x, y in item["points"])  # fmt: skip
        else:
            assert item["x"] >= 0 and item["y"] >= 0
            assert item["x"] + item["width"] <= scene["width"]
            assert item.get("bottom", item["y"] + item["height"]) <= scene["height"]
    for frame in (i for i in items if i["shape"] == "frame"):
        fx, fy, fw, fh = rect(frame)
        inside = [
            i for i in items if i["shape"] == "box" and i["id"].split(":")[0] in ("state",
            "deployment")
        ]  # fmt: skip
        mine = [
            i
            for i in inside
            if (frame["kind"] == "machine" and i["id"].startswith(f"state:{frame['id'][8:]}/"))
            or (frame["kind"] == "node" and i["detail"].get("node") == frame["label"])
        ]
        assert mine, frame["id"]
        for box in mine:
            x, y, w, h = rect(box)
            assert fx <= x and fy <= y and x + w <= fx + fw and y + h <= fy + fh, box["id"]


def _near(box: tuple, point: list[int]) -> bool:
    x, y, w, h = box
    return x - 1 <= point[0] <= x + w + 1 and y - 1 <= point[1] <= y + h + 1


def crossed_boxes(scene: dict) -> list[tuple[str, str]]:
    """Arrows of a graph scene that run through a box other than their own two ends."""
    boxes = [(i["id"], rect(i)) for i in scene["items"] if i["shape"] == "box"]
    crossed = []
    for arrow in (i for i in scene["items"] if i["shape"] == "arrow"):
        ends = (arrow["points"][0], arrow["points"][-1])
        others = [(bid, b) for bid, b in boxes if not any(_near(b, end) for end in ends)]
        for (x1, y1), (x2, y2) in itertools.pairwise(arrow["points"]):
            steps = max(abs(x2 - x1), abs(y2 - y1), 1)
            for k in range(steps + 1):
                px, py = x1 + (x2 - x1) * k / steps, y1 + (y2 - y1) * k / steps
                for bid, (bx, by, bw, bh) in others:
                    if bx < px < bx + bw and by < py < by + bh:
                        crossed.append((arrow["id"], bid))
    return sorted(set(crossed))


def test_sample_scenes_are_complete_and_deterministic():
    data = sample_data()
    scenes = render_scenes(data)
    assert [s["type"] for s in scenes] == TYPES
    assert render_scenes(sample_data()) == scenes
    for scene in scenes:
        check_scene(scene)
        assert crossed_boxes(scene) == [], scene["type"]
    by_type = {s["type"]: {i["id"] for i in s["items"]} for s in scenes}
    model_ids = {c["id"] for c in data["connections"]} | {m["id"] for m in data["messages"]}
    model_ids |= {f["id"] for f in data["flows"]} | {d["id"] for d in data["deployments"]}
    model_ids |= {m["id"] for m in data["machines"]}
    model_ids |= {s["id"] for m in data["machines"] for s in m["states"]}
    model_ids |= {t["id"] for m in data["machines"] for t in m["transitions"]}
    model_ids |= {n["id"] for n in data["nodes"]}
    assert model_ids <= set().union(*by_type.values())
    assert "component:design-entries" not in by_type["component"]
    assert "component:design-entries" in by_type["data_flow"]


def test_a_large_design_renders_every_element_without_overlap():
    data = large_data()
    scenes = render_scenes(data)
    assert [s["type"] for s in scenes] == TYPES
    for scene in scenes:
        check_scene(scene)
    component = next(s for s in scenes if s["type"] == "component")
    expected = len(data["components"]) + len(data["connections"])
    assert len(component["items"]) == expected
    sequence = next(s for s in scenes if s["type"] == "sequence")
    assert sum(i["kind"] == "message" for i in sequence["items"]) == 120
    assert render_scenes(large_data()) == scenes


def test_long_edges_are_routed_around_boxes():
    text = "state Comment: open -> addressed\nstate Comment: addressed -> resolved\n"
    text += "state Comment: resolved -> open\nconnect A -> B\nconnect B -> C\nconnect A -> C"
    scenes = render_scenes(build_model([entry(1, text)]).data)
    for scene in scenes:
        check_scene(scene)
        assert crossed_boxes(scene) == [], scene["type"]
    wanted = ("connection:a->c", "transition:comment/open->addressed")
    long_edges = [i for s in scenes for i in s["items"] if i["id"] in wanted]
    assert len(long_edges) == 2 and all(len(i["points"]) == 3 for i in long_edges)


def test_an_empty_model_has_no_scenes():
    assert render_scenes(build_model([]).data) == []
