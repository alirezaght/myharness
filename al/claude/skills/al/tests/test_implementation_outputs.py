"""A small task authors before planning proof; every status mode preserves that truth."""
import json


def test_small_authors_with_pending_proof_and_renders_without_mutating(cli, repo, state_file):
    for args in (
        ('start', '--spec', 'Apply a local established-pattern change with focused tests and review.'),
        ('spec-gate', 'SPEC_READY'),
        ('classify', 'SMALL', '--designer', 'no', '--implementers', 'al-builder'),
        ('phase', 'IMPLEMENTATION'),
        ('writer', 'start', '--id', 'local-fix', '--role', 'al-builder', '--files', 'svc/app.py'),
    ):
        result=cli(*args)
        assert result[0]==0,(args,result)
    (repo/'svc/app.py').write_text('x = 2\n')
    assert cli('writer','finish','--id','local-fix','--outcome','success')[0]==0
    before=state_file().read_bytes()
    raw=cli('show');line=cli('show','--line');panel=cli('show','--panel')
    assert all(result[0]==0 for result in (raw,line,panel))
    state=json.loads(raw[1])
    assert state['status']=='RUNNING' and state['phase']=='IMPLEMENTATION'
    assert state['proof_admission']['status']=='pending'
    assert state['implementation_readiness']['obligations']
    assert 'verification pending' in line[1].lower()
    assert 'Verification: pending' in panel[1]
    assert 'scoped work may continue' in panel[1]
    assert state_file().read_bytes()==before
    assert cli('approved')[0]!=0
    assert json.loads(state_file().read_bytes())['status']!='APPROVED'
