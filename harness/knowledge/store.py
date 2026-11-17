"""Transactional immutable graph publication in the existing Harness database."""

import json
import secrets

from harness.db import transaction, utcnow
from harness.knowledge.models import (
    EXTRACTOR_VERSION,
    MAX_REFRESH_BYTES,
    MAX_SOURCE_BYTES,
    PreparedSnapshot,
    canonical,
    digest,
    identity,
)


class SnapshotConflict(ValueError):
    pass


def record_refresh_attempt(conn, project_id, status, coverage):
    """Caller holds the publication or failed-attempt transaction."""
    conn.execute(
        "INSERT INTO knowledge_refresh_attempts VALUES (?,?,?,?,?)",
        (secrets.token_hex(16), project_id, status, canonical(coverage), utcnow()),
    )


def ensure_project(conn, scope):
    root = str(scope.repository.resolve())
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_projects(project_id,repository_identity) VALUES (?,?)",
        (scope.project_id, root),
    )
    row = conn.execute(
        "SELECT * FROM knowledge_projects WHERE project_id=?", (scope.project_id,)
    ).fetchone()
    if not row or row["repository_identity"] != root:
        raise ValueError("knowledge project repository mismatch")
    return row


def prepare_rows(prepared):
    """Validate and hash source-derived rows before acquiring the publication lock."""
    inventory, project = prepared.inventory, prepared.scope.project_id
    if not inventory.complete and prepared.names is None:
        raise ValueError("incomplete inventory cannot replace active snapshot")
    if len(inventory.sources) != len(prepared.extractions):
        raise ValueError("source extraction cardinality mismatch")
    if any(len(s.raw) > MAX_SOURCE_BYTES for s in inventory.sources) or (
        sum(len(s.raw) for s in inventory.sources) > MAX_REFRESH_BYTES
    ):
        raise ValueError("prepared source bytes exceed supported publication limits")
    rows = []
    for source, extraction in zip(inventory.sources, prepared.extractions, strict=True):
        parts = (source.source_id, source.content_sha256, EXTRACTOR_VERSION, source.kind)
        expected_version = (
            identity(*parts, source.visibility)
            if source.visibility != "project"
            else identity(*parts)
        )
        if source.visibility != "project" and (
            not prepared.scope.task_id or source.visibility != "task:" + prepared.scope.task_id
        ):
            raise ValueError("source visibility exceeds task scope")
        if (
            source.source_id != identity(project, source.locator)
            or source.version_id != expected_version
        ):
            raise ValueError("source identity is not scoped to project/extractor/kind")
        if digest(source.raw) != source.content_sha256:
            raise ValueError("source content hash mismatch")
        assertions = []
        for a in extraction.assertions:
            if not 0 <= a.start_byte <= a.end_byte <= len(source.raw):
                raise ValueError("invalid evidence span")
            if a.origin not in {"structured", "static-analysis", "attributed-inference"} or (
                a.verification not in {"source-backed", "unverified", "broken-evidence"}
            ):
                raise ValueError("invalid assertion trust state")
            if a.origin == "attributed-inference" and a.verification != "unverified":
                raise ValueError("inference cannot create verification")
            data = [
                a.subject,
                a.predicate,
                a.object,
                a.value,
                a.text,
                a.start_byte,
                a.end_byte,
                a.origin,
                a.verification,
                a.applicability,
                a.confidence,
                sorted(a.derivation_parents),
            ]
            aid = identity(source.version_id, canonical(data))
            subject = identity(project, a.subject)
            obj = identity(project, a.object) if a.object else None
            entities = [(subject, project, a.subject.split(":", 1)[0], a.subject)]
            if obj:
                entities.append((obj, project, a.object.split(":", 1)[0], a.object))
            assertions.append(
                (
                    entities,
                    (
                        aid,
                        project,
                        subject,
                        a.predicate,
                        obj,
                        canonical(a.value),
                        canonical(a.applicability),
                        a.origin,
                        a.verification,
                        a.confidence,
                        a.text,
                    ),
                    (
                        aid,
                        source.version_id,
                        a.start_byte,
                        a.end_byte,
                        digest(source.raw[a.start_byte : a.end_byte]),
                    ),
                    (a.text, a.subject, a.object or ""),
                    tuple((aid, parent) for parent in sorted(set(a.derivation_parents))),
                )
            )
        rows.append(
            (
                (source.source_id, project, source.locator, source.kind, source.visibility),
                (
                    source.version_id,
                    source.source_id,
                    source.content_sha256,
                    "deterministic",
                    EXTRACTOR_VERSION,
                    canonical(dict(source.observation, visibility=source.visibility)),
                    "complete",
                ),
                tuple(assertions),
                canonical(extraction.coverage),
                source.material_class,
            )
        )
    return tuple(rows)


