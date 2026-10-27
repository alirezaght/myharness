"""Standalone AL consumes the same source-backed residual-risk rubric."""
import hashlib
import json
from pathlib import Path
import pytest
import al_task_risk as Risk


def value(repo):
    return {'schema_version':1,'scope':'local','existing_pattern':True,'behavior_clear':True,
            'hard_parts':[],'pseudocode_benefit':'',
            'evidence':[{'path':'svc/app.py','sha256':hashlib.sha256((repo/'svc/app.py').read_bytes()).hexdigest(),
                         'reason':'Existing function is the complete scoped implementation seam.'}],
            'reason':'A local established-pattern change has no new coupled design problem.'}


def test_standalone_routing_refuses_keyword_inflation_and_preserves_state(cli,repo,tmp_path,state_file):
    assert cli('start','--spec','Fix a local retry parameter within the existing distributed state machine.')[0]==0
    file=tmp_path/'risk.json';file.write_text(json.dumps(value(repo)))
    before=state_file().read_bytes()
    assert cli('classify','COMPLEX','--designer','yes','--assessment-file',str(file))[0]!=0
    assert state_file().read_bytes()==before
    result=cli('classify','SMALL','--designer','no','--implementers','al-engineer','--assessment-file',str(file))
    assert result[0]==0,result
    state=json.loads(state_file().read_bytes())
    assert state['classification']=='SMALL' and state['classification_assessment']==value(repo)


def test_shared_rubric_bytes_are_identical():
    root=Path(__file__).resolve().parents[5]
    assert Path(Risk.__file__).read_bytes()==(root/'harness/task_risk.py').read_bytes()
