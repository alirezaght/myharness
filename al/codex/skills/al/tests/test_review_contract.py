"""Actual issued modern review artifacts match the published contracts."""
import copy
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from evidence_helpers import start_implementation, plan_and_run_broad, complete_review


def _contract_validator(name):
    root=Path(__file__).resolve().parents[1]/'reference/schemas'
    registry=Registry().with_resources((p.name,Resource.from_contents(json.loads(p.read_text()))) for p in root.glob("*.json"))
    return Draft202012Validator(json.loads((root/(name+'.json')).read_text()),registry=registry)


def test_real_v2_fresh_and_comparison_schema_runtime_roundtrip(cli, tmp_path, state_file, run_state):
    run_state("implemented")
    assert plan_and_run_broad(cli)==0
    assert cli("phase","VERIFICATION")[0]==0
    artifact_file=tmp_path/'review-artifact.svg'
    artifact_file.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>')
    code,out,err=cli('artifact','record','--file',str(artifact_file),'--kind','review-fixture')
    assert code==0,(out,err)
    artifact=json.loads(out)
    def inspect(report):
        report['criteria'][0]['rationale']='Completed review: the literal marker "FILL:" is quoted as protocol documentation.'
        report['criteria'][0]['locations']=['svc/app.py','svc/app.py:1',{'artifact':artifact,'explanation':'Immutable protocol fixture documents the literal FILL: marker; no live browser or provider observation claimed.'}]
        if report['stage']=='compare':
            report['assessments']={'rollout':{'applicable':True,'reason':'Exercise applicable assessment protocol.',**{name:{'status':'EVIDENCED','reason':'Immutable fixture exercises reference validation.','artifacts':[artifact]} for name in ('expand_contract','deployment_order','aggregator_environment','consumer_deployment','dlq_redrive')}},
                'ui':{'applicable':True,'reason':'Deterministic protocol fixture, no live rendering claim.','browser':'fixture','viewport':'10x10','functional_checks':['Reference protocol exercised.'],'alignment_observations':['Static fixture has a bounded rectangle.'],'ux_observations':['No live interaction performed.'],'artifacts':[artifact]}}
        variants=[]
        for bad_ref in [artifact['stored_path'],{'stored_path':artifact['stored_path']},dict(artifact,id='0'*64,sha256='0'*64)]:
            bad=copy.deepcopy(report);bad['criteria'][0]['locations']=[{'artifact':bad_ref,'explanation':'Invalid reference must refuse.'}];variants.append(bad)
        for location in ['svc/app.py:1-2','svc/app.py:0','svc/app.py:99999','missing-review-file.py']:
            bad=copy.deepcopy(report);bad['criteria'][0]['locations']=[location];variants.append(bad)
        bad=copy.deepcopy(report);bad['invariants']=[{'id':'invented-invariant','locations':['svc/app.py:1']}];variants.append(bad)
        if report['stage']=='compare':
            for collection in [artifact,artifact['stored_path'],[artifact['stored_path']],[{'sha256':artifact['sha256']}],[dict(artifact,id='0'*64,sha256='0'*64)],[]]:
                for surface in ('rollout','ui'):
                    bad=copy.deepcopy(report)
                    target=bad['assessments'][surface]['expand_contract'] if surface=='rollout' else bad['assessments'][surface]
                    target['artifacts']=collection;variants.append(bad)
            for field in ('browser','viewport','functional_checks','alignment_observations','ux_observations','artifacts'):
                bad=copy.deepcopy(report);del bad['assessments']['ui'][field];variants.append(bad)
            for dimension in ('expand_contract','deployment_order','aggregator_environment','consumer_deployment','dlq_redrive'):
                bad=copy.deepcopy(report);del bad['assessments']['rollout'][dimension];variants.append(bad)
        for key,value in [('schema_version',1),('schema_version',True),('extra_closed_field',None),('attempt_id','foreign-attempt'),('verification_authority_hash','0'*64)]:
            value_report=copy.deepcopy(report);value_report[key]=value;variants.append(value_report)
        missing=copy.deepcopy(report);missing.pop('document_hash');variants.append(missing)
        bad=copy.deepcopy(report);bad['criteria'][0]['evidence']=['E99999'];variants.append(bad)
        if report['stage']=='fresh':
            for prior in [None,[]]:
                bad=copy.deepcopy(report);bad['prior_resolutions']=prior;variants.append(bad)
            bad=copy.deepcopy(report);bad['findings']=[{'id':'F-null','source':report['criteria'][0]['id'],'severity':'NON_BLOCKING','description':'Forbidden fresh resolution key','resolution':None}];variants.append(bad)
        else:
            bad=copy.deepcopy(report);bad['counterexample_search']=[];variants.append(bad)
        for value in variants:
            path=tmp_path/'invalid-report.json';path.write_text(json.dumps(value))
            before=state_file().read_bytes()
            artifacts=Path(artifact['stored_path']).parent
            retained={p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}
            code,out,err=getattr(cli,'raw',cli)('review-fresh' if report['stage']=='fresh' else 'review-result','--file',str(path))
            assert code!=0,(out,err,value)
            assert state_file().read_bytes()==before
            assert {p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}==retained
    complete_review(cli,tmp_path,blocking=False,inspect_report=inspect)
    st=json.loads(state_file().read_bytes())
    errors=[]
    for stage,field in [('fresh','fresh'),('compare','comparison')]:
        packet=json.loads(Path(st['review']['packets'][stage]['stored_path']).read_bytes())
        report=json.loads(Path(st['review'][field]['stored_path']).read_bytes())
        assert packet['schema_version']==report['schema_version']==2
        assert 'literal marker "FILL:"' in report['criteria'][0]['rationale']
        assert 'literal FILL:' in report['criteria'][0]['locations'][-1]['explanation']
        assert set(packet['report_authoring_guidance'])=={'locations','artifact_references','rollout','ui','invariants','counterexamples','placeholders'}
        if stage=='fresh':
            assert packet['reserved_finding_ids']==[]
            assert not {'prior_findings','fresh','approved_inputs','comparison_contents'} & set(packet)
        for name,value in [('review-packet',packet),('architect-report',report)]:
            validator=_contract_validator(name)
            errors.extend(stage+' '+name+': '+e.message[:300] for e in validator.iter_errors(value))
    assert not errors, '\n'.join(errors)


