"""Authorized baseline exceptions admit implementation without hiding or repairing a failure."""
import json
import sys
from pathlib import Path

import pytest

from hook_regression_helpers import invoke
from launcher_context import admit_planning
from test_hook_batch6 import _manifest, _source

FAIL = [sys.executable, '-c', 'import sys;print("3 failed, 65 passed");sys.exit(1)']


def failing_base(cli, repo, tmp_path, *, second=False, env_names=()):
    invoke(cli, 'start', '--spec', 'Implement the feature while an unrelated baseline UI job fails.')
    invoke(cli, 'spec-gate', 'SPEC_READY');invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    state = json.loads(cli('show')[1])
    m = _manifest(state, source=_source(repo, 'pyproject.toml'), active='loc', argv=FAIL)
    loc = next(g for g in m['gates'] if g['id'] == 'loc');loc['env_names'] = list(env_names)
    if second:
        other = next(g for g in m['gates'] if g['id'] == 'contracts')
        other.update(applicability='APPLICABLE', required=True, argv=FAIL, source=loc['source'], reason='second failing job')
    path = tmp_path / 'manifest.json';path.write_text(json.dumps(m))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    invoke(cli, 'gates', 'run', '--stage', 'base', ok=False)
    return path


def status(cli):
    code, out, err = cli('gates', 'status')
    assert code == 0, (code, out, err)
    return json.loads(out)


def except_loc(cli, ok=True):
    return invoke(cli, 'gates', 'decision', '--id', 'loc', '--action', 'exception', '--source',
                  'user 2026-10-22: implement while the unchanged admin UI job stays red', '--reason',
                  'existing unchanged-base failure; repair tracked outside this task', ok=ok)


def loc_entry(cli):
    state = json.loads(cli('show')[1])
    run = [r for r in state['gate_runs'] if r['gate'] == 'loc' and r['stage'] == 'base'][-1]
    return state, next(e for e in state['evidence']['entries'] if e['id'] == run['evidence'])


def test_exception_admits_implementation_keeps_fail_and_shows_separately(cli, repo, tmp_path):
    failing_base(cli, repo, tmp_path)
    assert any('loc' in g for g in status(cli)['base'])
    except_loc(cli)
    current = status(cli)
    assert not any('loc' in g for g in current['base'])
    assert current['baseline_exceptions'][0]['status'] == 'current' and current['baseline_exceptions'][0]['evidence_status'] == 'FAIL'
    _, entry = loc_entry(cli)
    assert entry['status'] == 'FAIL'
    # Continuation processes the remaining base gates and retains the excepted failure as FAIL.
    _, out, _ = invoke(cli, 'gates', 'run', '--stage', 'base', '--resume')
    report = json.loads(next(line for line in reversed(out.strip().splitlines()) if line.startswith("{")))
    assert report['stage_ready'] and 'loc' not in report['selected'] and report['selected']
    assert report['base_decisions']['loc']['action'] == 'exception'
    assert loc_entry(cli)[1]['id'] == entry['id']
    admit_planning(cli)
    invoke(cli, 'phase', 'IMPLEMENTATION')
    # Candidate checks still run and report the same failure honestly; nothing approves around red checks.
    invoke(cli, 'writer', 'start', '--id', 'engineer', '--role', 'al-engineer', '--files', 'svc/app.py')
    (repo / 'svc/app.py').write_text('feature = True\n')
    invoke(cli, 'writer', 'finish', '--id', 'engineer', '--outcome', 'success')
    invoke(cli, 'gates', 'run', '--stage', 'candidate', ok=False)
    assert any('candidate gate loc requires passing evidence' in g for g in status(cli)['candidate'])


