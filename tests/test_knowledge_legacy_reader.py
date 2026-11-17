"""Actual 0b7eb32 source, isolated from current imports, must refuse upgraded projections."""

import hashlib
import json
import select
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from harness.controller import HarnessController
from harness.db import connect, transaction
from harness.knowledge.lessons import export_lesson, grant, lesson_body, revoke
from harness.knowledge.models import ContextRequest, RefreshRequest, projection_identity
from harness.knowledge.policy import current_operator_scope, operator_scope
from harness.knowledge.query import explain, query
from harness.knowledge.refresh import refresh
from harness.knowledge.retention import purge
from harness.knowledge.store import ensure_project
from tests.test_knowledge_lessons import approve, install
from tests.test_knowledge_lifecycle import git

_COMMIT = "0b7eb3288ea188e494df424010e6cbbd8c67efa0"
_FIXTURES = Path(__file__).parent / "fixtures/knowledge_legacy_0b7"
_PROGRAM = r"""
import json, sqlite3, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from harness.knowledge import query as old_query, refresh as old_refresh
from harness.knowledge.models import ContextRequest
from harness.knowledge.policy import operator_scope, derive_scope, policy_digest
import importlib
module = importlib.import_module("harness.knowledge.query")
for name, imported in tuple(sys.modules.items()):
    if name == 'harness' or name.startswith('harness.'):
        assert Path(imported.__file__).is_relative_to(Path(sys.argv[1])), name
conn = sqlite3.connect(sys.argv[2], isolation_level=None, timeout=20)
conn.row_factory = sqlite3.Row
conn.execute('PRAGMA foreign_keys=ON')
command = json.loads(sys.stdin.readline())
scope = operator_scope(Path(sys.argv[3]))
if command.get('derive'):
    scope = derive_scope(SimpleNamespace(conn=conn),
        dict(id='legacy-execution',task_id='task-B',epoch_id='epoch',authority_generation=0),
        dict(role='orchestrator',thread_id='legacy-principal'), {}, Path(sys.argv[3]))
if command.get('pause'):
    original = module.validate_scope
    first = True
    def paused(*args):
        global first
        result = original(*args)
        if first:
            first = False
            print(json.dumps({'status':'ready','generation':scope.policy_generation}), flush=True)
            assert json.loads(sys.stdin.readline()) == {'resume':True}
        return result
    module.validate_scope = paused
try:
    if command['action'] == 'refresh':
        result = old_refresh(conn, scope)
        output = {'status':result.status,'snapshot_id':result.snapshot_id}
    elif command['action'] == 'explain':
        output = module.explain(conn, scope, command['bundle_hash'], command['assertion_id'])
    else:
        output = old_query(conn, scope, ContextRequest(command.get('query','public'),
            as_of_snapshot=command.get('snapshot'))).to_dict()
    print(json.dumps({'status':'ok','result':output,
        'generation':scope.policy_generation}), flush=True)
except (ValueError, OSError) as error:
    print(json.dumps({'status':'refused','error':str(error),
        'generation':scope.policy_generation}), flush=True)
finally:
    conn.close()
"""


@pytest.fixture
def legacy(tmp_path):
    manifest = json.loads((_FIXTURES / "manifest.json").read_bytes())
    sources = json.loads((_FIXTURES / "sources.json").read_bytes())
    assert manifest["source_commit"] == _COMMIT and sources.keys() == manifest["files"].keys()
    package = tmp_path / "old-package"
    for name, text in sources.items():
        raw = text.encode()
        assert hashlib.sha256(raw).hexdigest() == manifest["files"][name]
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "public.py").write_text("import public_dependency\n")
    git(root, "add", ".")
    database = tmp_path / "state.db"
    conn = connect(database)
    scope = operator_scope(root)
    yield package, root, database, conn, scope
    conn.close()


def start_old(legacy, command):
    package, root, database, _, _ = legacy
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", _PROGRAM, str(package), str(database), str(root)],
        cwd=package,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process.stdin.write(json.dumps(command) + "\n")
    process.stdin.flush()
    return process


def ready(process):
    assert select.select([process.stdout], [], [], 20)[0], "old reader did not reach timing barrier"
    return json.loads(process.stdout.readline())


