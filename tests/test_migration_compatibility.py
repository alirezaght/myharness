"""Known-history upgrades use real SQL and populated storage, not inferred version numbers."""

import concurrent.futures
import sqlite3

import pytest

from harness.db import _migrations, apply_migrations, connect
from harness.migration_compatibility import OLD_NAMES, execute_migration
from harness.planning_session_store import register_session_protocol


def old_database(path, *, common=18, main=0, reliability=0, checkpoint=False):
    conn = sqlite3.connect(path, autocommit=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    register_session_protocol(conn)
    conn.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,"
        "applied_at TEXT NOT NULL)"
    )
    wanted = {*range(1, common + 1), *range(19, 19 + main), *range(21, 21 + reliability)}
    for version, name, sql in _migrations():
        if version not in wanted:
            continue
        execute_migration(conn, sql)
        if checkpoint and version >= 21:
            version, name = version - 2, OLD_NAMES[version - 21]
        conn.execute(
            "INSERT INTO schema_migrations VALUES (?,?,?)", (version, name, "original-time")
        )
    return conn


def populated(conn, reliability):
    """Persistence-only seed; these rows do not qualify a native producer or approval."""
    conn.execute(
        "INSERT INTO tasks VALUES ('t-upgrade','Preserve history','simple','proposed','"
        "design','then','then')"
    )
    conn.execute(
        "INSERT INTO spec_versions(task_id,version,path,revision,working_tree,content_h"
        "ash,status,created_at) VALUES ('t-upgrade',1,'spec','revision','tree','hash','"
        "approved','then')"
    )
    conn.execute(
        "INSERT INTO approvals(task_id,spec_version,path,revision,content_hash,review_s"
        "tate_hash,created_at) VALUES ('t-upgrade',1,'spec','revision','hash','review',"
        "'then')"
    )
    conn.execute(
        "INSERT INTO executions(id,task_id,approval_id,host,binding,handoff_path,handof"
        "f_hash,status,al_session_id,created_at,updated_at) VALUES ('execution-upgrade'"
        ",'t-upgrade',1,'codex','{}','handoff','hash','Completed','session','then','the"
        "n')"
    )
    conn.execute(
        "INSERT INTO execution_epochs SELECT id,'legacy-0',0,'ACTIVE',"
        "json_object('al_session_id',al_session_id,'al_run_id',al_run_id,"
        "'handoff_path',handoff_path,'handoff_hash',handoff_hash),0,'then' FROM executions"
    )
    if reliability:
        conn.execute(
            "INSERT INTO operation_audit VALUES ('audit','execution-upgrade','{}','guar"
            "ded','{}','then')"
        )
        conn.execute(
            "INSERT INTO reliability_observations VALUES ('observation',1,'operation-di"
            "spatch','audit','execution-upgrade','t-upgrade','legacy-0',0,'principal','"
            "orchestrator','read_file',NULL,1,1,'then','then','denied','AUTHORITY_REJEC"
            "TED','runtime_observed')"
        )
        conn.execute(
            "INSERT INTO reliability_incidents VALUES ('incident','observation',1,'oper"
            "ation_denied','open','then','then',1)"
        )
    if reliability >= 2:
        conn.execute("INSERT INTO reliability_traces VALUES ('trace','execution-upgrade','then')")
        conn.execute(
            "INSERT INTO reliability_timeline_links(link_id,trace_id,execution_id,epoch"
            "_id,source_kind,source_id,event_kind,provenance,created_at) VALUES ('link'"
            ",'trace','execution-upgrade','legacy-0','semantic','source','semantic.run'"
            ",'validated_semantic_report','then')"
        )
    if reliability >= 3:
        conn.execute(
            "INSERT INTO policy_decisions VALUES ('decision','execution-upgrade','legac"
            "y-0',NULL,'deny','BUDGET_EXHAUSTED','unknown','then')"
        )
    if reliability >= 5:
        from harness.reliability.catalog import project_operation

        conn.execute("BEGIN IMMEDIATE")
        project_operation(conn, "incident")
        conn.execute("COMMIT")
    names = ["tasks", "executions", "execution_epochs"]
    if reliability:
        names += ["reliability_observations", "reliability_incidents"]
    if reliability >= 2:
        names += ["reliability_traces", "reliability_timeline_links"]
    if reliability >= 3:
        names += ["policy_decisions"]
    if reliability >= 5:
        names += ["reliability_incident_catalog", "incident_operation_sources"]
    return {n: [dict(r) for r in conn.execute("SELECT * FROM " + n)] for n in names}


