"""Cheap writer refusals precede full current-source admission and provenance."""
import copy
from argparse import Namespace

import pytest
import al_store as S
import al_writers as W


def arguments(**changes):
    values = dict(action='start', id='writer', role='al-engineer', files='svc/app.py',
                  commands_file=None, degraded=False, reason='', task=None, agent=None)
    return Namespace(**(values | changes))


@pytest.mark.parametrize('failure', ['phase', 'status', 'id', 'role', 'path', 'commands', 'writer-overlap', 'check-overlap'])
def test_cheap_refusals_never_enter_full_readiness(repo, tmp_path, monkeypatch, failure):
    import al_new_origin as N
    import al_gate_coverage as G
    st = {'status': 'RUNNING', 'phase': 'IMPLEMENTATION', 'classification': 'STANDARD',
          'writers': {}, 'evidence': {'entries': []}}
    args = arguments()
    if failure == 'phase': st['phase'] = 'PLANNING'
    elif failure == 'status': st['status'] = 'WAITING_FOR_USER'
    elif failure == 'id': args.id = 'invalid writer'
    elif failure == 'role': args.role = 'al-architect'
    elif failure == 'path': args.files = '../escape'
    elif failure == 'commands':
        path = tmp_path / 'commands.json';path.write_text('{}');args.commands_file = str(path)
    elif failure == 'writer-overlap':
        st['writers']['other'] = {'id': 'other', 'state': 'ACTIVE', 'commands': [], 'files': ['svc']}
    else:
        st['evidence']['entries'].append({'id': 'E1', 'status': 'RUNNING', 'scope': ['svc'], 'argv': ['pytest']})
    before = copy.deepcopy(st)
    monkeypatch.setattr(S, 'load', lambda sid: st)
    saved = []
    monkeypatch.setattr(S, 'save', lambda *args: saved.append(args))
    def expensive(*args, **kwargs):
        pytest.fail('full readiness ran before a cheap writer refusal')
    monkeypatch.setattr(N, 'require_implementation_ready', expensive)
    monkeypatch.setattr(G, 'writer_ready', expensive)
    with pytest.raises(SystemExit):
        W.cmd_writer(args, 'fixture')
    assert st == before and not saved


def test_successful_admission_runs_full_readiness_once_and_retains_capture(cli, repo, tmp_path, monkeypatch):
    from evidence_helpers import start_implementation
    import al_implementation as I
    import al_task_attribution as T
    start_implementation(cli, tmp_path)
    full, captures = [], []
    original_ready, original_capture = I.require_ready, T.begin_contribution
    def ready(st, **kwargs):
        full.append(kwargs)
        return original_ready(st, **kwargs)
    def capture(st, writer):
        captures.append(copy.deepcopy(writer))
        return original_capture(st, writer)
    monkeypatch.setattr(I, 'require_ready', ready)
    monkeypatch.setattr(T, 'begin_contribution', capture)
    result = cli('writer', 'start', '--id', 'writer', '--role', 'al-engineer', '--files', 'svc/app.py')
    assert result[0] == 0, result
    assert 'AL · writer start ·' not in result[1]
    assert 'assessing current scoped implementation readiness' in result[2]
    assert 'publishing admitted writer' in result[2]
    assert 'svc/app.py' not in result[2]
    assert full == [{'task_id': None, 'paths': ['svc/app.py']}]
    assert captures == [{'id': 'writer', 'files': ['svc/app.py']}]
    st = cli.state()
    assert st['writers']['writer']['state'] == 'ACTIVE'
    assert st['writers']['writer']['contribution_before']
    assert st['implementation_readiness']['paths'] == ['svc/app.py']


def test_scoped_readiness_failure_never_starts_or_saves_writer(cli, repo, tmp_path, monkeypatch):
    from evidence_helpers import start_implementation
    import al_gate_coverage as G
    import al_task_attribution as T
    start_implementation(cli, tmp_path)
    before = S.state_path(cli.state()['session_id']).read_bytes()
    def refuse(st, paths):
        raise SystemExit('synthetic current-source refusal')
    def capture(*args):
        pytest.fail('contribution captured before current scoped readiness')
    monkeypatch.setattr(G, 'writer_ready', refuse)
    monkeypatch.setattr(T, 'begin_contribution', capture)
    result = cli('writer', 'start', '--id', 'writer', '--role', 'al-engineer', '--files', 'svc/app.py')
    assert result[0] != 0
    assert S.state_path(cli.state()['session_id']).read_bytes() == before