def _memberships(conn, prepared, expected):
    project = prepared.scope.project_id
    previous = {}
    if expected:
        previous = {
            r["locator"]: dict(r)
            for r in conn.execute(
                "SELECT m.*,s.locator,c.material_class FROM knowledge_snapshot_sources m "
                "JOIN knowledge_sources s USING(source_id) "
                "LEFT JOIN knowledge_source_class c USING(source_id) WHERE snapshot_id=?",
                (expected,),
            )
        }
    members = {
        s.locator: (s.source_id, s.version_id, "present", None) for s in prepared.inventory.sources
    }
    for gap in prepared.unknown:
        name = gap["locator"]
        old = previous.get(name)
        sid = identity(project, name)
        version = old["version_id"] if old else identity(sid, "unread", EXTRACTOR_VERSION)
        members[name] = (sid, version, "unknown", gap)
    names = prepared.names
    if names is None:
        names = frozenset(members)
    for name, old in previous.items():
        if name in members:
            continue
        if prepared.carry_forward:
            prior = conn.execute(
                "SELECT detail_json FROM knowledge_snapshot_source_state WHERE "
                "snapshot_id=? AND source_id=?",
                (expected, old["source_id"]),
            ).fetchone()
            gap = json.loads(prior[0]) if prior else None
            members[name] = (old["source_id"], old["version_id"], old["membership_state"], gap)
            continue
        if (
            old["material_class"] not in {None, "repository"}
            or old["membership_state"] == "deleted"
        ):
            state, gap = old["membership_state"], None
        elif prepared.names_complete and name not in names:
            state, gap = "deleted", None
        else:
            state = "unknown"
            gap = {"locator": name, "reason": "deletion-unconfirmed"}
        members[name] = (old["source_id"], old["version_id"], state, gap)
    return members