@pytest.mark.parametrize("common", range(0, 19))
def test_every_common_prefix_upgrades(tmp_path, common):
    path = tmp_path / "prefix.db"
    old_database(path, common=common).close()
    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 25
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("checkpoint", [True, False])
@pytest.mark.parametrize("reliability", range(1, 6))
def test_checkpoint_and_relocated_crash_prefix_preserve_data(tmp_path, reliability, checkpoint):
    path = tmp_path / "checkpoint.db"
    conn = old_database(path, reliability=reliability, checkpoint=checkpoint)
    before = populated(conn, reliability)
    conn.close()
    conn = connect(path)
    try:
        for name, rows in before.items():
            actual = [dict(r) for r in conn.execute("SELECT * FROM " + name)]
            # Runtime-policy adds nullable fields when rebuilding timeline links.
            assert [{k: r[k] for k in row} for r, row in zip(actual, rows, strict=True)] == rows
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        for version in range(21, 21 + reliability):
            assert (
                conn.execute(
                    "SELECT applied_at FROM schema_migrations WHERE version=?", (version,)
                ).fetchone()[0]
                == "original-time"
            )
        assert apply_migrations(conn) == []
    finally:
        conn.close()


@pytest.mark.parametrize("main", [1, 2])
@pytest.mark.parametrize("reliability", [0, 2, 5])
def test_main_and_resumable_mixed_prefixes(tmp_path, main, reliability):
    path = tmp_path / "main.db"
    old_database(path, main=main, reliability=reliability).close()
    connect(path).close()


@pytest.mark.parametrize(
    "fault", ["filename", "missing_table", "changed_trigger", "hole", "collision"]
)
def test_unknown_history_refuses_without_ledger_changes(tmp_path, fault):
    path = tmp_path / "unknown.db"
    conn = old_database(path, reliability=3, checkpoint=True)
    if fault == "filename":
        conn.execute("UPDATE schema_migrations SET name='0019_unknown.sql' WHERE version=19")
    if fault == "missing_table":
        conn.execute("DROP TABLE policy_decisions")
    if fault == "changed_trigger":
        conn.execute("DROP TRIGGER IF EXISTS reliability_timeline_policy_scope")
        conn.execute(
            "CREATE TRIGGER reliability_surprise BEFORE INSERT ON tasks BEGIN SELECT RA"
            "ISE(ABORT,'changed');END"
        )
    if fault == "hole":
        conn.execute("DELETE FROM schema_migrations WHERE version=20")
    if fault == "collision":
        conn.execute(
            "INSERT INTO schema_migrations VALUES (25,'0025_incident_catalog.sql','collision')"
        )
    before = list(conn.execute("SELECT * FROM schema_migrations ORDER BY version"))
    with pytest.raises(RuntimeError, match="unrecognized migration history"):
        apply_migrations(conn)
    assert [tuple(r) for r in conn.execute("SELECT * FROM schema_migrations ORDER BY version")] == [
        tuple(r) for r in before
    ]
    assert not conn.in_transaction
    conn.close()


@pytest.mark.parametrize("fault", ["unprefixed_ledger_trigger", "ledger_without_primary_key"])
def test_owned_ledger_schema_refuses_before_upgrade_side_effects(tmp_path, fault):
    conn = old_database(tmp_path / "owned-ledger.db")
    if fault == "unprefixed_ledger_trigger":
        conn.execute("CREATE TABLE extension_notes(note TEXT)")
        conn.execute(
            "CREATE TRIGGER extension_hook AFTER INSERT ON schema_migrations "
            "BEGIN INSERT INTO extension_notes VALUES ('unexpected upgrade effect'); END"
        )
    else:
        conn.execute("ALTER TABLE schema_migrations RENAME TO prior_ledger")
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER,name TEXT NOT NULL,"
            "applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_migrations SELECT * FROM prior_ledger")
        conn.execute("DROP TABLE prior_ledger")
    data = populated(conn, 0)
    schema_before = [
        tuple(r) for r in conn.execute("SELECT * FROM main.sqlite_schema ORDER BY name")
    ]
    before = [tuple(r) for r in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
    refused = False
    try:
        apply_migrations(conn)
    except RuntimeError as error:
        assert "unrecognized migration history" in str(error)
        refused = True
    after = [tuple(r) for r in conn.execute("SELECT * FROM schema_migrations ORDER BY version")]
    effects = (
        [tuple(r) for r in conn.execute("SELECT * FROM extension_notes")]
        if fault == "unprefixed_ledger_trigger"
        else []
    )
    assert refused, {
        "ledger_rows_before": len(before),
        "ledger_rows_after": len(after),
        "effects": effects,
    }
    assert after == before and effects == [] and not conn.in_transaction
    assert [
        tuple(r) for r in conn.execute("SELECT * FROM main.sqlite_schema ORDER BY name")
    ] == schema_before
    for name, rows in data.items():
        assert [dict(r) for r in conn.execute("SELECT * FROM " + name)] == rows
    conn.close()


@pytest.mark.parametrize(
    "fault",
    [
        "extra_constraint",
        "ledger_index",
        "ledger_view",
        "managed_trigger",
        "sqliteX_trigger",
        "temp_ledger",
        "temp_managed_trigger",
    ],
)
def test_owned_schema_conflicts_refuse_without_mutation(tmp_path, fault):
    conn = old_database(tmp_path / "conflict.db")
    if fault == "extra_constraint":
        conn.execute("ALTER TABLE schema_migrations RENAME TO prior_ledger")
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY CHECK(version>0), "
            "name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_migrations SELECT * FROM prior_ledger")
        conn.execute("DROP TABLE prior_ledger")
    elif fault == "ledger_index":
        conn.execute("CREATE INDEX extension_index ON schema_migrations(name)")
    elif fault == "ledger_view":
        conn.execute("ALTER TABLE schema_migrations RENAME TO prior_ledger")
        conn.execute("CREATE VIEW schema_migrations AS SELECT * FROM prior_ledger")
    elif fault in {"managed_trigger", "sqliteX_trigger"}:
        name = "extension_hook" if fault == "managed_trigger" else "sqliteXhook"
        conn.execute(f"CREATE TRIGGER {name} AFTER INSERT ON tasks BEGIN SELECT 1; END")
    elif fault == "temp_ledger":
        conn.execute("CREATE TEMP TABLE schema_migrations(version,name,applied_at)")
    else:
        conn.execute(
            "CREATE TEMP TRIGGER extension_hook AFTER INSERT ON main.tasks BEGIN SELECT 1; END"
        )
    before = list(conn.iterdump())
    temp_before = [tuple(r) for r in conn.execute("SELECT * FROM temp.sqlite_schema")]
    with pytest.raises(RuntimeError, match="unrecognized migration history"):
        apply_migrations(conn)
    assert list(conn.iterdump()) == before
    assert [tuple(r) for r in conn.execute("SELECT * FROM temp.sqlite_schema")] == temp_before
    assert not conn.in_transaction
    conn.close()


