"""Exact portable lessons and explicit local, non-transitive material grants."""

import json
import re
import secrets
import sqlite3
from dataclasses import replace
from pathlib import PurePosixPath

from harness.db import transaction, utcnow
from harness.decisions import DecisionStatus, parse_decision_text
from harness.knowledge.models import (
    EXTRACTOR_VERSION,
    MAX_SOURCE_BYTES,
    Assertion,
    Extraction,
    canonical,
    digest,
    identity,
)
from harness.knowledge.policy import validate_scope
from harness.knowledge.retention import invalidate_versions, require_operator
from harness.knowledge.sources import _SECRET, read_source

_PREFIX = "# Portable engineering lesson\n\n```knowledge-lesson\n"
_SUFFIX = "\n```\n"
_TRAILER = "\n<!-- knowledge-approval: "
_KEYS = {
    "schema_version",
    "source_project",
    "lesson_id",
    "version",
    "applicability",
    "rationale",
    "limitations",
    "evidence",
}


def lesson_body(
    source_project, lesson_id, version, applicability, rationale, limitations, evidence=()
):
    metadata = dict(
        schema_version=1,
        source_project=source_project,
        lesson_id=lesson_id,
        version=version,
        applicability=applicability,
        rationale=rationale,
        limitations=limitations,
        evidence=list(evidence),
    )
    raw = (_PREFIX + canonical(metadata) + _SUFFIX).encode()
    parse_body(raw)
    return raw


