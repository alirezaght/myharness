"""Real candidate observations and immutable admitted contribution chains."""
import json
from pathlib import Path
import subprocess
import pytest
import al_artifacts as A
import al_task_attribution as T
from evidence_helpers import start_implementation


def state(state_file):
    return json.loads(state_file().read_text())


def run(cli, *args):
    result, out, err = cli(*args)
    assert result == 0, f"{args}: {result} {out[-1000:]} {err[-1000:]}"


def contribution(st, files, edit, wid='writer'):
    writer = {'id': wid, 'files': files, 'state': 'ACTIVE'}
    before = T.begin_contribution(st, writer)
    st['writers'][wid] = writer
    edit()
    writer.update(state='FINISHED', outcome='success')
    T.finish_contribution(st, writer, before)


def test_start_freezes_preexisting_content_without_claiming_task_change(cli, repo, state_file):
    (repo/'docs/readme.md').write_text('existing user content\n')
    run(cli, 'start', '--spec', 'Implement a small repair preserving independent user changes.')
    st = state(state_file)
    result = T.classify_changes(st)
    assert result['attributed']
    assert result['unchanged_preexisting'] == ['docs/readme.md']
    assert not result['discovery_paths']
    snapshot = A.read_json(st['task_attribution']['snapshot'])
    assert snapshot['kind'] == 'task-attribution-v1'
    assert A.read_json(snapshot['baseline_capture_ref'])['binding']['run_id'] == st['run_id']


def test_public_writer_contribution_retains_two_file_repair_and_external_change(cli, repo, tmp_path, state_file):
    (repo/'docs/readme.md').write_text('preexisting user content\n')
    start_implementation(cli, tmp_path, ('svc', 'web', 'docs'))
    run(cli, 'writer', 'start', '--id', 'repair', '--role', 'al-engineer', '--files', 'svc,web')
    (repo/'svc/app.py').write_text('x = 2\n')
    (repo/'web/app.js').write_text('let a = 2\n')
    run(cli, 'writer', 'finish', '--id', 'repair', '--outcome', 'success')
    st = state(state_file)
    result = T.classify_changes(st)
    assert result['task_owned'] == ['svc/app.py', 'web/app.js']
    assert result['unchanged_preexisting'] == ['docs/readme.md']
    (repo/'docs/other.md').write_text('concurrent independent work\n')
    result = T.classify_changes(st)
    assert result['externally_changed'] == ['docs/other.md']
    assert result['discovery_paths'] == ['docs/other.md', 'svc/app.py', 'web/app.js']


