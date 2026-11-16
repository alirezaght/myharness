"""Extract outside SQLite locks, then revalidate and publish atomically."""

from harness.db import transaction
from harness.knowledge.extract import extract_source
from harness.knowledge.models import (
    MAX_REFRESH_BYTES,
    PreparedSnapshot,
    RefreshRequest,
    RefreshResult,
    digest,
)
from harness.knowledge.policy import validate_scope
from harness.knowledge.sources import enumerate_sources, inventory_names, read_source
from harness.knowledge.store import ensure_project, publish_snapshot, record_refresh_attempt

_DEFAULT_REFRESH_REQUEST = RefreshRequest()


def _record_incomplete(conn, scope, coverage):
    with transaction(conn):
        project = validate_scope(conn, scope)
        record_refresh_attempt(conn, scope.project_id, "incomplete", coverage)
        return RefreshResult(project["active_snapshot_id"], "incomplete", tuple(coverage))


def refresh(conn, scope, request: RefreshRequest = _DEFAULT_REFRESH_REQUEST) -> RefreshResult:
    ensure_project(conn, scope)
    project = validate_scope(conn, scope)
    if scope.purpose not in {"operator", "investigation"}:
        raise PermissionError("refresh requires an operator or investigation scope")
    expected = project["active_snapshot_id"]
    try:
        inventory = enumerate_sources(scope, request)
    except (OSError, ValueError) as error:
        _record_incomplete(
            conn, scope, [{"status": "unreadable", "kind": "inventory", "reason": str(error)}]
        )
        raise
    if not inventory.complete:
        return _record_incomplete(conn, scope, inventory.coverage)
    extractions = tuple(extract_source(source) for source in inventory.sources)
    coverage = [c for c in inventory.coverage if c["status"] != "excluded-sensitive"]
    try:
        _, observation = inventory_names(scope.repository, request)
        if observation != inventory.observation:
            coverage.append({"status": "unstable", "kind": "inventory"})
        retained_bytes = 0
        for source in inventory.sources:
            limit = min(request.max_file_bytes, MAX_REFRESH_BYTES - retained_bytes)
            raw = read_source(scope.repository, source.locator, limit)
            retained_bytes += len(raw)
            if digest(raw) != source.content_sha256:
                coverage.append({"status": "unstable", "locator": source.locator})
    except (OSError, UnicodeError, ValueError) as error:
        coverage.append(
            {
                "status": "unstable",
                "kind": "source-revalidation",
                "reason": str(error),
                "limit": request.max_file_bytes,
            }
        )
    if coverage:
        return _record_incomplete(conn, scope, coverage)
    prepared = PreparedSnapshot(scope, inventory, extractions, project["policy_generation"])
    try:
        snapshot = publish_snapshot(conn, prepared, expected_active=expected)
    except (OSError, ValueError) as error:
        _record_incomplete(
            conn, scope, [{"status": "failed", "kind": "publication", "reason": str(error)}]
        )
        raise
    return RefreshResult(
        snapshot,
        "published",
        (*inventory.coverage, *(gap for extraction in extractions for gap in extraction.coverage)),
    )