def finish_old(process, *, resume=False):
    try:
        output, error = process.communicate('{"resume":true}\n' if resume else None, timeout=30)
        assert process.returncode == 0, error
        return json.loads(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def old(legacy, **command):
    return finish_old(start_old(legacy, command))


def baseline(legacy):
    _, _, _, conn, scope = legacy
    result = old(legacy, action="refresh")
    assert result["status"] == "ok"
    assert conn.execute("SELECT repository_identity FROM knowledge_projects").fetchone()[0] == str(
        scope.repository
    )
    bundle = old(legacy, action="query")
    assert bundle["status"] == "ok" and bundle["result"]["assertions"]
    return result["result"]["snapshot_id"], bundle["result"]


def test_actual_old_public_baseline_and_task_private_current_history_refusal(legacy):
    _, root, _, conn, scope = legacy
    public_snapshot, _ = baseline(legacy)
    (root / "private.py").write_text("import private_canary\n")
    first = replace(scope, task_id="task-A")
    private = refresh(conn, first, RefreshRequest(included_paths=("private.py",)))
    assert conn.execute("SELECT repository_identity FROM knowledge_projects").fetchone()[
        0
    ] == projection_identity(root)
    assert query(conn, first, ContextRequest("private_canary")).payload["assertions"]
    assert not query(
        conn, replace(scope, task_id="task-B"), ContextRequest("private_canary")
    ).payload["assertions"]
    assert query(conn, scope, ContextRequest("public", as_of_snapshot=public_snapshot)).payload[
        "assertions"
    ]
    for snapshot in (None, private.snapshot_id, public_snapshot):
        result = old(legacy, action="query", derive=True, query="private_canary", snapshot=snapshot)
        assert result["status"] == "refused" and "project mismatch" in result["error"]
        assert result["generation"] == scope.policy_generation


def test_old_refresh_cannot_downgrade_or_publish_after_upgrade(legacy):
    _, _, _, conn, scope = legacy
    baseline(legacy)
    ensure_project(conn, scope)
    count = conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0]
    result = old(legacy, action="refresh")
    assert result["status"] == "refused" and "repository mismatch" in result["error"]
    assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == count
    assert conn.execute("SELECT repository_identity FROM knowledge_projects").fetchone()[
        0
    ] == projection_identity(scope.repository)


def test_old_query_in_flight_revalidates_after_upgrade_without_bundle_publication(legacy):
    _, root, _, conn, scope = legacy
    baseline(legacy)
    before = conn.execute("SELECT count(*) FROM knowledge_bundles").fetchone()[0]
    process = start_old(legacy, {"action": "query", "pause": True})
    try:
        assert ready(process)["status"] == "ready"
        (root / "private.py").write_text("import private_canary\n")
        refresh(
            conn, replace(scope, task_id="task-A"), RefreshRequest(included_paths=("private.py",))
        )
    finally:
        result = finish_old(process, resume=True)
    assert result["status"] == "refused" and "project mismatch" in result["error"]
    assert conn.execute("SELECT count(*) FROM knowledge_bundles").fetchone()[0] == before


def test_old_explain_paused_before_upgrade_cannot_accept_new_contract_bundle(legacy, tmp_path):
    _, _, _, conn, scope = legacy
    baseline(legacy)
    # Predict the exact current bundle using a disposable DB copy; original stays legacy.
    preview = connect(tmp_path / "preview.db")
    try:
        conn.backup(preview)
        predicted = query(preview, scope, ContextRequest("public"))
    finally:
        preview.close()
    aid = predicted.payload["assertions"][0]["assertion_id"]
    process = start_old(
        legacy,
        {
            "action": "explain",
            "pause": True,
            "bundle_hash": predicted.bundle_hash,
            "assertion_id": aid,
        },
    )
    try:
        assert ready(process)["status"] == "ready"
        actual = query(conn, scope, ContextRequest("public"))
        assert actual.bundle_hash == predicted.bundle_hash
    finally:
        result = finish_old(process, resume=True)
    assert result["status"] == "refused" and "bundle unavailable" in result["error"]
    assert explain(conn, scope, actual.bundle_hash, aid)["citations"]
    assert (
        old(legacy, action="explain", bundle_hash=actual.bundle_hash, assertion_id=aid)["status"]
        == "refused"
    )