def test_modern_wrong_version_diagnostic_precedes_closed_shape():
    import al_review_behavior as B
    with pytest.raises(SystemExit,match="requires schema_version 2"):
        B.validate_report({'proof_admission':{}},{'schema_version':1},'fresh',{})


def test_reserved_finding_ids_packet_schema_requires_identity_only_unique_strings():
    root=Path(__file__).resolve().parents[1]/'reference/schemas'
    schema=json.loads((root/'review-packet.json').read_text())
    field=schema['properties']['reserved_finding_ids']
    validator=Draft202012Validator(field)
    assert not list(validator.iter_errors(['R1-F1','R2-F1']))
    for value in [[{'id':'R1-F1','source':'AC-01'}],['R1-F1','R1-F1'],[''],[None]]:
        assert list(validator.iter_errors(value)),value


def test_actual_fresh_counterexample_requires_selected_criterion_blocker(cli,tmp_path,state_file,run_state):
    from evidence_helpers import _review_report
    import al_review as R
    run_state('verified')
    assert cli('review-start')[0]==0
    assert cli('review-packet','--stage','fresh')[0]==0
    state=json.loads(state_file().read_bytes());issued=state['review']['packets']['fresh']
    assert json.loads(Path(state['proof_admission']['authority']['stored_path']).read_bytes())['invariants']==[]
    artifacts=Path(issued['stored_path']).parent
    valid=_review_report(cli,'fresh','criterion-counterexample',False)
    valid['counterexample_search'][0]['result']='found'
    valid['findings']=[{'id':'R1-counterexample','source':valid['criteria'][0]['id'],'severity':'BLOCKING','description':'A concrete criterion-linked protocol counterexample.'}]
    valid['status']='CHANGES_REQUIRED'
    variants=[]
    for status in ('found','blocked'):
        for source in (None,'invented-invariant'):
            bad=copy.deepcopy(valid);bad['counterexample_search'][0]['result']=status
            if source is None:bad['findings']=[]
            else:bad['findings'][0]['source']=source
            variants.append(bad)
        bad=copy.deepcopy(valid);bad['counterexample_search'][0]['result']=status;bad['status']='APPROVED';variants.append(bad)
    bad=copy.deepcopy(valid);bad['invariants']=[{'id':'invented-invariant','locations':['svc/app.py']}];variants.append(bad)
    for report in variants:
        path=tmp_path/'bad-counterexample.json';path.write_text(json.dumps(report));before=state_file().read_bytes()
        retained={p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}
        code,out,err=getattr(cli,'raw',cli)('review-fresh','--file',str(path));assert code!=0,(out,err)
        assert state_file().read_bytes()==before
        assert {p.name:p.read_bytes() for p in artifacts.iterdir() if p.is_file()}==retained
    # Read-only validation uses the genuine issued state for both supported blocking results.
    for result in ('found','blocked'):
        report=copy.deepcopy(valid);report['counterexample_search'][0]['result']=result
        assert R.validate_report(state,report,'fresh')==(1,0)
    path=tmp_path/'valid-counterexample.json';path.write_text(json.dumps(valid))
    assert getattr(cli,'raw',cli)('review-fresh','--file',str(path))[0]==0
    saved=json.loads(state_file().read_bytes())['review']
    assert saved['stage']=='fresh-saved' and saved['packets']['fresh']==issued
    assert json.loads(Path(saved['fresh']['stored_path']).read_bytes())==valid
    assert not list(_contract_validator('architect-report').iter_errors(valid))
