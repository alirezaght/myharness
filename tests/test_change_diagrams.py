"""Change diagrams represent current approved sources without altering approval."""

import copy

import pytest

from harness.change_diagrams import binding, publish, reference_view
from harness.db import transaction
from harness.design_models import DesignSection
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution
from tests.execution_helpers import approved_execution


def prose(controller, task):
    with transaction(controller.conn):
        a = controller.workspace(task).state_design(
            DesignSection.COMPONENTS,
            "Notification service stores provider events; Web forwards signed callbacks.",
        )
        b = controller.workspace(task).state_design(
            DesignSection.DATA_FLOW, "Provider events are imported into the warehouse once a day."
        )
    return a.ref, b.ref


def approved_with_prose(controller, sample_repo, monkeypatch):
    from tests import complete_task

    design = complete_task._design

    def with_prose(controller, task_id):
        prose(controller, task_id)
        design(controller, task_id)

    monkeypatch.setattr(complete_task, "_design", with_prose)
    tid, eid = approved_execution(controller, sample_repo, monkeypatch)
    refs = tuple(
        e.ref
        for e in controller.workspace(tid).design_entries()
        if e.effective and str(e.kind) == "element"
    )
    return tid, eid, refs


def content(controller, execution, refs):
    source = binding(controller, execution)
    return {
        "spec_hash": source["spec_hash"],
        "design_hash": source["design_hash"],
        "caption": "Signed callback changes",
        "parts": [
            {
                "sources": [refs[0]],
                "notation": "component Web\ncomponent Notification service",
                "change": "changed",
                "inferred": False,
                "explanation": "Existing services gain callback handling",
            },
            {
                "sources": [refs[0]],
                "notation": "flow Web -> Notification service: exact signed body",
                "change": "new",
                "inferred": False,
                "explanation": "Web forwards the signed request",
            },
            {
                "sources": [refs[1]],
                "notation": "flow Notification service -> Warehouse: daily import",
                "change": "new",
                "inferred": True,
                "explanation": "Illustrative storage boundary inferred from daily warehouse import",
            },
        ],
    }


def test_prose_always_has_a_read_only_initial_change_scope(controller):
    task = controller.create_task("Track signed email callbacks")
    refs = prose(controller, task.id)
    before = controller.workspace(task.id).events()
    view = reference_view(controller, task)
    assert view["kind"] == "change_scope" and view["scenes"]
    assert set(refs) <= view["sources"].keys()
    assert any(item["label"] == "Data flow" for scene in view["scenes"] for item in scene["items"])
    assert controller.workspace(task.id).events() == before
    assert controller.get_task(task.id).phase == task.phase


def test_published_flow_is_exactly_source_bound_and_preserves_spec(
    controller, sample_repo, monkeypatch
):
    tid, eid, refs = approved_with_prose(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    original = copy.deepcopy(execution)
    request = content(controller, execution, refs)
    result = publish(controller, execution, request)
    assert result["specification_changed"] is False and result["approval_granted"] is False
    assert get_execution(controller.conn, eid) == original
    view = reference_view(controller, controller.get_task(tid))
    assert view["kind"] == "logical_change" and "reference" not in view["caption"].lower()
    edge = view["elements"]["flow:notification-service->warehouse"]
    assert edge["inferred"] is True and edge["change"] == "new"
    assert edge["sources"] == [refs[1]]
    with transaction(controller.conn):
        controller.workspace(tid).state_design(
            DesignSection.COMPONENTS, "Add another approved behavior"
        )
    assert reference_view(controller, controller.get_task(tid))["kind"] == "change_scope"
    with pytest.raises(ExecutionConflict, match="source changed"):
        publish(controller, execution, request)


@pytest.mark.parametrize(
    "bad", ["unknown-source", "bad-notation", "conflicting-element", "unknown-field"]
)
def test_invalid_diagram_cannot_become_visible(controller, sample_repo, monkeypatch, bad):
    tid, eid, refs = approved_with_prose(controller, sample_repo, monkeypatch)
    execution = get_execution(controller.conn, eid)
    request = content(controller, execution, refs)
    if bad == "unknown-source":
        request["parts"][0]["sources"] = ["E999999"]
    elif bad == "bad-notation":
        request["parts"][0]["notation"] = "flow Web ->"
    elif bad == "conflicting-element":
        request["parts"].append(request["parts"][0] | {"change": "new"})
    else:
        request["approve_spec"] = True
    before = controller.workspace(tid).events()
    with pytest.raises(ValueError):
        publish(controller, execution, request)
    assert controller.workspace(tid).events() == before
