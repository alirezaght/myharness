"""Parallel writers with disjoint ownership: a sibling still editing never fails the other.

Each writer's contribution captures and attributes only paths outside the other ACTIVE
writers' ownership. Edits outside every writer's scope, and edits inside a writer's own scope
while its capture is taken, are still observed and refused or reported."""
import json
import al_artifacts as A
import al_task_attribution as T
from evidence_helpers import start_implementation
from test_task_attribution import run, state


def _start_two(cli, tmp_path):
    start_implementation(cli, tmp_path, ('svc', 'web'))
    run(cli, 'writer', 'start', '--id', 'left', '--role', 'al-engineer', '--files', 'svc')


def _editing(monkeypatch, repo, path):
    """Every repository observation sees `path` with new content, as a sibling mid-edit."""
    original = T._rows
    counter = [0]

    def rows(st):
        counter[0] += 1
        (repo/path).write_text('edit = %d\n' % counter[0])
        return original(st)
    monkeypatch.setattr(T, '_rows', rows)
    return lambda: monkeypatch.setattr(T, '_rows', original)


def test_disjoint_writers_start_and_finish_while_sibling_edits(cli, repo, tmp_path, state_file, monkeypatch):
    _start_two(cli, tmp_path)
    (repo/'svc/app.py').write_text('x = 2\n')
    # `right` is admitted while `left` is still editing inside svc.
    stop = _editing(monkeypatch, repo, 'svc/app.py')
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    stop()
    (repo/'svc/app.py').write_text('x = 3\n')
    (repo/'web/app.js').write_text('let a = 2\n')
    # `left` finishes while `right` is still editing inside web.
    stop = _editing(monkeypatch, repo, 'web/app.js')
    run(cli, 'writer', 'finish', '--id', 'left', '--outcome', 'success')
    stop()
    (repo/'web/app.js').write_text('let a = 3\n')
    run(cli, 'writer', 'finish', '--id', 'right', '--outcome', 'success')
    st = state(state_file)
    result = T.classify_changes(st)
    assert result['task_owned'] == ['svc/app.py', 'web/app.js']
    assert not result['ambiguous'] and not result['externally_changed']
    left = A.read_json(st['task_attribution']['contributions'][0])
    assert left['writer_id'] == 'left' and left['changed_records'] == ['svc/app.py']
    after = A.read_json(left['after_capture_ref'])
    assert after['concurrent_writer_paths'] == ['web']
    assert not any(r['path'].startswith('web/') for r in after['files'])


def test_concurrent_edit_outside_every_writer_scope_still_refuses_finish(cli, repo, tmp_path, state_file, monkeypatch):
    _start_two(cli, tmp_path)
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    (repo/'svc/app.py').write_text('x = 2\n')
    before = state_file().read_bytes()
    stop = _editing(monkeypatch, repo, 'docs/readme.md')
    result, out, err = cli('writer', 'finish', '--id', 'left', '--outcome', 'success')
    stop()
    assert result != 0 and 'unstable task attribution observation' in f'{result}{out}{err}'
    assert state_file().read_bytes() == before


def test_concurrent_edit_inside_own_scope_still_refuses_finish(cli, repo, tmp_path, state_file, monkeypatch):
    _start_two(cli, tmp_path)
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    before = state_file().read_bytes()
    stop = _editing(monkeypatch, repo, 'svc/app.py')
    result, out, err = cli('writer', 'finish', '--id', 'left', '--outcome', 'success')
    stop()
    assert result != 0 and 'unstable task attribution observation' in f'{result}{out}{err}'
    assert state_file().read_bytes() == before


def test_out_of_scope_edit_during_parallel_writers_is_external(cli, repo, tmp_path, state_file):
    _start_two(cli, tmp_path)
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    (repo/'svc/app.py').write_text('x = 2\n')
    (repo/'docs/readme.md').write_text('outside every writer scope\n')
    run(cli, 'writer', 'finish', '--id', 'left', '--outcome', 'success')
    (repo/'web/app.js').write_text('let a = 2\n')
    run(cli, 'writer', 'finish', '--id', 'right', '--outcome', 'success')
    result = T.classify_changes(state(state_file))
    assert result['task_owned'] == ['svc/app.py', 'web/app.js']
    assert result['externally_changed'] == ['docs/readme.md']


