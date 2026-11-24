"""Existing investigation boundaries share knowledge; pure analyze remains read-only."""

import json
import os
import sqlite3
from dataclasses import replace

from harness.analyzer import analyze
from harness.db import transaction
from harness.knowledge.adapters import task_sources
from harness.knowledge.extract import extract_source
from harness.knowledge.models import (
    ContextRequest,
    PreparedSnapshot,
    RefreshRequest,
    SourceInventory,
)
from harness.knowledge.policy import current_operator_scope, validate_scope
from harness.knowledge.query import query
from harness.knowledge.refresh import refresh
from harness.knowledge.store import publish_snapshot

_DEFAULT_REFRESH_REQUEST = RefreshRequest()
MODE_VARIABLE = "HARNESS_KNOWLEDGE"
MAX_SETTLE_ROUNDS = 64
_INCREMENTAL = {"publish-batch-deferred", "refresh-bytes-capped"}


def knowledge_mode():
    """`off`, `auto` (default) or `on`; unknown values fall back to `auto`.

    `auto` keeps the graph lazy: a repository nobody has indexed costs nothing at investigation,
    and one an operator indexed (`harness knowledge refresh`) is kept current incrementally.
    """
    value = os.environ.get(MODE_VARIABLE, "auto").strip().lower()
    return {"0": "off", "false": "off", "1": "on", "true": "on"}.get(
        value, value if value in {"off", "auto", "on"} else "auto"
    )


def indexed(conn, scope):
    row = conn.execute(
        "SELECT active_snapshot_id FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    return bool(row and row[0])


def _pending(result):
    return sum(1 for gap in result.coverage if gap.get("reason") in _INCREMENTAL)


def refresh_with_records(controller, scope, request=_DEFAULT_REFRESH_REQUEST):
    result = refresh(controller.conn, scope, request)
    if (
        request.commit
        or result.snapshot_id is None
        or not scope.task_id
        or result.status == "incomplete"
    ):
        return result
    sources, coverage = task_sources(controller, scope, scope.task_id)
    from harness.knowledge.retention import version_purged

    blocked = tuple(
        {
            "locator": source.locator,
            "reason": "purged",
            "status": "purged",
            "visibility": source.visibility,
            "version_id": source.version_id,
        }
        for source in sources
        if version_purged(controller.conn, source.version_id)
    )
    sources = tuple(
        source for source in sources if not version_purged(controller.conn, source.version_id)
    )
    coverage = (*coverage, *blocked)
    if not sources and not coverage:
        return result
    project = validate_scope(controller.conn, scope)
    parent = controller.conn.execute(
        "SELECT observation_json FROM knowledge_snapshots WHERE snapshot_id=?",
        (project["active_snapshot_id"],),
    ).fetchone()
    old = {
        r[0]
        for r in controller.conn.execute(
            "SELECT version_id FROM knowledge_snapshot_sources WHERE snapshot_id=? AND "
            "membership_state='present'",
            (project["active_snapshot_id"],),
        )
    }
    if not coverage and all(s.version_id in old for s in sources):
        return result
    inventory = SourceInventory(sources, True, coverage, json.loads(parent[0]))
    snapshot = publish_snapshot(
        controller.conn,
        PreparedSnapshot(
            scope,
            inventory,
            tuple(extract_source(s) for s in sources),
            scope.policy_generation,
            carry_forward=True,
            unknown=blocked,
        ),
        expected_active=project["active_snapshot_id"],
    )
    return replace(
        result,
        snapshot_id=snapshot,
        status="published-partial"
        if blocked or result.status == "published-partial"
        else "published",
        coverage=(*result.coverage, *coverage),
    )


def refresh_until_settled(controller, scope, request=_DEFAULT_REFRESH_REQUEST, *, rounds=None):
    """Repeat incremental refreshes while each one publishes and defers the remainder.

    Every round hashes the inventory but retains and extracts only uncached sources, in
    bounded publication batches, so a large repository converges without a file-count cap.
    """
    from harness.knowledge.policy import current_operator_scope as current

    rounds = MAX_SETTLE_ROUNDS if rounds is None else rounds
    result = refresh_with_records(controller, scope, request)
    for _ in range(rounds - 1):
        if request.commit or result.status not in {"published", "published-partial"}:
            break
        if not _pending(result):
            break
        scope = current(
            controller.conn, scope.repository, task_id=scope.task_id, purpose=scope.purpose
        )
        result = refresh_with_records(controller, scope, request)
    return result


def investigate_with_context(controller, task_id, repo_root, request=None, decisions=None):
    task = controller.get_task(task_id)
    report = analyze(repo_root, task.request, decisions)
    controller.record_investigation(task_id, report)
    scope = current_operator_scope(
        controller.conn, repo_root, task_id=task_id, purpose="investigation"
    )
    mode = knowledge_mode()
    bundle = None
    try:
        if mode == "off" or (mode == "auto" and not indexed(controller.conn, scope)):
            # Opt-in: indexing a large repository costs more than an investigation that never
            # asks a consumer question gains. Grep/read remain the agent's primary tools.
            diagnostic = {
                "status": "skipped",
                "mode": mode,
                "reason": "knowledge graph is opt-in; run `harness knowledge refresh <task>` "
                f"once or set {MODE_VARIABLE}=on to index this repository",
            }
        else:
            result = refresh_with_records(controller, scope)
            bundle = (
                query(controller.conn, scope, request or ContextRequest(task.request[:4096]))
                if result.snapshot_id
                else None
            )
            diagnostic = {"status": result.status, "mode": mode, "coverage": list(result.coverage)}
            from harness.knowledge.consumers import coverage_summary

            diagnostic["summary"] = coverage_summary(controller.conn, scope)
    except (OSError, ValueError, PermissionError):
        diagnostic, bundle = (
            {"status": "unavailable", "reason": "knowledge context unavailable"},
            None,
        )
    with transaction(controller.conn):
        workspace = controller.workspace(task_id)
        receipt = {"diagnostic": diagnostic, "bundle_hash": bundle.bundle_hash if bundle else None}
        if bundle:
            row = controller.conn.execute(
                "SELECT receipt_id,snapshot_id,policy_digest,source_reads,elapsed_ms FROM "
                "knowledge_receipts WHERE receipt_id=?",
                (bundle.receipt_id,),
            ).fetchone()
            receipt["receipt"] = dict(row) if row else None
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


def assignment_context(operation, controller, principal):
    """Return only references to a bundle issued for this already-admitted writer."""
    from pathlib import Path

    from harness.knowledge.policy import derive_scope

    execution, state = operation.authority(controller, principal, operation="read")
    assignment = operation.assignments[principal["thread_id"]]
    task = state["integration"]["tasks"][assignment["task"]]
    try:
        scope = derive_scope(
            controller,
            execution,
            principal,
            state,
            Path(operation.envelope["repository"]),
            operation=operation,
        )
        bundle = query(controller.conn, scope, ContextRequest(task["title"][:4096]))
    except (ValueError, OSError, LookupError, sqlite3.Error):
        return {"status": "unavailable"}
    return {
        "status": "available",
        "bundle_hash": bundle.bundle_hash,
        "snapshot_id": bundle.payload["snapshot_id"],
        "assertion_ids": [a["assertion_id"] for a in bundle.payload["assertions"]],
        "receipt_id": bundle.receipt_id,
    }
