"""Actual guarded result/review producers to canonical Harness incidents, not fabricated events."""
import copy
import json
import sys
from pathlib import Path

import pytest
import al_store as S
from evidence_helpers import complete_review, plan_na_gates
from execution_helpers import bound, register_task, report
from launcher_context import admit_planning
from test_implementation_continuity import worker


def ok(cli, *args):
    value = cli(*args)
    assert value[0] == 0, value
    return value


def prepare_bound(cli, tmp_path, repo, monkeypatch):
    controller, eid, sid = bound(cli, tmp_path, repo, monkeypatch)
    ok(cli, 'spec-gate', 'SPEC_READY')
    ok(cli, 'classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')
    assert register_task(cli, tmp_path, 'A', ['svc', 'web', 'docs', '.gitignore', 'pyproject.toml'])[0] == 0
    plan_na_gates(cli, tmp_path, ('svc/app.py',))
    admit_planning(cli)
    ok(cli, 'phase', 'IMPLEMENTATION')
    ok(cli, 'integration', 'task-admit', '--task', 'A')
    return controller, eid, sid


def ingest_all(controller, eid, sid):
    from harness.execution_projection import ingest_event
    for event in S.load(sid)['integration']['outbox']:
        ingest_event(controller.conn, eid, event)


def test_real_failed_pytest_receipt_atomic_incident_and_validity_replay(cli, tmp_path, repo, monkeypatch):
    controller, eid, sid = prepare_bound(cli, tmp_path, repo, monkeypatch)
    try:
        monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
        (repo / 'svc/test_failure.py').write_text('def test_actual_assertion():\n    assert 2 + 2 == 5\n')
        ok(cli, 'evidence', 'plan', '--area', 'arbitrary-name-not-a-category', '--kind', 'test',
           '--tier', 'focused', '--scope', 'svc', '--purpose', 'unit', '--env', 'PYTHONDONTWRITEBYTECODE',
           '--', sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--assert=plain', 'svc/test_failure.py')
        failed = cli('evidence', 'run', '--area', 'arbitrary-name-not-a-category')
        assert failed[0] != 0, failed
        state = S.load(sid)
        entry = state['evidence']['entries'][-1]
        assert entry['status'] == 'FAIL', failed
        reference = state['integration']['incident_results'][entry['id']]
        receipt = json.loads(Path(reference['stored_path']).read_bytes())
        assert receipt['purpose'] == 'unit' and receipt['status'] == 'FAIL'
        events = [e for e in state['integration']['outbox'] if e['kind'] == 'test.result' and e['payload']['id'] == entry['id']]
        assert events[-1]['payload']['source_receipt']['sha256'] == reference['sha256']
        ingest_all(controller, eid, sid)
        rows = controller.conn.execute('SELECT * FROM reliability_incident_catalog WHERE source_kind=\'al_test_result\'').fetchall()
        assert len(rows) == 1 and rows[0]['category'] == 'verification_failed'
        assert controller.conn.execute('SELECT count(*) FROM reliability_observations').fetchone()[0] == 0
        (repo / 'svc/app.py').write_text('changed_after_result = True\n')
        ok(cli, 'show')
        # A legitimate subsequent save refreshes semantic validity while the accepted receipt stays exact.
        ok(cli, 'evidence', 'plan', '--area', 'later', '--kind', 'check', '--scope', 'svc', '--', sys.executable, '-V')
        assert S.load(sid)['integration']['incident_results'][entry['id']] == reference
        ingest_all(controller, eid, sid)
        assert controller.conn.execute('SELECT count(*) FROM reliability_incident_catalog WHERE source_kind=\'al_test_result\'').fetchone()[0] == 1
    finally:
        controller.conn.close()


def test_actual_accepted_review_snapshot_then_missing_or_altered_bytes_refuse_atomically(cli, tmp_path, repo, monkeypatch):
    controller, eid, sid = prepare_bound(cli, tmp_path, repo, monkeypatch)
    try:
        worker(cli, tmp_path)
        (repo / 'svc/app.py').write_text('answer = 42\n')
        ok(cli, 'writer', 'finish', '--id', 'write-A', '--outcome', 'success')
        assert report(cli, tmp_path, 'agent-update', {'id': 'engineer', 'status': 'Completed'})[0] == 0
        ok(cli, 'gates', 'run', '--stage', 'candidate')
        ok(cli, 'evidence', 'plan', '--area', 'compile', '--tier', 'broad', '--kind', 'check', '--scope', 'svc', '--', sys.executable, '-m', 'compileall', '-q', 'svc')
        ok(cli, 'evidence', 'run', '--area', 'compile')
        ok(cli, 'integration', 'task-finish', '--task', 'A')
        ok(cli, 'phase', 'VERIFICATION')
        complete_review(cli, tmp_path, blocking=True)
        state = S.load(sid)
        events = state['integration']['outbox']
        accepted = next(e for e in events if e['kind'] == 'review.accepted' and e['payload']['stage'] == 'compare')
        from harness.execution_projection import ingest_event
        from harness.execution_store import get_execution
        for event in events[:accepted['sequence'] - 1]:
            ingest_event(controller.conn, eid, event)
        ref = state['review']['comparison']
        path = Path(ref['stored_path'])
        raw = path.read_bytes()
        before = get_execution(controller.conn, eid)['cursor']
        path.write_bytes(raw + b' changed')
        with pytest.raises(ValueError, match='accepted incident source'):
            ingest_event(controller.conn, eid, accepted)
        assert get_execution(controller.conn, eid)['cursor'] == before
        assert controller.conn.execute('SELECT count(*) FROM incident_event_sources WHERE source_kind=\'al_review_finding\'').fetchone()[0] == 0
        path.write_bytes(raw)
        # Caller-selected report identity/root is never an import capability.
        foreign = copy.deepcopy(accepted)
        foreign['payload']['reference']['id'] = foreign['payload']['reference']['sha256'] = '0' * 64
        with pytest.raises(ValueError, match='accepted incident source'):
            ingest_event(controller.conn, eid, foreign)
        source_path = S.state_path(sid)
        original_state = source_path.read_bytes()
        redirected = json.loads(original_state)
        redirected['integration']['incident_reviews'][ref['sha256']]['stored_path'] = str(tmp_path / ref['sha256'])
        (tmp_path / ref['sha256']).write_bytes(raw)
        source_path.write_text(json.dumps(redirected))
        try:
            with pytest.raises(ValueError, match='accepted incident source'):
                ingest_event(controller.conn, eid, accepted)
        finally:
            source_path.write_bytes(original_state)
        assert get_execution(controller.conn, eid)['cursor'] == before
        import sqlite3
        controller.conn.execute("CREATE TRIGGER incident_fault BEFORE INSERT ON reliability_incident_catalog WHEN NEW.source_kind='al_review_finding' BEGIN SELECT RAISE(ABORT,'injected source fault'); END")
        with pytest.raises(sqlite3.IntegrityError, match='injected source fault'):
            ingest_event(controller.conn, eid, accepted)
        assert get_execution(controller.conn, eid)['cursor'] == before
        assert controller.conn.execute('SELECT count(*) FROM execution_events WHERE event_id=?', (accepted['event_id'],)).fetchone()[0] == 0
        assert not controller.conn.in_transaction
        controller.conn.execute('DROP TRIGGER incident_fault')
        import harness.reliability.event_sources as sources
        original_read = sources.read_artifact
        def replace_after_read(directory, reference, **kwargs):
            buffer = original_read(directory, reference, **kwargs)
            path.write_bytes(b'{"untrusted":"SECRET_SENTINEL_AFTER_READ"}')
            return buffer
        monkeypatch.setattr(sources, 'read_artifact', replace_after_read)
        assert ingest_event(controller.conn, eid, accepted)
        monkeypatch.setattr(sources, 'read_artifact', original_read)
        # Exact accepted replay needs no new report read; missing bytes cannot create another fact.
        assert not ingest_event(controller.conn, eid, copy.deepcopy(accepted))
        path.write_bytes(raw)
        assert not ingest_event(controller.conn, eid, copy.deepcopy(accepted))
        row = controller.conn.execute('SELECT * FROM reliability_incident_catalog WHERE source_kind=\'al_review_finding\'').fetchone()
        assert row['category'] == 'review_finding' and row['provenance'] == 'validated_review_finding'
        assert controller.conn.execute('SELECT count(*) FROM reliability_observations').fetchone()[0] == 0
    finally:
        controller.conn.close()