def test_overlapping_writer_ownership_is_still_refused(cli, repo, tmp_path):
    _start_two(cli, tmp_path)
    result, out, err = cli('writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'svc/app.py')
    assert result != 0 and 'writer overlap: left' in f'{result}{out}{err}'


def _drifting_discovery(monkeypatch):
    from al_repository_capture import CaptureDrift
    import al_gate_discovery as D

    def drift(*args, **kwargs):
        raise CaptureDrift('source-content-or-identity', ['web/app.js'])
    monkeypatch.setattr(D, 'discover', drift)


def test_gates_discover_names_active_sibling_instead_of_capture_drift(cli, repo, tmp_path, state_file, monkeypatch):
    _start_two(cli, tmp_path)
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    paths = tmp_path/'paths.json'
    paths.write_text(json.dumps(['svc']))
    _drifting_discovery(monkeypatch)
    before = state_file().read_bytes()
    result, out, err = cli('gates', 'discover', '--paths-file', str(paths))
    text = f'{result}{out}{err}'
    assert result != 0 and 'writer right still active; run after writer finish' in text
    assert 'web/app.js' in text and 'capture drift' not in text
    assert state_file().read_bytes() == before


def test_gates_discover_drift_without_active_writer_is_reported_unchanged(cli, repo, tmp_path, monkeypatch):
    start_implementation(cli, tmp_path, ('svc', 'web'))
    paths = tmp_path/'paths.json'
    paths.write_text(json.dumps(['svc']))
    _drifting_discovery(monkeypatch)
    result, out, err = cli('gates', 'discover', '--paths-file', str(paths))
    assert result != 0 and 'coverage capture drift' in f'{result}{out}{err}'


def _writer_check(cli, writer):
    from evidence_helpers import PY
    return cli('evidence', 'run', '--area', 'lint', '--kind', 'check', '--scope', 'svc,web',
               *(('--writer', writer) if writer else ()), '--', PY, '-m', 'compileall', '-q', 'svc')


def _lint_verdict(state_file):
    import al_ledger as L
    st = state(state_file)
    entry = [e for e in st['evidence']['entries'] if e['area'] == 'lint'][-1]
    return entry, L.entry_verdict(st, entry)


def test_writer_check_overlapping_sibling_is_provisional_then_revalidated(cli, repo, tmp_path, state_file):
    _start_two(cli, tmp_path)
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    (repo/'svc/app.py').write_text('x = 2\n')
    (repo/'web/app.js').write_text('let a = 2\n')
    result, out, err = _writer_check(cli, 'left')
    assert result == 0, f'{result}{out}{err}'
    entry, (verdict, why) = _lint_verdict(state_file)
    assert entry['provisional']['writers'] == ['right']
    assert verdict == 'PROVISIONAL' and why == 'needs revalidation after writer right finishes'
    result, out, err = cli('evidence', 'status')
    assert 'PROVISIONAL' in out and 'needs revalidation after writer right finishes' in out
    # The sibling finishes without changing anything the check read: the result now counts.
    result, out, err = cli('writer', 'finish', '--id', 'right', '--outcome', 'success')
    assert result == 0 and 'provisional on right: now VALID' in out, f'{result}{out}{err}'
    assert _lint_verdict(state_file)[1][0] == 'VALID'


def test_provisional_check_is_stale_when_sibling_changed_its_inputs(cli, repo, tmp_path, state_file):
    _start_two(cli, tmp_path)
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    result, out, err = _writer_check(cli, 'left')
    assert result == 0, f'{result}{out}{err}'
    (repo/'web/app.js').write_text('let a = 3\n')
    result, out, err = cli('writer', 'finish', '--id', 'right', '--outcome', 'success')
    assert result == 0 and 'provisional on right: now STALE' in out and 'rerun' in out, f'{result}{out}{err}'
    assert _lint_verdict(state_file)[1][0] == 'STALE'


def test_provisional_check_stays_unaccepted_while_sibling_unresolved(cli, repo, tmp_path, state_file):
    _start_two(cli, tmp_path)
    run(cli, 'writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    result, out, err = _writer_check(cli, 'left')
    assert result == 0, f'{result}{out}{err}'
    run(cli, 'writer', 'finish', '--id', 'right', '--outcome', 'failed')
    assert _lint_verdict(state_file)[1][0] == 'PROVISIONAL'


def test_check_without_writer_overlapping_sibling_is_still_refused(cli, repo, tmp_path, state_file):
    _start_two(cli, tmp_path)
    before = state_file().read_bytes()
    result, out, err = _writer_check(cli, None)
    text = f'{result}{out}{err}'
    assert result != 0 and 'check overlaps unresolved writer left' in text and '--writer' in text
    assert state_file().read_bytes() == before


def test_writer_start_gate_refresh_drift_names_active_sibling(cli, repo, tmp_path, state_file, monkeypatch):
    from al_repository_capture import CaptureDrift
    import al_gate_coverage
    _start_two(cli, tmp_path)

    def drift(st, paths):
        raise CaptureDrift('repeated-drift', ['svc/app.py'])
    monkeypatch.setattr(al_gate_coverage, 'writer_ready', drift)
    before = state_file().read_bytes()
    result, out, err = cli('writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    text = f'{result}{out}{err}'
    assert result != 0 and 'writer start: writer left still active; run after writer finish' in text
    assert 'svc/app.py' in text and 'capture drift' not in text
    assert state_file().read_bytes() == before


def test_writer_start_currency_drift_names_active_sibling_not_coverage_debt(cli, repo, tmp_path, state_file, monkeypatch):
    import al_gate_currency
    _start_two(cli, tmp_path)

    def drifted(st):
        return {'current': False, 'reasons': ['coverage capture unavailable: repeated-drift'],
                'superseded': None, 'paths': [], 'pathless': True, 'drift_paths': ['svc/app.py']}
    monkeypatch.setattr(al_gate_currency, 'check', drifted)
    before = state_file().read_bytes()
    result, out, err = cli('writer', 'start', '--id', 'right', '--role', 'al-engineer', '--files', 'web')
    text = f'{result}{out}{err}'
    assert result != 0 and 'writer start: writer left still active; run after writer finish' in text
    assert 'svc/app.py' in text
    assert state_file().read_bytes() == before