def test_overlapping_external_edit_is_ambiguous_even_with_later_admitted_writer(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Repair a module with accurate attribution and independent dependencies.')
    st = state(state_file)
    (repo/'svc/app.py').write_text('x = 9\n')
    contribution(st, ['svc'], lambda: (repo/'svc/app.py').write_text('x = 10\n'))
    result = T.classify_changes(st)
    assert result['ambiguous'] == ['svc/app.py']
    assert not result['task_owned']
    assert result['discovery_paths'] == ['svc/app.py']


def test_after_writer_overlapping_edit_cannot_erase_task_path(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Repair a module with accurate attribution and independent dependencies.')
    st = state(state_file)
    contribution(st, ['svc'], lambda: (repo/'svc/app.py').write_text('x = 2\n'))
    (repo/'svc/app.py').write_text('x = 3\n')
    assert T.classify_changes(st)['ambiguous'] == ['svc/app.py']


def test_modes_deletion_membership_and_index_are_bound(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Track real file modes deletions new members and index identities.')
    st = state(state_file)
    def edit():
        (repo/'svc/app.py').chmod(0o600)
        (repo/'web/app.js').unlink()
        (repo/'svc/new.py').write_text('new = True\n')
        subprocess.run(['git', 'add', 'svc/new.py'], cwd=repo, check=True, capture_output=True)
    contribution(st, ['svc', 'web'], edit)
    result = T.classify_changes(st)
    assert result['task_owned'] == ['svc/app.py', 'svc/new.py', 'web/app.js']
    ref = A.read_json(st['task_attribution']['contributions'][0])['after_capture_ref']
    rows = {r['path']: r for r in A.read_json(ref)['files']}
    assert rows['svc/app.py']['physical_mode'] == 0o600
    assert rows['web/app.js']['worktree']['type'] == 'deleted'
    assert rows['svc/new.py']['index']['sha256']


def test_legacy_run_remains_unattributed_without_snapshot_backfill(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Preserve genuinely legacy issuing runtime attribution without backfill.')
    st = state(state_file)
    del st['task_attribution']
    (repo/'svc/app.py').write_text('x = 2\n')
    before = json.dumps(st, sort_keys=True)
    result = T.classify_changes(st)
    assert not result['attributed'] and result['discovery_paths'] == ['svc/app.py']
    assert T.begin_contribution(st, {'id':'later','files':['svc']}) is None
    assert json.dumps(st, sort_keys=True) == before


def test_foreign_snapshot_and_mutated_artifact_refuse(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Reject altered or foreign immutable task attribution provenance.')
    st = state(state_file)
    st['run_id'] = 'foreign'
    with pytest.raises(SystemExit, match='foreign or corrupt'):
        T.classify_changes(st)
    st = state(state_file)
    ref = st['task_attribution']['snapshot']
    from pathlib import Path
    Path(ref['stored_path']).write_bytes(b'{}')
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        T.classify_changes(st)


def test_reverted_task_change_is_not_still_an_implementation_change(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Classify a reverted implementation contribution honestly.')
    st = state(state_file)
    contribution(st, ['svc'], lambda: (repo/'svc/app.py').write_text('x = 2\n'))
    contribution(st, ['svc'], lambda: (repo/'svc/app.py').write_text('x = 1\n'), 'second')
    assert not T.classify_changes(st)['task_owned']


def test_unstable_membership_cannot_publish_start_snapshot(cli, repo, monkeypatch):
    original = T._rows
    calls = []
    def observe(st):
        rows = original(st)
        calls.append(None)
        if len(calls) == 1:
            (repo/'svc/late.py').write_text('late = True\n')
        return rows
    monkeypatch.setattr(T, '_rows', observe)
    result, _, _ = cli('start', '--spec', 'Refuse unstable start observation without publishing a run.')
    assert 'unstable task attribution observation' in str(result)


def test_overlap_blocks_public_verification_without_changing_state(cli, repo, tmp_path, state_file):
    start_implementation(cli, tmp_path, ('svc',))
    run(cli, 'writer', 'start', '--id', 'first', '--role', 'al-engineer', '--files', 'svc')
    (repo/'svc/app.py').write_text('x = 2\n')
    run(cli, 'writer', 'finish', '--id', 'first', '--outcome', 'success')
    (repo/'svc/app.py').write_text('x = 3\n')
    before = state_file().read_bytes()
    result, _, _ = cli('phase', 'VERIFICATION')
    assert 'unreconciled task attribution overlap: svc/app.py' in str(result)
    assert state_file().read_bytes() == before


def test_disjoint_contributions_do_not_claim_or_block_each_other(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Preserve independently admitted disjoint contributions without false overlap.')
    st = state(state_file)
    left = {'id':'left','files':['svc'],'state':'ACTIVE'}
    right = {'id':'right','files':['web'],'state':'ACTIVE'}
    st['writers'].update(left=left, right=right)
    left_before, right_before = T.begin_contribution(st, left), T.begin_contribution(st, right)
    (repo/'svc/app.py').write_text('x = 2\n')
    (repo/'web/app.js').write_text('let a = 2\n')
    for writer, before in ((left, left_before), (right, right_before)):
        writer.update(state='FINISHED', outcome='success')
        T.finish_contribution(st, writer, before)
    result = T.classify_changes(st)
    assert result['task_owned'] == ['svc/app.py', 'web/app.js']
    assert not result['ambiguous'] and not result['externally_changed']


def test_admission_capture_cannot_be_substituted_between_writers(cli, repo, state_file):
    run(cli, 'start', '--spec', 'Bind every before observation to its exact admitted writer and revision.')
    st = state(state_file)
    writer = {'id':'original','files':['svc'],'outcome':'success'}
    before = T.begin_contribution(st, writer)
    with pytest.raises(SystemExit, match='admission differs'):
        T.finish_contribution(st, dict(writer, id='other'), before)


def test_retained_content_survives_later_edit_and_corruption_refuses(cli, repo, state_file):
    (repo/'docs/readme.md').write_bytes(b'preexisting binary\x00content\n')
    run(cli, 'start', '--spec', 'Retain exact source preimages without exposing their bytes in observation output.')
    st = state(state_file)
    snapshot = A.read_json(st['task_attribution']['snapshot'])
    capture = A.read_json(snapshot['baseline_capture_ref'])
    ref = capture['contents']['docs/readme.md']
    (repo/'docs/readme.md').write_bytes(b'changed after capture')
    assert A.read(ref) == b'preexisting binary\x00content\n'
    Path(ref['stored_path']).write_bytes(b'altered retained preimage')
    with pytest.raises(SystemExit, match='immutable artifact was altered'):
        T.classify_changes(st)


def test_explicit_reconciliation_retains_external_scope_and_allows_next_contribution(cli, repo, tmp_path, state_file):
    from task_attribution_helpers import overlapping, inspection, request_file
    overlapping(cli, repo, tmp_path)
    before = {str(p):p.read_bytes() for p in Path(state_file().parent).rglob('*') if p.is_file()}
    index = (repo/'.git/index').read_bytes()
    view = inspection(cli, repo, state_file)
    assert {str(p):p.read_bytes() for p in Path(state_file().parent).rglob('*') if p.is_file()} == before
    assert (repo/'.git/index').read_bytes() == index
    path = request_file(tmp_path, view, ['svc/app.py'])
    run(cli, 'task', 'reconcile-attribution', '--file', str(path))
    st = state(state_file)
    result = T.classify_changes(st)
    assert not result['ambiguous'] and result['externally_changed'] == ['svc/app.py']
    assert result['discovery_paths'] == ['svc/app.py'] and not result['task_owned']
    assert A.read_json(st['task_attribution']['reconciliations'][0])['source']
    run(cli, 'writer', 'start', '--id', 'next', '--role', 'al-engineer', '--files', 'svc')
    (repo/'svc/app.py').write_text('x = 4\n')
    run(cli, 'writer', 'finish', '--id', 'next', '--outcome', 'success')
    result = T.classify_changes(state(state_file))
    assert result['task_owned'] == ['svc/app.py'] and not result['ambiguous']
    run(cli, 'phase', 'VERIFICATION')
    assert cli('approved')[0] != 0


@pytest.mark.parametrize('damage', ['source', 'mode', 'new-file', 'task', 'paths', 'active-writer', 'missing-preimage'])
def test_reconciliation_never_acknowledges_drift_or_unobserved_scope(cli, repo, tmp_path, state_file, damage):
    from task_attribution_helpers import overlapping, inspection, request_file
    overlapping(cli, repo, tmp_path)
    view = inspection(cli, repo, state_file)
    request = request_file(tmp_path, view, ['svc/app.py'])
    if damage == 'source':(repo/'svc/app.py').write_text('x = 5\n')
    elif damage == 'mode':(repo/'svc/app.py').chmod(0o600)
    elif damage == 'new-file':(repo/'svc/late.py').write_text('late = True\n')
    elif damage == 'task':
        value=json.loads(request.read_bytes());value['binding']['task_hash']='f'*64;request.write_text(json.dumps(value))
    elif damage == 'paths':
        value=json.loads(request.read_bytes());value['paths']=['docs/readme.md'];request.write_text(json.dumps(value))
    elif damage == 'active-writer':
        run(cli, 'writer', 'start', '--id', 'active', '--role', 'al-engineer', '--files', 'svc')
    else:
        st=state(state_file);snapshot=A.read_json(st['task_attribution']['snapshot'])
        capture=A.read_json(snapshot['baseline_capture_ref']);Path(capture['contents']['svc/app.py']['stored_path']).unlink()
    before=state_file().read_bytes()
    result, _, _ = cli('task', 'reconcile-attribution', '--file', str(request))
    assert result != 0 and state_file().read_bytes() == before


def test_real_default_runtime_does_not_capture_its_own_publication(cli_process, repo, monkeypatch):
    from conftest import SCRIPTS
    monkeypatch.delenv('AL_RUNTIME_DIR', raising=False)
    process=cli_process('start', '--spec', 'Capture a real default runtime without recursive self-publication.')
    stdout, stderr=process.communicate(timeout=30)
    assert process.returncode==0, stdout[-1000:]+stderr[-1000:]
    runtime=repo/('.'+SCRIPTS.parents[2].name)/'al-runtime'
    files=list(runtime.glob('*.json'))
    assert len(files)==1
    st=json.loads(files[0].read_bytes())
    snapshot=A.read_json(st['task_attribution']['snapshot'])
    capture=A.read_json(snapshot['baseline_capture_ref'])
    assert all('/al-runtime/' not in r['path'] for r in capture['files'])
    assert (runtime/'.gitignore').read_text()=='*\n'
