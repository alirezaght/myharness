"""Real harness approval plus real guarded AL CLI for protocol tests."""
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))


def bound(cli, tmp_path, repo, monkeypatch, *, bind_source=True):
    from harness.approval import ApprovalGate
    from harness.controller import HarnessController
    from harness.db import connect
    from harness.execution import start_execution
    from harness.execution_projection import ingest_event
    from harness.execution_store import get_execution
    from harness.spec_store import SpecStore
    helper = importlib.util.spec_from_file_location('p3_settle', ROOT / 'tests/complete_task.py')
    module = importlib.util.module_from_spec(helper); helper.loader.exec_module(module)
    monkeypatch.setenv('HARNESS_HOME', str(tmp_path / 'harness-home'))
    monkeypatch.setenv('HARNESS_AUTHORITY_ROOT', str(ROOT))
    monkeypatch.setenv('PYTHONPATH', str(ROOT) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    conn = connect(tmp_path / 'harness.db')
    controller = HarnessController(conn)
    task = controller.create_task('Implement task-scoped execution with observable verification', 'trivial')
    module.settle(controller, task.id); controller.advance(task.id)
    ApprovalGate(controller, task.id, SpecStore(repo / 'docs/specs' / task.id), repo).approve()
    launch = start_execution(controller, task.id, launch=False)
    eid = launch['execution']['id']; saved = get_execution(conn, eid)
    handoff = Path(saved['handoff_path'])
    package = handoff.parent / 'task-package.json'
    import al_store
    runtime = handoff.parent / 'al-runtime'
    monkeypatch.setattr(al_store, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(al_store, 'CURRENT', runtime / 'current')
    monkeypatch.setenv('AL_RUNTIME_DIR', str(runtime))
    code, out, err = cli('--session', saved['al_session_id'], 'start', '--package-file', str(package), '--handoff', str(handoff))
    assert code == 0, (code, out, err)
    # Harness ingestion binds exactly the actual AL start event, as the worker does.
    state = json.loads(al_store.state_path(saved['al_session_id']).read_text())
    if bind_source:
        conn.execute("UPDATE executions SET worker_token='owner' WHERE id=?", (eid,))
        for event in state['integration']['outbox']:
            ingest_event(conn, eid, event)
    # Subsequent in-process fixture CLI commands must target this explicit session.
    if 'SESSION' in cli.__globals__:
        monkeypatch.setitem(cli.__globals__, 'SESSION', saved['al_session_id'])
    return controller, eid, state['session_id']


def report(cli, tmp_path, action, data):
    path = tmp_path / ('report-' + action + '.json')
    path.write_text(json.dumps(data))
    return cli('integration', action, '--file', str(path))


def register_task(cli, tmp_path, tid, files, dependencies=()):
    return report(cli, tmp_path, 'task-register', {'id': tid, 'title': 'Implement ' + tid, 'files': files, 'criteria': ['AC-01'], 'dependencies': list(dependencies)})
