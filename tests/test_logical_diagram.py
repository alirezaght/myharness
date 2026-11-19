from harness.dashboard.workspace_view import source_snapshot
from harness.spec_store import SpecStore
from harness.workspace_proposals import apply_operations


def test_fallback_is_task_specific_editable_stable_and_not_architecture(controller, tmp_path):
    task = controller.create_task("Build a scheduler")
    source = source_snapshot(controller, task, SpecStore(tmp_path / "specs"), None)
    assert source["logical"]["kind"] == "workflow"
    assert any(n["label"] == task.request for n in source["graph"]["nodes"])
    assert any(n["kind"] == "missing_detail" for n in source["graph"]["nodes"])
    assert source_snapshot(controller, task, SpecStore(tmp_path / "specs"), None) == source
    node = source["graph"]["nodes"][0]
    edited = apply_operations(
        source, [{"type": "node_label", "target": node["id"], "label": "Refinement"}]
    )
    assert edited["graph"]["nodes"][0]["label"] == "Refinement"
    assert edited["graph"]["nodes"][0]["status"] == node["status"]