def publish_snapshot(conn, prepared: PreparedSnapshot, *, expected_active: str | None) -> str:
    rows = prepare_rows(prepared) if prepared.rows is None else prepared.rows
    ensure_project(conn, prepared.scope)
    project = prepared.scope.project_id
    members = _memberships(conn, prepared, expected_active)
    snapshot, now = secrets.token_hex(16), utcnow()
    coverage = [
        *prepared.inventory.coverage,
        *(g for extraction in prepared.extractions for g in extraction.coverage),
    ]
    previous_sources = {
        r[0]
        for r in conn.execute(
            "SELECT source_id FROM knowledge_snapshot_sources WHERE snapshot_id=?",
            (expected_active,),
        )
    }
    annotated = []
    for gap in coverage:
        member = members.get(gap.get("locator"))
        if member and member[2] == "unknown":
            gap = dict(gap, membership="unknown", carried_version=member[0] in previous_sources)
        annotated.append(gap)
    coverage = annotated
    covered = {g.get("locator") for g in coverage}
    coverage.extend(
        dict(gap, status="unknown", membership="unknown", carried_version=True)
        for _, _, state, gap in members.values()
        if state == "unknown" and gap and gap["locator"] not in covered
    )
    gaps = [
        (sid, gap.get("reason", gap.get("status", "unreadable")), canonical(gap))
        for sid, _, state, gap in members.values()
        if state == "unknown" and gap
    ]
    unknown_count = sum(m[2] == "unknown" for m in members.values())
    deleted_count = sum(m[2] == "deleted" for m in members.values())
    status = "published-partial" if unknown_count or not prepared.names_complete else "published"
    observation = prepared.inventory.observation
    cursor = prepared.cursor or {}
    snapshot_row = (
        snapshot,
        project,
        expected_active,
        canonical(observation),
        int(prepared.names_complete and not unknown_count),
        canonical(coverage),
        now,
    )
    attempt_row = (secrets.token_hex(16), project, status, canonical(coverage), now)
    placeholder_rows = []
    for name, (sid, version, state, _) in members.items():
        if state == "unknown":
            placeholder_rows.append(
                (
                    (
                        sid,
                        project,
                        name,
                        "python" if name.endswith(".py") else "text",
                        members[name][3].get("visibility", "project")
                        if members[name][3]
                        else "project",
                    ),
                    (
                        version,
                        sid,
                        "",
                        "deterministic",
                        EXTRACTOR_VERSION,
                        canonical(
                            {
                                "visibility": members[name][3].get("visibility", "project")
                                if members[name][3]
                                else "project"
                            }
                        ),
                        "unread",
                    ),
                )
            )
    with transaction(conn):
        current = conn.execute(
            "SELECT * FROM knowledge_projects WHERE project_id=?", (project,)
        ).fetchone()
        if current["active_snapshot_id"] != expected_active or (
            current["policy_generation"] != prepared.expected_policy_generation
        ):
            raise SnapshotConflict("active snapshot or policy changed during extraction")
        conn.execute("INSERT INTO knowledge_snapshots VALUES (?,?,?,?,?,?,?)", snapshot_row)
        derivations = []
        for source, version, assertions, extraction_coverage, material_class in rows:
            conn.execute("INSERT OR IGNORE INTO knowledge_sources VALUES (?,?,?,?,?)", source)
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_source_versions VALUES (?,?,?,?,?,?,?)", version
            )
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_source_class VALUES (?,?)",
                (source[0], material_class),
            )
            if version[0] in prepared.reused:
                continue
            for entities, assertion, evidence, lexical, parents in assertions:
                derivations.extend(parents)
                conn.executemany(
                    "INSERT OR IGNORE INTO knowledge_entities VALUES (?,?,?,?)", entities
                )
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    assertion,
                )
                rowid = conn.execute(
                    "SELECT rowid FROM knowledge_assertions WHERE assertion_id=?", (assertion[0],)
                ).fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_assertion_fts(rowid,text,subject,object) "
                    "VALUES (?,?,?,?)",
                    (rowid, *lexical),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_evidence VALUES (?,?,?,?,?)", evidence
                )
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_extraction_members VALUES (?,?)",
                    (version[0], assertion[0]),
                )
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_extraction_cache VALUES (?,?,?,?,?)",
                (version[0], EXTRACTOR_VERSION, extraction_coverage, len(assertions), now),
            )
        for assertion_id, parent_id in derivations:
            if not conn.execute(
                "SELECT 1 FROM knowledge_assertions WHERE assertion_id=? AND project_id=?",
                (parent_id, project),
            ).fetchone():
                raise ValueError("derivation parent outside project")
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_derivations VALUES (?,?)",
                (assertion_id, parent_id),
            )
        for source, version in placeholder_rows:
            conn.execute("INSERT OR IGNORE INTO knowledge_sources VALUES (?,?,?,?,?)", source)
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_source_versions VALUES (?,?,?,?,?,?,?)", version
            )
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_source_class VALUES (?,?)",
                (source[0], "repository"),
            )
        conn.executemany(
            "INSERT INTO knowledge_snapshot_sources VALUES (?,?,?,?)",
            [(snapshot, sid, version, state) for sid, version, state, _ in members.values()],
        )
        conn.executemany(
            "INSERT INTO knowledge_snapshot_source_state VALUES (?,?,?,?)",
            [(snapshot, *gap) for gap in gaps],
        )
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_snapshot_assertions SELECT ?,e.assertion_id "
            "FROM knowledge_extraction_members e "
            "JOIN knowledge_snapshot_sources m USING(version_id) "
            "WHERE m.snapshot_id=? AND m.membership_state='present'",
            (snapshot, snapshot),
        )
        if expected_active:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_snapshot_assertions SELECT ?,p.assertion_id "
                "FROM knowledge_snapshot_assertions p WHERE p.snapshot_id=? AND EXISTS "
                "(SELECT 1 FROM knowledge_evidence e WHERE e.assertion_id=p.assertion_id) "
                "AND NOT EXISTS (SELECT 1 FROM knowledge_evidence e "
                "WHERE e.assertion_id=p.assertion_id "
                "AND NOT EXISTS (SELECT 1 FROM knowledge_snapshot_sources m WHERE m.snapshot_id=? "
                "AND m.version_id=e.version_id AND m.membership_state='present'))",
                (snapshot, expected_active, snapshot),
            )
        conn.execute(
            "INSERT INTO knowledge_snapshot_cursor VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot,
                cursor.get("mode", "working-tree"),
                observation.get("branch"),
                observation.get("head"),
                cursor.get("parent_head"),
                cursor.get("ancestry", "unknown"),
                observation.get("inventory_sha256"),
                int(prepared.names_complete),
                unknown_count,
                deleted_count,
            ),
        )
        conn.execute(
            "INSERT INTO knowledge_source_cursor VALUES (?,?,?,?,?,?,?) ON CONFLICT(project_id) "
            "DO UPDATE SET snapshot_id=excluded.snapshot_id,head=excluded.head,"
            "branch=excluded.branch,"
            "inventory_sha256=excluded.inventory_sha256,extractor_version=excluded.extractor_version,updated_at=excluded.updated_at",
            (
                project,
                snapshot,
                observation.get("head"),
                observation.get("branch"),
                observation.get("inventory_sha256"),
                EXTRACTOR_VERSION,
                now,
            ),
        )
        changed = conn.execute(
            "UPDATE knowledge_projects SET active_snapshot_id=? "
            "WHERE project_id=? AND active_snapshot_id IS ?",
            (snapshot, project, expected_active),
        )
        if changed.rowcount != 1:
            raise SnapshotConflict("active snapshot changed during publication")
        if prepared.contribution is not None:
            conn.execute(
                "INSERT INTO knowledge_contributions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                prepared.contribution,
            )
        conn.execute("INSERT INTO knowledge_refresh_attempts VALUES (?,?,?,?,?)", attempt_row)
    return snapshot
