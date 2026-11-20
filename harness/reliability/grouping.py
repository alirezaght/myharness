"""Bounded observable-symptom suggestions, never a common-cause assertion or incident merge."""

import json
import secrets

from harness.db import transaction, utcnow
from harness.execution_contract import canonical
from harness.reliability import catalog
from harness.reliability.investigation_contracts import identity, object_keys, rationale


def _metadata(conn, item):
    row = conn.execute(
        (
            "SELECT o.tool,o.role,o.policy_digest,o.spec_version FROM incident"
            "_operation_sources s JOIN reliability_incidents i ON i.incident_i"
            "d=s.legacy_incident_id JOIN reliability_observations o USING(obse"
            "rvation_id) WHERE s.incident_id=?"
        ),
        (item["incident_id"],),
    ).fetchone()
    return {
        k: item[k]
        for k in ("task_id", "execution_id", "epoch_id", "source_kind", "category", "symptom_code")
    } | (
        dict(row)
        if row
        else {"tool": None, "role": None, "policy_digest": None, "spec_version": None}
    )


def _key(item, selected):
    code = item["symptom_code"]
    family = (
        "boundary_rejected"
        if code in {"AUTHORITY_REJECTED", "DISPATCH_REJECTED", "CONTRACT_REJECTED"}
        else code
    )
    return {
        "task_id": item["task_id"],
        "execution_id": selected.get("execution"),
        "epoch_id": selected.get("epoch"),
        "symptom_family": family,
    }


def get(conn, selected, group_id):
    row = conn.execute(
        "SELECT * FROM incident_group_suggestions WHERE group_id=? AND task_id=?",
        (group_id, selected["task"]),
    ).fetchone()
    if row is None:
        raise LookupError("group scope not found")
    key = json.loads(row["safe_key"])
    if "execution" in selected and (key["execution_id"], key["epoch_id"]) != (
        selected["execution"],
        selected["epoch"],
    ):
        raise LookupError("group scope not found")
    members = json.loads(row["input_set"])
    variants = {
        member: _metadata(conn, catalog.detail(conn, selected, member)) for member in members
    }
    latest = conn.execute(
        "SELECT * FROM incident_group_decisions WHERE group_id=? ORDER BY revision DESC LIMIT 1",
        (group_id,),
    ).fetchone()
    return {
        "group_id": group_id,
        "algorithm_version": 1,
        "safe_key": key,
        "members": members,
        "member_metadata": variants,
        "created_at": row["created_at"],
        "revision": latest["revision"] if latest else 0,
        "decision": json.loads(latest["document"]) if latest else None,
        "meaning": "related symptoms; common cause not established",
    }


def suggest(conn, selected, *, limit=100, cursor=None, expected_window=None):
    with transaction(conn):
        page = catalog.list_incidents(conn, selected, limit=limit, cursor=cursor)
        if expected_window is not None and canonical(page) != expected_window:
            raise catalog.IncidentConflict("incident suggestion window changed")
        buckets = {}
        skipped = 0
        for item in page["items"]:
            if item["indexing"] != "indexed":
                skipped += 1
                continue
            key = canonical(_key(item, selected))
            buckets.setdefault(key, []).append(item["incident_id"])
        groups = []
        for key, members in sorted(buckets.items()):
            if len(members) < 2:
                continue
            inputs = canonical(sorted(members))
            prior = conn.execute(
                (
                    "SELECT group_id FROM incident_group_suggestions WHERE task_id=? A"
                    "ND algorithm_version=1 AND safe_key=? AND input_set=?"
                ),
                (selected["task"], key, inputs),
            ).fetchone()
            gid = prior[0] if prior else secrets.token_hex(16)
            if not prior:
                conn.execute(
                    "INSERT INTO incident_group_suggestions VALUES (?,?,1,?,?,?)",
                    (gid, selected["task"], key, inputs, utcnow()),
                )
                conn.executemany(
                    "INSERT INTO incident_group_members VALUES (?,?)",
                    [(gid, member) for member in sorted(members)],
                )
            groups.append(get(conn, selected, gid))
        return {
            "schema_version": 1,
            "scope": selected,
            "items": groups,
            "next_cursor": page["next_cursor"],
            "legacy_unindexed_in_window": skipped,
            "coverage": (
                "closed symptom families in this bounded incident window; variants"
                " retained; no cause inference"
            ),
        }


def decide(conn, selected, group_id, *, expected_revision, request_id, decision):
    identity(request_id)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid group revision")
    request = canonical(
        {"scope": selected, "expected_revision": expected_revision, "decision": decision}
    )
    if len(request.encode()) > 32768:
        raise ValueError("group request too large")
    with transaction(conn):
        group = get(conn, selected, group_id)
        prior = conn.execute(
            "SELECT * FROM incident_group_decisions WHERE group_id=? AND request_id=?",
            (group_id, request_id),
        ).fetchone()
        if prior:
            if prior["request"] != request:
                raise catalog.IncidentConflict("group request already used")
            return {"revision": prior["revision"], "document": json.loads(prior["document"])}
        if group["revision"] != expected_revision:
            raise catalog.IncidentConflict("group revision changed")
        if not isinstance(decision, dict) or decision.get("kind") not in {
            "accept",
            "reject",
            "split",
            "supersede",
        }:
            raise ValueError("invalid group decision")
        kind = decision["kind"]
        object_keys(
            decision,
            {"kind", "rationale"}
            | (
                {"partitions"}
                if kind == "split"
                else {"target_group"}
                if kind == "supersede"
                else set()
            ),
        )
        rationale(conn, selected, decision["rationale"], required=True)
        if kind == "split":
            partitions = decision["partitions"]
            if (
                not isinstance(partitions, list)
                or not 2 <= len(partitions) <= 100
                or any(not isinstance(p, list) or not p for p in partitions)
            ):
                raise ValueError("invalid group partitions")
            flattened = [i for part in partitions for i in part]
            if any(not isinstance(i, str) for i in flattened) or sorted(flattened) != sorted(
                group["members"]
            ):
                raise ValueError("partitions must cover each member exactly once")
        if kind == "supersede":
            identity(decision["target_group"])
            if decision["target_group"] == group_id:
                raise ValueError("group cannot supersede itself")
            get(conn, selected, decision["target_group"])
        revision = expected_revision + 1
        conn.execute(
            "INSERT INTO incident_group_decisions VALUES (?,?,?,?,'local_dashboard_operator',?,?)",
            (group_id, revision, request_id, request, utcnow(), canonical(decision)),
        )
        return {"revision": revision, "document": decision}


def decisions(conn, selected, group_id, *, before_revision=None, limit=25):
    get(conn, selected, group_id)
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or (
            before_revision is not None
            and (type(before_revision) is not int or before_revision < 1)
        )
    ):
        raise ValueError("invalid group history page")
    rows = conn.execute(
        (
            "SELECT * FROM incident_group_decisions WHERE group_id=? AND (? IS"
            " NULL OR revision<?) ORDER BY revision DESC LIMIT ?"
        ),
        (group_id, before_revision, before_revision, limit + 1),
    ).fetchall()
    return {
        "items": [
            {
                "revision": r["revision"],
                "actor_kind": r["actor_kind"],
                "request_id": r["request_id"],
                "created_at": r["created_at"],
                "document": json.loads(r["document"]),
            }
            for r in rows[:limit]
        ],
        "next_revision": rows[limit - 1]["revision"] if len(rows) > limit else None,
    }
