"""Canonical source integrity and unchanged R1 facts against disposable local SQLite."""

# ruff: noqa: F811
import sqlite3

import pytest

from harness.db import transaction
from harness.reliability import catalog, query, source_queries
from tests.incident_helpers import seed, selected
from tests.test_reliability_store import observed  # noqa: F401


def test_readonly_union_preserves_r1_api_and_literal_search(observed):
    conn = observed[2].conn
    seed(observed, 3)
    scoped = selected(observed)
    old = query.list_incidents(conn, scoped)
    changes = conn.total_changes
    conn.execute("PRAGMA query_only=ON")
    page = catalog.list_incidents(conn, scoped)
    assert {i["incident_id"] for i in page["items"]} == {i["incident_id"] for i in old["items"]}
    assert all(i["indexing"] == "legacy_unindexed" for i in page["items"])
    assert not catalog.list_incidents(conn, scoped, q="%_")["items"]
    assert conn.total_changes == changes
    assert query.list_incidents(conn, scoped) == old


def test_exact_source_family_and_no_orphan_catalog_at_commit(observed):
    conn = observed[2].conn
    ids = seed(observed)
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        catalog._insert(
            conn,
            "budget_decision",
            selected(observed)["execution"],
            "legacy-0",
            "budget_exhaustion",
            "BUDGET_EXHAUSTED",
            "runtime_observed",
            "then",
        )
    if conn.in_transaction:
        conn.execute("ROLLBACK")
    assert not conn.execute("SELECT 1 FROM reliability_incident_catalog").fetchone()
    with transaction(conn):
        catalog.project_operation(conn, ids[0])
        assert catalog.project_operation(conn, ids[0]) == ids[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO incident_policy_sources(incident_id,policy_decision_id) VALUES (?,?)",
                (ids[0], "missing"),
            )
    assert conn.execute("SELECT count(*) FROM reliability_incident_catalog").fetchone()[0] == 1
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_source_insert_failure_is_atomic_even_if_caller_catches(observed):
    conn = observed[2].conn
    iid = seed(observed)[0]
    conn.execute(
        "CREATE TRIGGER injected_source BEFORE INSERT ON incident_operation_sources "
        "BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with transaction(conn):
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            catalog.project_operation(conn, iid)
        assert not conn.execute("SELECT 1 FROM reliability_incident_catalog").fetchone()
    assert catalog.detail(conn, selected(observed), iid)["indexing"] == "legacy_unindexed"


def test_policy_null_attempt_and_legacy_eight_value_insert(observed):
    conn = observed[2].conn
    execution = selected(observed)["execution"]
    with transaction(conn):
        conn.execute(
            (
                "INSERT INTO policy_decisions VALUES ('budget',?,'legacy-0',NULL,'"
                "deny','BUDGET_EXHAUSTED','policy','then')"
            ),
            (execution,),
        )
        incident = catalog.record_budget_decision(conn, "budget")
        assert catalog.record_budget_decision(conn, "budget") == incident
    item = catalog.detail(conn, selected(observed), incident)
    assert item["source_kind"] == "budget_decision"
    assert source_queries.source_relation(conn, selected(observed), incident)["reference"] == {
        "policy_decision_id": "budget",
        "attempt_id": None,
    }
    assert "dimension" not in item and "pool" not in item
    assert (
        conn.execute("SELECT attempt_id FROM policy_decisions WHERE id='budget'").fetchone()[0]
        is None
    )
    with transaction(conn):
        conn.execute(
            (
                "INSERT INTO policy_decisions VALUES ('capability',?,'legacy-0',NU"
                "LL,'deny','UNSUPPORTED_ENFORCEMENT_CAPABILITY','policy','then')"
            ),
            (execution,),
        )
        with pytest.raises(ValueError, match="unavailable"):
            catalog.record_budget_decision(conn, "capability")


def test_scope_and_closed_fact_projection(observed):
    conn = observed[2].conn
    iid = seed(observed)[0]
    scoped = selected(observed)
    with pytest.raises(LookupError):
        catalog.detail(conn, scoped | {"task": "other-task"}, iid)
    conn.execute("UPDATE operation_audit SET outcome=?", ("SECRET_SENTINEL_DO_NOT_COPY",))
    with transaction(conn):
        catalog.project_operation(conn, iid)
    item = catalog.detail(conn, scoped, iid)
    assert "SECRET_SENTINEL" not in str(item)
    source = source_queries.source_relation(conn, scoped, iid)
    assert source["source_kind"] == "operation_dispatch"
    assert "SECRET_SENTINEL" not in str(source)
    assert set(source["reference"]) == {"legacy_incident_id", "observation_id", "source_id"}
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM reliability_incident_catalog")


def test_schema_only_upgrade_preserves_legacy_and_positional_policy_rows(request, monkeypatch):
    from harness import db

    migrations = db._migrations()
    monkeypatch.setattr(db, "_migrations", lambda: [m for m in migrations if m[0] <= 22])
    observed = request.getfixturevalue("observed")
    conn = observed[2].conn
    seed(observed, 2)
    conn.execute(
        (
            "INSERT INTO policy_decisions VALUES ('old',?,'legacy-0',NULL,'den"
            "y','BUDGET_EXHAUSTED','old','then')"
        ),
        (selected(observed)["execution"],),
    )
    before = [tuple(r) for r in conn.execute("SELECT * FROM reliability_incidents")]
    policy = tuple(conn.execute("SELECT * FROM policy_decisions").fetchone())
    monkeypatch.undo()
    assert db.apply_migrations(conn) == ["0023_incident_catalog.sql"]
    assert [tuple(r) for r in conn.execute("SELECT * FROM reliability_incidents")] == before
    assert tuple(conn.execute("SELECT * FROM policy_decisions").fetchone()) == policy
    assert conn.execute("SELECT count(*) FROM reliability_incident_catalog").fetchone()[0] == 0
    assert db.apply_migrations(conn) == []
    assert len(catalog.list_incidents(conn, selected(observed))["items"]) == len(before)