def test_unrelated_extension_objects_remain_allowed(tmp_path):
    conn = old_database(tmp_path / "extension.db")
    conn.execute("CREATE TABLE extension_notes(note TEXT)")
    conn.execute(
        "CREATE TRIGGER extension_hook AFTER INSERT ON extension_notes "
        "BEGIN UPDATE extension_notes SET note=upper(new.note); END"
    )
    conn.execute("CREATE INDEX extension_index ON extension_notes(note)")
    conn.execute("CREATE TEMP TABLE extension_temp(value TEXT)")
    conn.execute("INSERT INTO extension_notes VALUES ('preserve')")
    before = [tuple(r) for r in conn.execute("SELECT * FROM extension_notes")]
    assert len(apply_migrations(conn)) == 7
    assert [tuple(r) for r in conn.execute("SELECT * FROM extension_notes")] == before
    conn.close()


def test_absent_and_empty_canonical_ledger_are_fresh():
    for absent in (True, False):
        conn = sqlite3.connect(":memory:", autocommit=True)
        register_session_protocol(conn)
        if not absent:
            conn.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, "
                "name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
        assert len(apply_migrations(conn)) == 25
        conn.close()


def test_failed_remap_rolls_back_and_retries(tmp_path):
    path = tmp_path / "atomic.db"
    conn = old_database(path, reliability=5, checkpoint=True)
    data = populated(conn, 5)
    before = [tuple(r) for r in conn.execute("SELECT * FROM schema_migrations")]
    baseline = conn.total_changes
    interrupted = []

    def after_relocation():
        if conn.total_changes > baseline:
            interrupted.append(conn.total_changes - baseline)
            conn.set_progress_handler(None, 0)
            return 1
        return 0

    conn.set_progress_handler(after_relocation, 1)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        apply_migrations(conn)
    conn.set_progress_handler(None, 0)
    assert interrupted and interrupted[0] >= 1
    assert [tuple(r) for r in conn.execute("SELECT * FROM schema_migrations")] == before
    for name, rows in data.items():
        assert [dict(r) for r in conn.execute("SELECT * FROM " + name)] == rows
    assert not conn.in_transaction
    conn.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: connect(path).close(), range(2)))


def test_statement_executor_preserves_trigger_literals_and_rolls_back():
    conn = sqlite3.connect(":memory:", autocommit=True)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError):
        execute_migration(
            conn,
            "CREATE TABLE x(v TEXT); CREATE TRIGGER t AFTER INSERT ON x BEGIN UPDATE x "
            "SET v='a;b'; END; INSERT INTO missing VALUES(1);",
        )
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT name FROM sqlite_schema WHERE name='x'").fetchone() is None
    for sql in ["BEGIN;", "COMMIT;", "SELECT 1"]:
        with pytest.raises(RuntimeError):
            execute_migration(conn, sql)
    conn.close()


def test_manifest_generation_is_reproducible_and_binds_exact_sql():
    from harness.migration_compatibility import schema_manifest
    from scripts.generate_migration_schema import generate

    assert generate() == schema_manifest()
    conn = sqlite3.connect(":memory:", autocommit=True)
    changed = _migrations()
    version, name, sql = changed[0]
    changed[0] = version, name, sql + "\nDELETE FROM tasks;\n"
    with pytest.raises(RuntimeError, match="packaged SQL differs"):
        apply_with = __import__("harness.migration_compatibility", fromlist=["validate_history"])
        apply_with.validate_history(conn, changed)
    assert conn.execute("SELECT name FROM sqlite_schema").fetchall() == []
    conn.close()
