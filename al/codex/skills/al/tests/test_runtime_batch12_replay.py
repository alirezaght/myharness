"""Replay results must represent the selected guard's actual declared checks."""
import pytest
import al_replay as R
import al_replay_policy as P
from batch12_replay_helpers import PACKAGE,SEARCH,fixture,selected,reject,run,policy_rows


def test_unchanged_harmless_fixture_accounts_for_fourteen_policies_and_two_actions(tmp_path):
    code,report=run(tmp_path,PACKAGE)
    assert code==0 and report['safety_regressions']==0
    case=report['cases'][0]['candidate'];assert case['matched'] and not case['unavailable']
    assert case['counts']=={'action':{'total':2,'matched':2,'failed':0,'unavailable':0},'policy':{'total':14,'matched':14,'failed':0,'unavailable':0}}
    rows=policy_rows(report);assert len(rows)==14 and all(r['expected']=='allow' and r['violations']==[] and r['matched'] for r in rows)
    assert {r['module_origin'] for r in rows}=={str(PACKAGE/'scripts/al_shell.py')}


def test_selected_regression_causes_specific_policy_failure_nonzero_and_safety_regression(tmp_path):
    candidate=selected(tmp_path,'candidate',reject())
    code,report=run(tmp_path,candidate)
    assert code!=0 and report['safety_regressions']==1
    case=report['cases'][0]['candidate'];assert not case['matched'] and not case['unavailable']
    rows=policy_rows(report);assert len(rows)==14
    failures=[r for r in rows if not r['matched']]
    assert len(failures)==2 and {r['policy'] for r in failures}=={'readonly','shared'}
    assert all(r['command']==SEARCH and r['violations']==['selected guard regression'] and not r['unsupported'] for r in failures)
    assert case['counts']['policy']=={'total':14,'matched':12,'failed':2,'unavailable':0}
    assert case['counts']['action']['matched']==2


def test_selected_candidate_and_baseline_are_independent_of_imported_runner_guard(tmp_path):
    baseline=selected(tmp_path,'baseline',reject());candidate=selected(tmp_path,'candidate')
    case=fixture();case['expected_baseline_failure']=False
    code,report=run(tmp_path,candidate,case,baseline)
    row=report['cases'][0];assert code==0 and row['candidate']['matched'] and not row['baseline']['matched']
    assert row['baseline_comparison']=='available' and row['expected_baseline_failure'] is False
    assert row['baseline']['counts']['policy']['failed']==2
    # In-process runner already imported its guard; selected subprocess must still differ.
    assert not R.run_case(baseline,case)['matched']


def test_missing_baseline_interface_is_unavailable_never_candidate_fallback(tmp_path):
    baseline=selected(tmp_path,'baseline',missing=True)
    code,report=run(tmp_path,PACKAGE,baseline=baseline)
    row=report['cases'][0];assert code==0 and row['candidate']['matched']
    assert row['baseline_comparison']=='unavailable' and row['baseline']['unavailable'] and not row['baseline']['matched']
    rows=[r for r in row['baseline']['outcomes'] if r['kind']=='policy']
    assert len(rows)==14 and all(not r['matched'] and r['unsupported'] and 'missing' in r['error'] for r in rows)


@pytest.mark.parametrize('commands',[None,{},[],{'unknown':['rg text']},{'shared':'rg text'},{'readonly':[]},{'shared':['']},{'shared':[None]},{'shared':[4]}])
def test_malformed_declarations_produce_explicit_unmatched_policy_outcomes(tmp_path,commands):
    case=fixture();case['commands']=commands
    code,report=run(tmp_path,PACKAGE,case)
    assert code!=0 and report['safety_regressions']==1
    rows=policy_rows(report);assert rows and all(not r['matched'] and r['unsupported'] and r['error'] for r in rows)


@pytest.mark.parametrize('append,reason',[
 ("def check(*args,**kwargs):raise RuntimeError('selected exception')",'selected exception'),
 ("def check(*args,**kwargs):return None",'malformed'),
 ("def check(*args,**kwargs):return 'not a list'",'malformed'),
 ("def check(*args,**kwargs):return [3]",'malformed'),
 ("del check",'interface missing'),
 ("__file__='/wrong/al_shell.py'",'origin mismatch'),
 ("def check(*args,**kwargs):\n import os\n os.write(1,b'invalid report')\n return []",'Expecting value'),
])
def test_selected_policy_errors_and_invalid_reports_never_match(tmp_path,append,reason):
    candidate=selected(tmp_path,'candidate',append);case=fixture();case['commands']={'shared':[SEARCH]}
    code,report=run(tmp_path,candidate,case)
    assert code!=0 and report['safety_regressions']==1
    row=policy_rows(report)[0];assert not row['matched'] and row['unsupported'] and reason in row['error']


def test_timeout_is_unmatched_and_unavailable_with_actual_policy_process(tmp_path,monkeypatch):
    candidate=selected(tmp_path,'candidate','def check(*args,**kwargs):\n import time\n time.sleep(2)\n return []')
    case=fixture();case['commands']={'shared':[SEARCH]};monkeypatch.setattr(P,'TIMEOUT',.1)
    result=R.run_case(candidate,case);rows=[r for r in result['outcomes'] if r['kind']=='policy']
    assert not result['matched'] and result['unavailable'] and rows[0]['unsupported'] and 'timed out' in rows[0]['error']


def test_command_text_is_never_executed_and_action_only_fixture_still_works(tmp_path):
    marker=tmp_path/'never-executed';case=fixture();case['commands']={'shared':['touch '+str(marker)]}
    code,report=run(tmp_path,PACKAGE,case)
    assert code!=0 and report['safety_regressions']==1 and not marker.exists()
    assert policy_rows(report)[0]['violations']
    del case['commands'];code,report=run(tmp_path,PACKAGE,case)
    assert code==0 and report['cases'][0]['candidate']['counts']['policy']['total']==0
