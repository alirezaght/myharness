"""Replay reports must describe actual typed invocations, including unavailable ones."""
import json
import sys
from pathlib import Path
import pytest
import jsonschema
import al_replay as R

PACKAGE=Path(__file__).resolve().parents[1]


def case(actions):return {'id':'typed-argv','provenance':'Owned workflow simulation','actions':actions}


def action(argv):
    # Replayed actions go through the pinned launcher: `alctl run ... -- <arguments>`.
    return len(argv)>2 and argv[1]=='run' and '--' in argv


def report(result):
    data={'schema_version':1,'simulation':True,'cases':[{'candidate':result,'baseline':None,'baseline_comparison':'unavailable','expected_baseline_failure':False,'safety_regression':not result['matched']}],'safety_regressions':int(not result['matched'])}
    jsonschema.validate(data,json.loads((PACKAGE/'reference/schemas/replay-result.json').read_text()))
    return data


def test_executed_outcomes_store_resolved_checkpoint_python_and_file_arguments(monkeypatch):
    calls=[];original=R.invoke
    def capture(root,argv,env):
        if action(argv):calls.append(argv[argv.index('--')+1:])
        return original(root,argv,env)
    monkeypatch.setattr(R,'invoke',capture)
    fixture={'id':'resolved','provenance':'Owned reference simulation','files':{'wait.json':{'reason':'Await input','resume_instructions':'Continue after validation'}},'actions':[
        {'argv':['start','--spec','Track actual resolved action arguments and valid recovery.']},
        {'argv':['waiting','--file','$file:wait.json']},
        {'argv':['resume','--checkpoint',{'$state':'checkpoints.0.id'}]},
        {'argv':['show','--invalid-python-argument','$python'],'expected':'refuse'}]}
    result=R.run_case(PACKAGE,fixture);report(result)
    assert [o['argv'] for o in result['outcomes']]==calls
    assert all(isinstance(v,str) for argv in calls for v in argv)
    assert calls[2][0:2]==['resume','--checkpoint'] and len(calls[2][2])==64
    assert calls[1][2].endswith('/wait.json') and calls[3][-1]==sys.executable


@pytest.mark.parametrize('value',[{'$state':'missing.path'},{'$state':'checkpoints.999.id'}])
def test_unavailable_reference_has_no_invocation_and_identifies_reference(value):
    result=R.run_case(PACKAGE,case([{'argv':['show','--checkpoint',value]}]));report(result);out=result['outcomes'][0]
    assert out['argv']==[] and out['matched'] is False and out['unsupported'] is True
    assert value['$state'] in out['reason'] and 'show' in out['reason'] and 'exit' not in out


@pytest.mark.parametrize('value',[42,False,{'unsupported':'object'},['show',42],['show',None]])
def test_nonstring_arguments_are_not_stringified_or_invoked(value):
    result=R.run_case(PACKAGE,case([{'argv':value}]));report(result);out=result['outcomes'][0]
    assert out['argv']==[] and out['unsupported'] is True and out['matched'] is False
    assert 'list of strings' in out['reason'] and 'exit' not in out


def test_invocation_error_keeps_resolved_strings(monkeypatch):
    original=R.invoke
    def fail(root,argv,env):
        if action(argv):raise OSError('Owned invocation unavailable')
        return original(root,argv,env)
    monkeypatch.setattr(R,'invoke',fail)
    result=R.run_case(PACKAGE,case([{'argv':['show','$python','$file:note.txt']}]));report(result);out=result['outcomes'][0]
    assert out['argv'][0:2]==['show',sys.executable] and out['argv'][2].endswith('/note.txt')
    assert out['matched'] is False and out['unsupported'] is True and 'Owned invocation' in out['error']