def parse_body(raw):
    text = raw.decode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES or not text.startswith(_PREFIX) or not text.endswith(_SUFFIX):
        raise ValueError("lesson-content-mismatch")
    value = json.loads(text[len(_PREFIX) : -len(_SUFFIX)])
    if not isinstance(value, dict):
        raise ValueError("invalid portable lesson")
    if (
        set(value) != _KEYS
        or value["schema_version"] != 1
        or not isinstance(value["source_project"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["source_project"])
        or not isinstance(value["lesson_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value["lesson_id"])
        or type(value["version"]) is not int
        or value["version"] < 1
        or any(
            not isinstance(value[k], str) or not value[k].strip()
            for k in ("applicability", "rationale", "limitations")
        )
        or not isinstance(value["evidence"], list)
        or len(value["evidence"]) > 40
    ):
        raise ValueError("invalid portable lesson")
    for evidence in value["evidence"]:
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {"assertion_id", "description"}
            or not isinstance(evidence["assertion_id"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence["assertion_id"])
            or not isinstance(evidence["description"], str)
        ):
            raise ValueError("disclosure-safe evidence reference required")
    if (
        (_PREFIX + canonical(value) + _SUFFIX).encode() != raw
        or _SECRET.search(text)
        or "\x00" in text
    ):
        raise ValueError("lesson-content-mismatch")
    return value


def approval_marker(body):
    value = parse_body(body)
    return "knowledge-lesson-approval: " + canonical(
        {
            "project_id": value["source_project"],
            "lesson_id": value["lesson_id"],
            "version": value["version"],
            "lesson-sha256": digest(body),
        }
    )


def parse_portable(raw):
    text = raw.decode("utf-8")
    if text.count(_TRAILER) != 1 or not text.endswith(" -->\n"):
        raise ValueError("lesson approval trailer required")
    body, trailer = text.rsplit(_TRAILER, 1)
    metadata = parse_body(body.encode())
    approval = json.loads(trailer[:-5])
    if (
        not isinstance(approval, dict)
        or set(approval) != {"approval_ref", "lesson-sha256"}
        or approval["lesson-sha256"] != digest(body.encode())
    ):
        raise ValueError("lesson-content-mismatch")
    metadata["approval_ref"] = approval["approval_ref"]
    return body.encode(), metadata


def _approval(scope, body, locator):
    path = PurePosixPath(locator)
    if path.parent != PurePosixPath("docs/decisions") or not re.fullmatch(
        r"DEC-\d+[^/]*\.md", path.name
    ):
        raise PermissionError("lesson-approval-missing")
    raw = read_source(scope.repository, locator, MAX_SOURCE_BYTES)
    record = parse_decision_text(raw.decode(), scope.repository / locator)
    if (
        record.status != DecisionStatus.APPROVED
        or record.superseded_by
        or approval_marker(body) not in record.decision.splitlines()
    ):
        raise PermissionError("lesson-approval-missing")
    return canonical(
        {
            "locator": locator,
            "decision_id": record.id,
            "version": record.version,
            "record_sha256": digest(raw),
        }
    )


def _ancestry(conn, scope, evidence, *, verify_bytes=True):
    from harness.knowledge.query import _citation_current, _evidence, _partition

    project = validate_scope(conn, scope)
    _partition(conn, scope, project["active_snapshot_id"])
    pending = [e["assertion_id"] for e in evidence]
    visited = set()
    while pending:
        aid = pending.pop()
        if aid in visited:
            continue
        visited.add(aid)
        if conn.execute(
            "SELECT 1 FROM knowledge_evidence e JOIN knowledge_source_versions v USING(version_id) "
            "JOIN knowledge_source_class c USING(source_id) WHERE e.assertion_id=? "
            "AND c.material_class='lesson-import'",
            (aid,),
        ).fetchone():
            raise PermissionError("grant-non-transitive")
        if not conn.execute(
            "SELECT 1 FROM temp.k_eligible WHERE assertion_id=?", (aid,)
        ).fetchone():
            raise PermissionError("lesson evidence unavailable")
        pending.extend(
            r[0]
            for r in conn.execute(
                "SELECT parent_assertion_id FROM knowledge_derivations WHERE assertion_id=?", (aid,)
            )
        )
    if verify_bytes:
        cache = {}
        if not all(
            _citation_current(scope, ref, conn=conn, cache=cache)
            for aid in visited
            for ref in _evidence(conn, aid)
        ):
            raise PermissionError("lesson evidence stale")


def export_lesson(conn, scope, body, decision_locator):
    require_operator(conn, scope)
    value = parse_body(body)
    if value["source_project"] != scope.project_id:
        raise PermissionError("lesson project mismatch")
    _ancestry(conn, scope, value["evidence"])
    approval = _approval(scope, body, decision_locator)
    payload = (
        body.decode()
        + _TRAILER
        + canonical({"approval_ref": approval, "lesson-sha256": digest(body)})
        + " -->\n"
    )
    # Re-read retained approval before the short publication transaction.
    if _approval(scope, body, decision_locator) != approval:
        raise PermissionError("lesson approval changed")
    with transaction(conn):
        require_operator(conn, scope)
        _ancestry(conn, scope, value["evidence"], verify_bytes=False)
        prior = conn.execute(
            "SELECT * FROM knowledge_lessons WHERE lesson_id=? AND version=?",
            (value["lesson_id"], value["version"]),
        ).fetchone()
        if prior and (
            prior["project_id"] != scope.project_id
            or prior["content_sha256"] != digest(body)
            or not prior["payload"]
        ):
            raise ValueError("lesson-content-mismatch")
        if not prior:
            conn.execute(
                "INSERT INTO knowledge_lessons VALUES (?,?,?,?,?,?,?)",
                (
                    value["lesson_id"],
                    value["version"],
                    scope.project_id,
                    digest(body),
                    payload,
                    approval,
                    utcnow(),
                ),
            )
    return {
        "lesson_id": value["lesson_id"],
        "version": value["version"],
        "material_sha256": digest(body),
        "payload": prior["payload"] if prior else payload,
    }


def _lesson(conn, scope, lesson_id, version):
    require_operator(conn, scope)
    row = conn.execute(
        "SELECT * FROM knowledge_lessons WHERE project_id=? AND lesson_id=? AND version=?",
        (scope.project_id, lesson_id, version),
    ).fetchone()
    if not row or not row["payload"]:
        raise ValueError("lesson unavailable")
    body, value = parse_portable(row["payload"].encode())
    approval = json.loads(row["approval_ref"])
    if _approval(scope, body, approval["locator"]) != row["approval_ref"]:
        raise PermissionError("lesson-approval-missing")
    _ancestry(conn, scope, value["evidence"])
    return row


def _insert_grant(conn, scope, row):
    with transaction(conn):
        require_operator(conn, scope)
        prior = conn.execute(
            "SELECT * FROM knowledge_grants WHERE grant_id=?", (row[0],)
        ).fetchone()
        if prior and tuple(prior)[:5] != tuple(row)[:5]:
            raise PermissionError("grant binding mismatch")
        if prior and prior["revoked_at"]:
            raise PermissionError("grant-revoked")
        conn.execute("INSERT OR IGNORE INTO knowledge_grants VALUES (?,?,?,?,?,?)", row)


def grant(
    source_conn,
    source_scope,
    destination_conn,
    destination_scope,
    lesson_id,
    version,
    *,
    grant_id=None,
):
    require_operator(destination_conn, destination_scope)
    lesson = _lesson(source_conn, source_scope, lesson_id, version)
    if source_scope.project_id == destination_scope.project_id:
        raise ValueError("distinct explicit local projects required")
    grant_id = grant_id or secrets.token_hex(16)
    row = (
        grant_id,
        source_scope.project_id,
        destination_scope.project_id,
        lesson["content_sha256"],
        lesson["approval_ref"],
        None,
    )
    _insert_grant(source_conn, source_scope, row)
    try:
        _insert_grant(destination_conn, destination_scope, row)
    except (sqlite3.Error, OSError):
        return {"status": "source-only", "grant_id": grant_id, "enforced": False}
    return {"status": "granted", "grant_id": grant_id, "enforced": True}


def exact_grant(conn, project_id, value, material):
    return conn.execute(
        "SELECT * FROM knowledge_grants WHERE source_project=? AND destination_project=? "
        "AND material_sha256=? AND approval_ref=? AND revoked_at IS NULL ORDER BY grant_id LIMIT 1",
        (value["source_project"], project_id, material, value["approval_ref"]),
    ).fetchone()


def import_observation(observation):
    """Only source-bound evidence and exact grant provenance travel with a lesson."""
    allowed = {
        "mode",
        "commit",
        "file_mode",
        "blob",
        "type",
        "visibility",
        "source_project",
        "destination_project",
        "lesson_sha256",
        "lesson_id",
        "lesson_version",
        "grant_id",
        "approval_ref",
    }
    return {key: value for key, value in observation.items() if key in allowed}


def admit_import(conn, scope, source):
    body, value = parse_portable(source.raw)
    row = exact_grant(conn, scope.project_id, value, digest(body))
    if not row:
        raise PermissionError("ungranted-lesson")
    kind = "lesson"
    parts = (source.source_id, source.content_sha256, EXTRACTOR_VERSION, kind)
    version_id = (
        identity(*parts, source.visibility) if source.visibility != "project" else identity(*parts)
    )
    observation = dict(
        import_observation(source.observation),
        source_project=value["source_project"],
        destination_project=scope.project_id,
        lesson_sha256=digest(body),
        lesson_id=value["lesson_id"],
        lesson_version=value["version"],
        grant_id=row["grant_id"],
        approval_ref=value["approval_ref"],
    )
    return replace(
        source,
        kind=kind,
        material_class="lesson-import",
        version_id=version_id,
        observation=observation,
    )


def extract_import(source):
    body, value = parse_portable(source.raw)
    text = "; ".join(value[k] for k in ("applicability", "rationale", "limitations"))
    return Extraction(
        (
            Assertion(
                "lesson:" + value["lesson_id"],
                "suggests",
                None,
                value["rationale"],
                text,
                0,
                len(body),
                "attributed-inference",
                "unverified",
                applicability={
                    "imported": True,
                    "state": "suggestion",
                    "source_project": value["source_project"],
                    "version": value["version"],
                },
            ),
        )
    )


def import_lesson(conn, scope, locator):
    """Explicit tracked local Markdown import; publication uses normal refresh admission."""
    require_operator(conn, scope)
    if not locator.startswith("docs/knowledge/imports/"):
        raise ValueError("lesson import locator required")
    from harness.knowledge.refresh import refresh

    result = refresh(conn, scope)
    row = conn.execute(
        "SELECT m.membership_state FROM knowledge_snapshot_sources m JOIN "
        "knowledge_sources s USING(source_id) WHERE m.snapshot_id=? AND s.locator=?",
        (result.snapshot_id, locator),
    ).fetchone()
    if not row or row[0] != "present":
        raise PermissionError("lesson import withheld; track the exact granted portable Markdown")
    return {"status": "suggestion", "snapshot_id": result.snapshot_id}


def current_bytes(conn, scope, ref):
    observation = json.loads(ref["observation_json"])
    if observation.get("mode") == "commit":
        from harness.knowledge.commit_sources import read_blob

        raw = read_blob(scope.repository, observation, MAX_SOURCE_BYTES)
    else:
        raw = read_source(scope.repository, ref["locator"], MAX_SOURCE_BYTES)
    body, value = parse_portable(raw)
    if not exact_grant(conn, scope.project_id, value, digest(body)):
        raise PermissionError("grant-revoked")
    if digest(body) != observation.get("lesson_sha256") or value[
        "source_project"
    ] != observation.get("source_project"):
        raise PermissionError("lesson-content-mismatch")
    return raw


def revoke(source_conn, source_scope, destination_conn, destination_scope, grant_id):
    require_operator(source_conn, source_scope)
    require_operator(destination_conn, destination_scope)
    source = source_conn.execute(
        "SELECT * FROM knowledge_grants WHERE grant_id=?", (grant_id,)
    ).fetchone()
    destination = destination_conn.execute(
        "SELECT * FROM knowledge_grants WHERE grant_id=?", (grant_id,)
    ).fetchone()
    if not source or (source["source_project"], source["destination_project"]) != (
        source_scope.project_id,
        destination_scope.project_id,
    ):
        raise PermissionError("grant-missing")
    if destination and tuple(destination)[:5] != tuple(source)[:5]:
        raise PermissionError("grant binding mismatch")
    try:
        with transaction(destination_conn):
            require_operator(destination_conn, destination_scope)
            if not destination:
                destination_conn.execute(
                    "INSERT INTO knowledge_grants VALUES (?,?,?,?,?,?)", tuple(source)
                )
            changed = destination_conn.execute(
                "UPDATE knowledge_grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                (utcnow(), grant_id),
            ).rowcount
            if changed:
                versions = [
                    r[0]
                    for r in destination_conn.execute(
                        "SELECT version_id FROM knowledge_import_sources WHERE grant_id=?",
                        (grant_id,),
                    )
                ]
                invalidate_versions(
                    destination_conn,
                    destination_scope.project_id,
                    versions,
                    "grant-revoked",
                    grant_id,
                )
                destination_conn.execute(
                    "UPDATE knowledge_projects SET policy_generation=policy_generation+1 "
                    "WHERE project_id=?",
                    (destination_scope.project_id,),
                )
    except (sqlite3.Error, OSError):
        return {"status": "revocation-not-enforced", "enforced": False, "grant_id": grant_id}
    try:
        with transaction(source_conn):
            require_operator(source_conn, source_scope)
            source_conn.execute(
                "UPDATE knowledge_grants SET revoked_at=coalesce(revoked_at,?) WHERE grant_id=?",
                (utcnow(), grant_id),
            )
    except (sqlite3.Error, OSError):
        return {"status": "revoked-destination-only", "enforced": True, "grant_id": grant_id}
    return {"status": "revoked", "enforced": True, "grant_id": grant_id}