@pytest.mark.parametrize('failure', [None, 'authority', 'scope', 'task-status', 'receipt', 'agent'])
def test_task_authority_is_retained_before_scoped_readiness(repo, monkeypatch, failure):
    import al_execution_tasks as E
    import al_execution_outputs as O
    import al_implementation as I
    import al_gate_coverage as G
    import al_task_attribution as T
    st = {'status': 'RUNNING', 'phase': 'IMPLEMENTATION', 'classification': 'STANDARD',
          'writers': {}, 'evidence': {'entries': [], 'plan': {}},
          'integration': {'graph_revision': 1, 'blockers': {}, 'revalidations': {},
              'tasks': {'A': {'status': 'Running', 'revision': 1, 'files': ['svc'],
                             'dependencies': [], 'admission': {'revision': 1, 'graph_revision': 1}}},
              'agents': {'agent': {'task': 'A', 'role': 'al-engineer', 'status': 'Running'}}}}
    args = arguments(task='A', agent='agent')
    if failure == 'scope': args.files = 'docs/readme.md'
    elif failure == 'task-status': st['integration']['tasks']['A']['status'] = 'Waiting'
    elif failure == 'receipt': st['integration']['tasks']['A']['admission']['revision'] = 0
    elif failure == 'agent': st['integration']['agents']['agent']['status'] = 'Completed'
    before = copy.deepcopy(st)
    calls = []
    monkeypatch.setattr(S, 'load', lambda sid: st)
    monkeypatch.setattr(S, 'save', lambda *args: calls.append('save'))
    monkeypatch.setattr(I, 'require_preflight', lambda st: calls.append('preflight'))
    def authority(state, operation, task):
        assert state is st and operation == 'admit' and task == 'A'
        calls.append('authority')
        if failure == 'authority': raise SystemExit('sealed authority refused')
    monkeypatch.setattr(E, 'authority', authority)
    monkeypatch.setattr(G, 'writer_ready', lambda st, paths: calls.append('readiness'))
    monkeypatch.setattr(O, 'invalidate_for_write', lambda *args: calls.append('invalidate'))
    monkeypatch.setattr(T, 'begin_contribution', lambda *args: calls.append('capture'))
    if failure is None:
        W.cmd_writer(args, 'fixture')
        assert calls == ['preflight', 'authority', 'readiness', 'invalidate', 'capture', 'save']
        assert st['writers']['writer']['task'] == 'A'
    else:
        with pytest.raises(SystemExit): W.cmd_writer(args, 'fixture')
        assert 'readiness' not in calls and 'capture' not in calls and 'save' not in calls
        assert st == before


@pytest.mark.parametrize('failure', ['identity', 'specification', 'classification', 'recovery', 'origin'])
def test_extracted_preflight_preserves_actual_identity_and_origin_checks(cli, repo, tmp_path, failure):
    from evidence_helpers import start_implementation
    from al_implementation import require_preflight
    start_implementation(cli, tmp_path)
    st = cli.state()
    if failure == 'identity': st['identity']['root'] = str(repo / 'foreign')
    elif failure == 'specification': st['spec_status'] = 'SPEC_BLOCKED'
    elif failure == 'classification': st['classification'] = 'UNKNOWN'
    elif failure == 'recovery': st['rework_recovery']['origin']['run_id'] = 'foreign'
    else: st['runtime_origin']['pin'] = {'foreign': 'pin'}
    before = S.state_path(st['session_id']).read_bytes()
    with pytest.raises(SystemExit): require_preflight(st)
    assert S.state_path(st['session_id']).read_bytes() == before


def test_writer_progress_context_closes_after_refusal_and_finish_stays_quiet(monkeypatch, capsys):
    import al_task_attribution as T
    from al_writer_progress import report
    st = {'status': 'RUNNING', 'phase': 'PLANNING', 'classification': 'STANDARD',
          'writers': {}, 'evidence': {'entries': [], 'plan': {}}}
    monkeypatch.setattr(S, 'load', lambda sid: st)
    monkeypatch.setattr(S, 'save', lambda *args: None)
    with pytest.raises(SystemExit): W.cmd_writer(arguments(), 'fixture')
    failed = capsys.readouterr()
    assert 'checking writer request' in failed.err and failed.out == ''
    report('readiness', force=True)
    assert capsys.readouterr().err == ''
    # A registered writer always records its files; writer finish settles gate currency with them (a556bc4, #32).
    st['writers']['writer'] = {'id': 'writer', 'state': 'ACTIVE', 'files': []}
    monkeypatch.setattr(T, 'finish_contribution', lambda *args: None)
    W.cmd_writer(arguments(action='finish', outcome='success'), 'fixture')
    finished = capsys.readouterr()
    assert finished.out == '' and finished.err == ''
