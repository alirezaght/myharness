"""Operator update public behavior invariants."""

import pytest

from al.tests.operator_update_helpers import (
    O,
    Path,
    _prefix_cache as _prefix_cache,
    artifact,
    digest,
    enrolled as enrolled,
    git_test,
    json,
    load_record,
    public,
    read_json,
    retained,
    tag,
)


def test_public_explicit_mode_warns_and_preserves_all_retained_bytes(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = retained(enrolled)
    refused = public(enrolled, req)
    assert refused.returncode == 3 and refused.data['status'] == 'deferred'
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.data['activity']['settlement'] == 'NOT_ASSESSED'
    for key in ('active-or-unsettled-writers', 'active-or-unsettled-evidence', 'unsupported', 'oversize', 'unreadable-or-malformed'):
        assert result.data['activity']['counts'][key] > 0
    assert retained(enrolled) == before
    current = load_record(w['state'], target['target_id'])
    assert current['previous'] == row['current'] and current['policy'] == row['policy']
    O.check_current(current, w['state'])
    receipt = read_json(current['current']['journal'])
    assert [r['barrier'] for r in receipt['operator_update_barriers']] == [
        'pre-apply', 'before-platform:codex', 'before-platform:claude', 'before-completed-publication']
    assert not receipt['ancillary_actions']
    assert receipt['operator_update']['kind'] == 'operator-global-package-update-receipt-v1'


def test_managed_old_pin_stays_old_and_separate_bootstrap_selects_new(enrolled):
    import os
    import shutil
    import sys

    from al.tests.conftest import copy_al_source
    from al.tests.runtime_run_helpers import invoke as invoke_raw

    def invoke(*args):
        result = invoke_raw(*args)
        try:
            result.data = json.loads(result.stdout)
        except json.JSONDecodeError:
            result.data = None
        return result

    w, target, row, runtime = enrolled
    (w['state'] / 'al-run-pins').chmod(0o700)
    repo = w['root'] / 'repository'; repo.mkdir()
    git_test(repo, 'init', '-q'); git_test(repo, 'commit', '--allow-empty', '-qm', 'fixture repository')
    launcher = w['home'] / 'bin/alctl'
    env = dict(os.environ, HOME=str(w['home']), PYTHONDONTWRITEBYTECODE='1')
    for name in ('AL_RUNTIME_DIR', 'AL_DISTRIBUTION_STATE', 'PYTHONPATH', 'PYTHONHOME',
                 'CLAUDE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_PROJECT_DIR'):
        env.pop(name, None)
    def boot(source):
        result = invoke([sys.executable, '-I', '-S', '-B', source / 'alctl.py', 'bootstrap',
                         '--source-root', source, '--destination', launcher, '--state-root', w['state']], repo, env)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.data
    def run(session, *args):
        return invoke([launcher, 'run', '--repository', repo, '--platform', 'codex', '--session', session, '--', *args], repo, env)
    old_source = copy_al_source(w['root'] / 'old-bootstrap')
    old_boot = boot(old_source)
    started = run('old-session', 'start', '--spec', 'Retain the old protected runtime.')
    assert started.returncode == 0, started.stdout + started.stderr
    cache, manifest = tag(enrolled)
    req = artifact(enrolled, cache, manifest)
    old_state = repo / '.codex/al-runtime/old-session.json'
    before = retained(enrolled)
    before[str(old_state)] = (old_state.read_bytes(), old_state.stat().st_mode)
    snapshot = {str(p): p.read_bytes() for p in Path(old_boot['snapshot']).rglob('*') if p.is_file()}
    result = invoke([launcher, 'update', '--global', '--home', w['home'], '--state-root', w['state'],
                     '--version', '1.1.0', '--reviews', req['operator_review_artifact']['path'],
                     '--runtime-root', runtime, '--allow-running'], repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(Path(p).read_bytes() == raw and Path(p).stat().st_mode == mode for p, (raw, mode) in before.items())
    assert all(Path(p).read_bytes() == raw for p, raw in snapshot.items())
    shown = run('old-session', 'show', '--json')
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert shown.data['run_id'] == read_json(old_state)['run_id']
    new_source = copy_al_source(w['root'] / 'new-bootstrap')
    for platform in ('codex', 'claude'):
        shutil.rmtree(new_source / platform)
    for name, item in manifest['files'].items():
        path = new_source / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((cache / 'payload' / name).read_bytes()); path.chmod(item['mode'])
    boot(new_source)  # Separate supported coordinator publication, never implicit in update.
    fresh = run('new-session', 'start', '--spec', 'Select the newly bootstrapped runtime.')
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    bindings = [read_json(p) for p in (w['state'] / 'al-run-pins/runs').glob('*.json')]
    old, new = [next(b for b in bindings if b.get('session_id') == sid) for sid in ('old-session', 'new-session')]
    name = 'payload/codex/skills/al/scripts/reviewed_addition.py'
    assert not (w['state'] / 'al-run-pins/pins' / old['pin']['pin_identity'] / name).exists()
    added = w['state'] / 'al-run-pins/pins' / new['pin']['pin_identity'] / name
    assert digest(added.read_bytes()) == manifest['files'][name.removeprefix('payload/')]['sha256']
    assert old['pin']['pin_identity'] != new['pin']['pin_identity']
    assert all(Path(p).read_bytes() == raw and Path(p).stat().st_mode == mode for p, (raw, mode) in before.items())


@pytest.mark.parametrize('enrolled', ['status-line', 'follow'], indirect=True)
def test_existing_configuration_and_policy_are_preserved(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    before = {name: Path(name).read_bytes() for name in row['current']['roles']}
    configs = {p: (w['home'] / ('.' + p) / ('config.toml' if p == 'codex' else 'settings.json'))
               for p in target['platforms']}
    raw = {p: path.read_bytes() if path.exists() else None for p, path in configs.items()}
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 0, result.stdout + result.stderr
    current = load_record(w['state'], target['target_id'])
    assert current['policy'] == row['policy']
    assert current['current']['status_line'] == row['current']['status_line']
    assert {p: path.read_bytes() if path.exists() else None for p, path in configs.items()} == raw
    assert any(Path(name).read_bytes() != data for name, data in before.items())  # reviewed bodies changed