def test_upgrade_preserves_ids_old_bytes_pins_and_authority_and_is_idempotent(legacy):
    _, _, _, conn, scope = legacy
    _, bundle = baseline(legacy)
    HarnessController(conn).create_task("Retain authority across knowledge reader upgrade")
    authority = [line for line in conn.iterdump() if "knowledge_" not in line]
    conn.execute(
        "INSERT INTO knowledge_bundle_pins VALUES (?,?)",
        (bundle["bundle_hash"], "execution:retained"),
    )

    def retained():
        return {
            table: [tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')]
            for table in (
                "knowledge_snapshots",
                "knowledge_assertions",
                "knowledge_evidence",
                "knowledge_bundles",
                "knowledge_bundle_pins",
                "executions",
            )
        }

    before = retained()
    project = conn.execute(
        "SELECT project_id,policy_generation,active_snapshot_id FROM knowledge_projects"
    ).fetchone()[:]
    with transaction(conn):
        ensure_project(conn, scope)
        assert conn.in_transaction
        ensure_project(conn, scope)
    assert retained() == before
    assert [line for line in conn.iterdump() if "knowledge_" not in line] == authority
    assert (
        conn.execute(
            "SELECT project_id,policy_generation,active_snapshot_id FROM knowledge_projects"
        ).fetchone()[:]
        == project
    )
    assert query(conn, scope, ContextRequest("public")).payload["assertions"]
    conn.execute("UPDATE knowledge_projects SET repository_identity='unknown-contract'")
    with pytest.raises(ValueError, match="repository mismatch"):
        ensure_project(conn, scope)
    assert (
        conn.execute("SELECT repository_identity FROM knowledge_projects").fetchone()[0]
        == "unknown-contract"
    )


def test_failed_protected_publication_keeps_barrier_or_rolls_back_all_protected_rows(legacy):
    _, root, _, conn, scope = legacy
    baseline(legacy)
    before = conn.execute("SELECT count(*) FROM knowledge_source_versions").fetchone()[0]
    conn.execute(
        "CREATE TRIGGER reject_snapshot BEFORE INSERT ON "
        "knowledge_snapshots BEGIN SELECT RAISE(FAIL,'injected failure'); END"
    )
    (root / "private.py").write_text("import private_canary\n")
    with pytest.raises(Exception, match="injected failure"):
        refresh(
            conn, replace(scope, task_id="task-A"), RefreshRequest(included_paths=("private.py",))
        )
    assert conn.execute("SELECT count(*) FROM knowledge_source_versions").fetchone()[0] == before
    assert conn.execute("SELECT repository_identity FROM knowledge_projects").fetchone()[
        0
    ] == projection_identity(root)
    assert old(legacy, action="query", derive=True, query="private_canary")["status"] == "refused"


def test_old_historical_import_and_purge_reads_refuse_latest_generation(legacy, tmp_path):
    _, _, _, conn, scope = legacy
    original_snapshot, _ = baseline(legacy)
    # The approved lesson originates from a second explicitly selected local project.
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_conn = connect(tmp_path / "source.db")
    source_scope = operator_scope(source_root)
    try:
        ensure_project(source_conn, source_scope)
        body = lesson_body(
            source_scope.project_id, "portable", 1, "local", "import canary", "suggestion"
        )
        export_lesson(source_conn, source_scope, body, approve(source_scope, body))
        grant(source_conn, source_scope, conn, scope, "portable", 1, grant_id="explicit")
        payload = source_conn.execute("SELECT payload FROM knowledge_lessons").fetchone()[0]
        imported = install(conn, scope, payload)
        bundle = query(conn, scope, ContextRequest("canary"))
        assert bundle.payload["assertions"]
        revoke(source_conn, source_scope, conn, scope, "explicit")
        scope = current_operator_scope(conn, scope.repository)
        assert not query(
            conn, scope, ContextRequest("canary", as_of_snapshot=imported["snapshot_id"])
        ).payload["assertions"]
        result = old(
            legacy, action="query", derive=True, query="canary", snapshot=imported["snapshot_id"]
        )
        assert result["status"] == "refused" and "project mismatch" in result["error"]
        assert result["generation"] == scope.policy_generation
        public = query(conn, scope, ContextRequest("public"))
        purge(conn, scope, [public.payload["citations"][0]["version_id"]])
        scope = current_operator_scope(conn, scope.repository)
        result = old(
            legacy, action="query", derive=True, query="public", snapshot=original_snapshot
        )
        assert result["status"] == "refused" and result["generation"] == scope.policy_generation
    finally:
        source_conn.close()


def test_upgrade_respects_existing_transaction_rollback(legacy):
    _, _, _, conn, scope = legacy
    baseline(legacy)
    legacy_identity = str(scope.repository.resolve())
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction(conn):
            ensure_project(conn, scope)
            assert conn.in_transaction
            assert conn.execute("SELECT repository_identity FROM knowledge_projects").fetchone()[
                0
            ] == projection_identity(scope.repository)
            raise RuntimeError("rollback")
    assert (
        conn.execute("SELECT repository_identity FROM knowledge_projects").fetchone()[0]
        == legacy_identity
    )
    assert old(legacy, action="query")["status"] == "ok"
