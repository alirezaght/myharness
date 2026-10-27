"""Actual issued modern review artifacts match the published contracts."""
import copy
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from evidence_helpers import start_implementation, plan_and_run_broad, complete_review


def test_real_v2_fresh_and_comparison_schema_runtime_roundtrip(cli,tmp_path,state_file):
    start_implementation(cli,tmp_path)
    assert plan_and_run_broad(cli)==0
    assert cli("phase","VERIFICATION")[0]==0
    def inspect(report):
        variants=[]
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
            code,out,err=getattr(cli,'raw',cli)('review-fresh' if report['stage']=='fresh' else 'review-result','--file',str(path))
            assert code!=0,(out,err,value)
            assert state_file().read_bytes()==before
    complete_review(cli,tmp_path,blocking=False,inspect_report=inspect)
    st=json.loads(state_file().read_bytes())
    root=Path(__file__).resolve().parents[1]/'reference/schemas'
    registry=Registry().with_resources((p.name,Resource.from_contents(json.loads(p.read_text()))) for p in root.glob("*.json"))
    errors=[]
    for stage,field in [('fresh','fresh'),('compare','comparison')]:
        packet=json.loads(Path(st['review']['packets'][stage]['stored_path']).read_bytes())
        report=json.loads(Path(st['review'][field]['stored_path']).read_bytes())
        assert packet['schema_version']==report['schema_version']==2
        for name,value in [('review-packet',packet),('architect-report',report)]:
            validator=Draft202012Validator(json.loads((root/(name+'.json')).read_text()),registry=registry)
            errors.extend(stage+' '+name+': '+e.message[:300] for e in validator.iter_errors(value))
    assert not errors, '\n'.join(errors)


def test_modern_wrong_version_diagnostic_precedes_closed_shape():
    import al_review_behavior as B
    with pytest.raises(SystemExit,match="requires schema_version 2"):
        B.validate_report({'proof_admission':{}},{'schema_version':1},'fresh',{})
