"""Explicit native investigation orchestration; pure repository analyze stays read-only."""

from harness.analyzer import analyze
from harness.db import transaction
from harness.knowledge.models import ContextRequest, RefreshRequest
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import query
from harness.knowledge.refresh import refresh


def investigate_with_context(controller, task_id, repo_root, request=None, decisions=None):
    task = controller.get_task(task_id)
    report = analyze(repo_root, task.request, decisions)
    controller.record_investigation(task_id, report)
    scope = operator_scope(repo_root, task_id=task_id, purpose="investigation")
    try:
        result = refresh(controller.conn, scope, RefreshRequest())
        if result.snapshot_id is None:
            diagnostic = {"status": result.status, "coverage": list(result.coverage)}
            bundle = None
        else:
            bundle = query(controller.conn, scope, request or ContextRequest(task.request[:4096]))
            diagnostic = {"status": result.status, "coverage": list(result.coverage)}
    except (OSError, ValueError, PermissionError) as error:
        diagnostic, bundle = {"status": "unavailable", "reason": str(error)}, None
    with transaction(controller.conn):
        workspace = controller.workspace(task_id)
        receipt = {"diagnostic": diagnostic, "bundle_hash": bundle.bundle_hash if bundle else None}
        workspace.checkpoint("knowledge-context", receipt)
        workspace.record_event("knowledge.context", receipt)
        if bundle:
            controller.conn.execute(
                "INSERT OR IGNORE INTO knowledge_bundle_pins VALUES (?,?)",
                (bundle.bundle_hash, f"task:{task_id}"),
            )
    return {
        "report": report,
        "context": bundle.to_dict() if bundle else None,
        "diagnostic": diagnostic,
    }