def test_exception_on_one_failure_cannot_satisfy_other_gates(cli, repo, tmp_path):
    failing_base(cli, repo, tmp_path, second=True)
    except_loc(cli)
    invoke(cli, 'gates', 'run', '--stage', 'base', '--resume', ok=False)
    gaps = status(cli)['base']
    assert not any('gate loc ' in g for g in gaps) and any('gate contracts ' in g for g in gaps)
    # The uncovered contracts failure holds back review (workflow_gaps), not implementation.
    import al_transitions
    assert any('gate contracts ' in g for g in al_transitions.workflow_gaps(json.loads(cli('show')[1])))


def test_tampered_log_or_replanned_manifest_makes_the_exception_stale(cli, repo, tmp_path):
    path = failing_base(cli, repo, tmp_path)
    except_loc(cli)
    _, entry = loc_entry(cli)
    log = Path(entry['log']);original = log.read_bytes()
    log.write_bytes(original + b'forged\n')
    assert any('loc' in g for g in status(cli)['base'])
    assert status(cli)['baseline_exceptions'][0]['status'] == 'stale'
    log.write_bytes(original)
    assert not any('loc' in g for g in status(cli)['base'])
    invoke(cli, 'gates', 'plan', '--file', str(path))
    assert any('loc' in g for g in status(cli)['base'])


def test_changed_declared_environment_makes_the_exception_stale(cli, repo, tmp_path, monkeypatch):
    monkeypatch.setenv('AL_UI_BROWSER', 'chromium')
    failing_base(cli, repo, tmp_path, env_names=('AL_UI_BROWSER',))
    except_loc(cli)
    assert not any('loc' in g for g in status(cli)['base'])
    monkeypatch.setenv('AL_UI_BROWSER', 'webkit')
    assert any('loc' in g for g in status(cli)['base'])


def test_exception_requires_exact_current_failure_and_authorization(cli, repo, tmp_path, state_file):
    invoke(cli, 'start', '--spec', 'Implement the feature while an unrelated baseline UI job fails.')
    invoke(cli, 'spec-gate', 'SPEC_READY');invoke(cli, 'classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    state = json.loads(cli('show')[1])
    m = _manifest(state, source=_source(repo, 'svc/app.py'), active='loc', argv=[sys.executable, '-c', 'print("ok")'])
    path = tmp_path / 'manifest.json';path.write_text(json.dumps(m))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    before = state_file().read_bytes()
    except_loc(cli, ok=False)  # no base execution yet
    invoke(cli, 'gates', 'run', '--stage', 'base')
    except_loc(cli, ok=False)  # a passing base needs no exception
    invoke(cli, 'gates', 'decision', '--id', 'loc', '--action', 'exception', '--source', ' ', '--reason', 'x', ok=False)
    assert json.loads(state_file().read_bytes()).get('baseline_exceptions') is None


def test_forged_exception_artifact_is_refused(cli, repo, tmp_path):
    failing_base(cli, repo, tmp_path)
    except_loc(cli)
    state, _ = loc_entry(cli)
    stored = Path(state['baseline_exceptions'][-1]['artifact']['stored_path'])
    value = json.loads(stored.read_bytes());value['binding']['evidence'] = 'E999'
    stored.write_text(json.dumps(value, sort_keys=True))
    assert any('loc' in g for g in status(cli)['base'])


def test_rerunning_the_base_gate_makes_the_exception_stale(cli, repo, tmp_path):
    failing_base(cli, repo, tmp_path)
    except_loc(cli)
    assert not any('loc' in g for g in status(cli)['base'])
    invoke(cli, 'gates', 'run', '--stage', 'base', '--id', 'loc', ok=False)
    assert any('loc' in g for g in status(cli)['base'])
    assert status(cli)['baseline_exceptions'][0]['status'] == 'stale'


def test_exception_cannot_be_recorded_after_the_run_stopped(cli, repo, tmp_path):
    failing_base(cli, repo, tmp_path)
    invoke(cli, 'gates', 'decision', '--id', 'loc', '--action', 'stop', '--source', 'user', '--reason', 'stop the run')
    except_loc(cli, ok=False)
