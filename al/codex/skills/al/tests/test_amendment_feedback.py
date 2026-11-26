"""Amendments explain invalidation; repeated routing never invents new authority."""
import json


def ok(cli, *args):
    result = cli(*args)
    assert result[0] == 0, result
    return result


def test_amendment_reports_gate_reset_and_classification_is_idempotent(cli, tmp_path, state_file):
    ok(cli, 'start', '--spec', 'Clarify the local implementation while retaining approved routing history.')
    ok(cli, 'spec-gate', 'SPEC_READY')
    route = ('classify', 'STANDARD', '--designer', 'no', '--implementers', 'al-engineer')
    ok(cli, *route, '--reason', 'Existing implementation pattern is sufficient.')
    for number in (1, 2):
        path = tmp_path / f'amend-{number}.json'
        path.write_text(json.dumps({'id': f'AM-{number}', 'source': 'explicit user clarification',
                                    'text': f'Retain the existing behavior and clarify acceptance detail number {number}.'}))
        result = ok(cli, 'task', 'amend', '--file', str(path))
        assert 'Specification gate reset' in result[1]
        assert 'spec-gate SPEC_READY' in result[1]
        before = state_file().read_bytes()
        amended = json.loads(before)
        assert amended['spec_status'] is None
        assert amended['classification'] == 'STANDARD'
        assert amended['proof_admission']['status'] == 'pending'
        result = ok(cli, *route)
        assert 'classification unchanged' in result[1]
        assert 'Specification gate pending' in result[1]
        assert state_file().read_bytes() == before
        ok(cli, 'spec-gate', 'SPEC_READY')


def test_identical_routing_after_planning_is_a_readonly_noop(cli, tmp_path, state_file):
    from evidence_helpers import plan_na_gates

    ok(cli, 'start', '--spec', 'Implement a small scoped change using the existing service behavior.')
    ok(cli, 'spec-gate', 'SPEC_READY')
    route = ('classify', 'SMALL', '--designer', 'no', '--implementers', 'al-engineer')
    ok(cli, *route)
    plan_na_gates(cli, tmp_path, ('svc/app.py',))
    ok(cli, 'phase', 'IMPLEMENTATION')
    before = state_file().read_bytes()
    assert 'classification unchanged' in ok(cli, *route)[1]
    assert state_file().read_bytes() == before
    result = cli('classify', 'COMPLEX', '--designer', 'yes', '--implementers', 'al-engineer', '--hard-part', 'invariant: amendments keep earlier feedback')
    assert result[0] != 0 and 'only change during planning' in ''.join(map(str, result))
    assert state_file().read_bytes() == before


def test_invalid_additive_amendments_refuse_before_publication(cli,tmp_path,state_file,run_state):
    from pathlib import Path
    import al_proof_json as J
    run_state('implemented')
    state=json.loads(state_file().read_bytes())
    original=J.read(state['task_package'])['criteria'][0]
    path=tmp_path/'invalid-addition.json'
    def request(**fields):
        return dict(id='AM-invalid',source='explicit user clarification',
                    text='Retain all accepted behavior and add an exact additional acceptance obligation.',**fields)
    defects=[]
    for field in ('criteria','supplemental_acceptance'):
        for value in (None,'AC-new',{},[None],[''],['   '],['AC-new','AC-new']):
            defects.append((request(**{field:value}),'amendment '+field))
        defects.append((request(**{field:[original]}),'duplicate accepted criterion'))
    defects.append((request(criteria=['same'],supplemental_acceptance=['same']),'duplicate accepted criterion'))
    artifacts=Path(state['task_package']['stored_path']).parent
    for data,diagnostic in defects:
        path.write_text(json.dumps(data));before=state_file().read_bytes()
        retained={p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}
        code,out,err=cli('task','amend','--file',str(path))
        assert code!=0 and diagnostic in str(code)+out+err,(data,code,out,err)
        assert state_file().read_bytes()==before
        assert {p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}==retained
    accepted=request(criteria=['previously accepted addition']);accepted['id']='AM-first'
    path.write_text(json.dumps(accepted));ok(cli,'task','amend','--file',str(path))
    for field in ('criteria','supplemental_acceptance'):
        path.write_text(json.dumps(request(**{field:accepted['criteria']})))
        before=state_file().read_bytes();retained={p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}
        code,out,err=cli('task','amend','--file',str(path))
        assert code!=0 and 'duplicate accepted criterion' in str(code)+out+err
        assert state_file().read_bytes()==before
        assert {p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}==retained


def test_raw_amendment_json_refuses_before_state_or_artifact_publication(cli,tmp_path,state_file,run_state):
    from pathlib import Path
    run_state('implemented')
    state=json.loads(state_file().read_bytes())
    artifacts=Path(state['task_package']['stored_path']).parent
    path=tmp_path/'raw-amendment.json'
    prefix=b'{"id":"AM-raw","source":"explicit user clarification","text":"Preserve accepted behavior and add one exact additional criterion."'
    cases=[('duplicate-key',prefix+b',"criteria":["first"],"criteria":["second"]}', 'duplicate proof JSON key: criteria')]
    for token in (b'NaN',b'Infinity',b'-Infinity'):
        cases.append(('nonfinite-'+token.decode(),prefix+b',"criteria":["additional"],"metadata":'+token+b'}','nonfinite proof JSON'))
    cases.append(('oversize',prefix+b',"criteria":["additional"],"padding":"'+b'x'*(16*1024*1024)+b'"}', 'proof JSON exceeds bounded input size'))
    for label,raw,diagnostic in cases:
        path.write_bytes(raw);before=state_file().read_bytes()
        retained={p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}
        code,out,err=cli('task','amend','--file',str(path))
        assert code!=0 and diagnostic in str(code)+out+err,(label,code,out,err)
        assert state_file().read_bytes()==before,label
        assert {p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}==retained,label
