"""Internal bounded reassessment never becomes a developer implementation gate."""
import copy

import pytest

from test_rework import fast, load, started
from test_rework_recovery import decision, exhaust


@pytest.mark.parametrize('condition',['pending','declined','cap'])
def test_authorized_work_continues_without_fabricating_rework_credits(cli,state_file,tmp_path,repo,condition):
    started(cli,tmp_path)
    exhaust(cli)
    if condition=='declined':
        path,_=decision(cli,tmp_path,decision='decline',basis='no-useful-next-step')
        assert cli('rework-recovery','decide','--file',str(path))[0]==0
    elif condition=='cap':
        for index in (1,2):
            path,_=decision(cli,tmp_path,request_id='allow-'+str(index))
            assert cli('rework-recovery','decide','--file',str(path))[0]==0
            assert cli('phase','FINALIZATION')[0]==0
            assert cli('rework','--source','finalization',*fast(cli))[0]==0
            assert cli('phase','FINALIZATION')[0]==0
            assert cli('rework','--source','finalization',*fast(cli))[0]==10
    before=load(state_file)
    history=copy.deepcopy(before['rework'])
    bounded=copy.deepcopy(before['rework_recovery'])
    result=cli('writer','start','--id','ordinary-repair','--role','al-engineer','--files','svc/app.py')
    assert result[0]==0,result
    (repo/'svc/app.py').write_text('ordinary_scoped_fix = 42\n')
    assert cli('writer','finish','--id','ordinary-repair','--outcome','success')[0]==0
    current=load(state_file)
    assert current['status']=='RUNNING' and current['phase']=='IMPLEMENTATION'
    assert current['rework']==history and current['rework_recovery']==bounded
    assert current['rework_implementation_continuity']
    assert cli('approved','--reason','Bookkeeping is not evidence')[0]!=0
    assert current.get('approval') is None
