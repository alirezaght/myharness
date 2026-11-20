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
    register_session_protocol(conn)
    conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL)")
    wanted = {*range(1, common+1), *range(19, 19+main), *range(21, 21+reliability)}
    for version, name, sql in _migrations():
        if version not in wanted:
            continue
        execute_migration(conn, sql)
        if checkpoint and version >= 21:
            version, name = version-2, OLD_NAMES[version-21]
        conn.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (version, name, "original-time"))
    return conn


def populated(conn, reliability):
    """Persistence-only seed; these rows do not qualify a native producer or approval."""
    conn.execute("INSERT INTO tasks VALUES ('t-upgrade','Preserve history','simple','proposed','design','then','then')")
    conn.execute("INSERT INTO spec_versions(task_id,version,path,revision,working_tree,content_hash,status,created_at) VALUES ('t-upgrade',1,'spec','revision','tree','hash','approved','then')")
    conn.execute("INSERT INTO approvals(task_id,spec_version,path,revision,content_hash,review_state_hash,created_at) VALUES ('t-upgrade',1,'spec','revision','hash','review','then')")
    conn.execute("INSERT INTO executions(id,task_id,approval_id,host,binding,handoff_path,handoff_hash,status,al_session_id,created_at,updated_at) VALUES ('execution-upgrade','t-upgrade',1,'codex','{}','handoff','hash','Completed','session','then','then')")
    conn.execute("INSERT INTO execution_epochs VALUES ('execution-upgrade','legacy-0',0,'ACTIVE','{}',0,'then')")
    if reliability:
        conn.execute("INSERT INTO operation_audit VALUES ('audit','execution-upgrade','{}','guarded','{}','then')")
        conn.execute("INSERT INTO reliability_observations VALUES ('observation',1,'operation-dispatch','audit','execution-upgrade','t-upgrade','legacy-0',0,'principal','orchestrator','read_file',NULL,1,1,'then','then','denied','AUTHORITY_REJECTED','runtime_observed')")
        conn.execute("INSERT INTO reliability_incidents VALUES ('incident','observation',1,'operation_denied','open','then','then',1)")
    if reliability >= 2:
        conn.execute("INSERT INTO reliability_traces VALUES ('trace','execution-upgrade','then')")
        conn.execute("INSERT INTO reliability_timeline_links(link_id,trace_id,execution_id,epoch_id,source_kind,source_id,event_kind,provenance,created_at) VALUES ('link','trace','execution-upgrade','legacy-0','al-event','source','semantic.run','validated_semantic_report','then')")
    if reliability >= 3:
        conn.execute("INSERT INTO policy_decisions VALUES ('decision','execution-upgrade','legacy-0',NULL,'unknown','deny','BUDGET_EXHAUSTED','then')")
    if reliability >= 5:
        from harness.reliability.catalog import project_operation

        project_operation(conn, 'incident')
    names = ['tasks','executions','execution_epochs']
    if reliability:names += ['reliability_observations','reliability_incidents']
    if reliability >= 2:names += ['reliability_traces','reliability_timeline_links']
    if reliability >= 3:names += ['policy_decisions']
    if reliability >= 5:names += ['reliability_incident_catalog','incident_operation_sources']
    return {n:[dict(r) for r in conn.execute('SELECT * FROM '+n)] for n in names}


@pytest.mark.parametrize('common', range(0,19))
def test_every_common_prefix_upgrades(tmp_path, common):
    path=tmp_path/'prefix.db'
    old_database(path,common=common).close()
    with connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]==25
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


@pytest.mark.parametrize('checkpoint', [True,False])
@pytest.mark.parametrize('reliability', range(1,6))
def test_checkpoint_and_relocated_crash_prefix_preserve_data(tmp_path, reliability, checkpoint):
    path=tmp_path/'checkpoint.db'
    conn=old_database(path,reliability=reliability,checkpoint=checkpoint)
    before=populated(conn,reliability)
    conn.close()
    conn=connect(path)
    try:
        for name, rows in before.items():
            actual=[dict(r) for r in conn.execute('SELECT * FROM '+name)]
            # Runtime-policy adds nullable fields when rebuilding timeline links.
            assert [{k:r[k] for k in row} for r,row in zip(actual,rows,strict=True)]==rows
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
        for version in range(21,21+reliability):
            assert conn.execute('SELECT applied_at FROM schema_migrations WHERE version=?',(version,)).fetchone()[0]=='original-time'
        assert apply_migrations(conn)==[]
    finally:conn.close()


@pytest.mark.parametrize('main', [1,2])
@pytest.mark.parametrize('reliability', [0,2,5])
def test_main_and_resumable_mixed_prefixes(tmp_path, main, reliability):
    path=tmp_path/'main.db'
    old_database(path,main=main,reliability=reliability).close()
    connect(path).close()


@pytest.mark.parametrize('fault', ['filename','missing_table','changed_trigger','hole','collision'])
def test_unknown_history_refuses_without_ledger_changes(tmp_path, fault):
    path=tmp_path/'unknown.db'
    conn=old_database(path,reliability=3,checkpoint=True)
    if fault=='filename':conn.execute("UPDATE schema_migrations SET name='0019_unknown.sql' WHERE version=19")
    if fault=='missing_table':conn.execute('DROP TABLE policy_decisions')
    if fault=='changed_trigger':
        conn.execute('DROP TRIGGER IF EXISTS reliability_timeline_policy_scope')
        conn.execute("CREATE TRIGGER reliability_surprise BEFORE INSERT ON tasks BEGIN SELECT RAISE(ABORT,'changed');END")
    if fault=='hole':conn.execute('DELETE FROM schema_migrations WHERE version=20')
    if fault=='collision':conn.execute("INSERT INTO schema_migrations VALUES (25,'0025_incident_catalog.sql','collision')")
    before=list(conn.execute('SELECT * FROM schema_migrations ORDER BY version'))
    with pytest.raises(RuntimeError,match='unrecognized migration history'):apply_migrations(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]==[tuple(r) for r in before]
    assert not conn.in_transaction
    conn.close()


def test_failed_remap_rolls_back_and_retries(tmp_path):
    path=tmp_path/'atomic.db';conn=old_database(path,reliability=5,checkpoint=True)
    conn.execute("CREATE TRIGGER stop_relocation BEFORE UPDATE ON schema_migrations WHEN OLD.version=20 BEGIN SELECT RAISE(ABORT,'relocation-fault');END")
    before=[tuple(r) for r in conn.execute('SELECT * FROM schema_migrations')]
    with pytest.raises(sqlite3.IntegrityError,match='relocation-fault'):apply_migrations(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM schema_migrations')]==before
    assert not conn.in_transaction
    conn.execute('DROP TRIGGER stop_relocation');conn.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _:connect(path).close(),range(2)))


def test_statement_executor_preserves_trigger_literals_and_rolls_back():
    conn=sqlite3.connect(':memory:',autocommit=True)
    conn.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.OperationalError):
        execute_migration(conn,"CREATE TABLE x(v TEXT); CREATE TRIGGER t AFTER INSERT ON x BEGIN UPDATE x SET v='a;b'; END; INSERT INTO missing VALUES(1);")
    conn.execute('ROLLBACK')
    assert conn.execute("SELECT name FROM sqlite_schema WHERE name='x'").fetchone() is None
    for sql in ['BEGIN;','COMMIT;','SELECT 1']:
        with pytest.raises(RuntimeError):execute_migration(conn,sql)
    conn.close()
