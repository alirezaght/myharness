"""Dashboard graph actions stay scoped to the selected Harness task."""

import json

from harness.controller import HarnessController
from harness.dashboard.knowledge_view import (
    knowledge_action,
    read_knowledge,
    read_knowledge_evidence,
)
from harness.db import connect


def _task(db_path, repository):
    conn = connect(db_path)
    try:
        controller = HarnessController(conn)
        task = controller.create_task("inspect scoped knowledge")
        controller.workspace(task.id).checkpoint("investigation", {"repo_root": str(repository)})
        return task.id
    finally:
        conn.close()


def test_dashboard_status_refresh_query_and_evidence(db_path, sample_repo):
    marker = "builder_evidence_marker"
    (sample_repo / "demo/export.py").write_text(
        f"def {marker}():\n    return '{marker}'\n", encoding="utf-8"
    )
    task_id = _task(db_path, sample_repo)
    assert read_knowledge(db_path, task_id)["status"] == "not-indexed"
    status, refreshed = knowledge_action(db_path, json.dumps({"task": task_id}).encode(), "refresh")
    assert status == 200 and refreshed["status"] == "published"
    status, bundle = knowledge_action(
        db_path, json.dumps({"task": task_id, "query": marker}).encode(), "query"
    )
    assert status == 200 and bundle["assertions"]
    assertion = bundle["assertions"][0]
    status, explanation = knowledge_action(
        db_path,
        json.dumps(
            {
                "task": task_id,
                "bundle_hash": bundle["bundle_hash"],
                "assertion_id": assertion["assertion_id"],
            }
        ).encode(),
        "explain",
    )
    assert status == 200 and explanation["citations"]
    source = read_knowledge_evidence(
        db_path, task_id, bundle["bundle_hash"], assertion["assertion_id"]
    )
    assert source["citations"]
    citation = source["citations"][0]
    cited_bytes = (sample_repo / citation["locator"]).read_bytes()[
        citation["start_byte"] : citation["end_byte"]
    ]
    assert citation["locator"] == "demo/export.py"
    assert citation["text"] == cited_bytes.decode("utf-8", errors="replace")
    assert marker in citation["text"]
    status, empty = knowledge_action(
        db_path,
        json.dumps({"task": task_id, "query": "no-such-symbol-78234"}).encode(),
        "query",
    )
    assert status == 200 and empty["assertions"] == []
    assert "no result proves no dependency" in empty["notice"]


def test_dashboard_refuses_unknown_fields_and_unknown_task(db_path, sample_repo):
    task_id = _task(db_path, sample_repo)
    status, error = knowledge_action(
        db_path,
        json.dumps({"task": task_id, "query": "x", "project_id": "forged"}).encode(),
        "query",
    )
    assert status == 409 and "unexpected" in error["error"]
    status, error = knowledge_action(
        db_path, json.dumps({"task": "t-missing000", "query": "x"}).encode(), "query"
    )
    assert status == 404
    assert "unknown task" in error["error"]
