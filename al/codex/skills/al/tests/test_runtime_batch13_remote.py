"""Malformed remote data must finalize public reservations rather than escape parsing."""
import json
import pytest
from hook_regression_helpers import invoke
from batch13_helpers import start,manifest,stub,body,finished

BAD=[
 ((),None),((),[]),((),False),((),3),((),'text'),
 (('pipeline',),None),(('pipeline',),[]),(('pipeline',),'missing'),
 (('commit',),None),(('commit',),[]),(('commit',),'missing'),
 (('id',),True),(('id',),0),(('id',),-1),(('id',),'9'),(('id',),1.5),(('id',),'missing'),
 (('pipeline','id'),False),(('pipeline','id'),0),(('pipeline','id'),'11'),(('pipeline','id'),'missing'),
 (('name',),[]),(('name',),''),(('name',),'   '),(('name',),'missing'),
 (('commit','id'),{}),(('commit','id'),''),(('commit','id'),'missing'),
 (('status',),False),(('status',),''),(('status',),'missing'),
 (('pipeline','project_id'),[]),(('pipeline','project_id'),{}),(('pipeline','project_id'),True),(('pipeline','project_id'),0),(('pipeline','project_id'),''),
 (('web_url',),[]),(('web_url',),False),
]


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('path,value',BAD)
def test_malformed_shapes_finalize_infra_with_intact_raw_log(cli,repo,tmp_path,monkeypatch,stage,path,value):
    data=body(start(cli,repo))
    if not path:data=value
    else:
        target=data
        for part in path[:-1]:target=target[part]
        if value=='missing':target.pop(path[-1])
        else:target[path[-1]]=value
    raw=json.dumps(data).encode();stub(tmp_path,monkeypatch,raw)
    manifest(cli,tmp_path,['glab','api','projects/42/jobs/9'],category='ci',remote=True)
    _,out,err=invoke(cli,'gates','run','--stage',stage,'--id','boundary',ok=False)
    entry,log=finished(cli,'INFRA');assert entry['exit']!=0 and raw in log
    assert 'invalid remote job response' in entry['remote_provenance']['reason'] and 'Traceback' not in out+err


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('raw',[b'not json',b'\xff\xfe\x80',b'{'])
def test_invalid_encoding_and_json_finalize_infra(cli,repo,tmp_path,monkeypatch,stage,raw):
    start(cli,repo);stub(tmp_path,monkeypatch,raw)
    manifest(cli,tmp_path,['glab','api','projects/42/jobs/9'],category='ci',remote=True)
    invoke(cli,'gates','run','--stage',stage,ok=False);entry,log=finished(cli,'INFRA')
    assert entry['exit']==126 and raw in log


@pytest.mark.parametrize('stage',['base','candidate'])
@pytest.mark.parametrize('change,expected',[
 ({},'PASS'),({'status':'failed'},'FAIL'),({'status':'canceled'},'FAIL'),
 ({'name':'other'},'INFRA'),({'commit':{'id':'wrong'}},'INFRA'),
 ({'pipeline':{'id':11,'project_id':43}},'INFRA'),
 ({'pipeline':{'id':11}},'PASS'),({'pipeline':{'id':11,'project_id':None}},'PASS'),
 ({'pipeline':{'id':11,'project_id':'42'}},'INFRA'),({'web_url':'https://example.invalid/job'},'PASS'),
])
def test_valid_remote_success_failure_and_provenance_controls(cli,repo,tmp_path,monkeypatch,stage,change,expected):
    data=body(start(cli,repo));data.update(change);stub(tmp_path,monkeypatch,json.dumps(data).encode())
    manifest(cli,tmp_path,['glab','api','projects/42/jobs/9'],category='ci',remote=True)
    invoke(cli,'gates','run','--stage',stage,ok=expected=='PASS');entry,_=finished(cli,expected)
    assert entry['remote_provenance']['pipeline_id']==11 and entry['remote_provenance']['status']==data['status']


@pytest.mark.parametrize('stage',['base','candidate'])
def test_invalid_utf8_inside_otherwise_valid_job_is_not_lossily_accepted(cli,repo,tmp_path,monkeypatch,stage):
    data=body(start(cli,repo));data['web_url']='https://bad'
    raw=json.dumps(data).encode().replace(b'https://bad',b'https://\xff')
    stub(tmp_path,monkeypatch,raw);manifest(cli,tmp_path,['glab','api','projects/42/jobs/9'],category='ci',remote=True)
    invoke(cli,'gates','run','--stage',stage,ok=False);entry,log=finished(cli,'INFRA')
    assert entry['exit']==126 and raw in log


@pytest.mark.parametrize('path,value',BAD)
def test_schema_rejects_malformed_fields(path,value):
    import al_remote_gates as R
    data={'id':9,'pipeline':{'id':11,'project_id':42},'name':'verify','commit':{'id':'abc'},'status':'success'}
    if not path:data=value
    else:
        target=data
        for part in path[:-1]:target=target[part]
        if value=='missing':target.pop(path[-1])
        else:target[path[-1]]=value
    with pytest.raises(ValueError):R.response(json.dumps(data).encode())
