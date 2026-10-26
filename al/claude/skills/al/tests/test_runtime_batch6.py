"""Final-batch safety proofs and complete successful public routes."""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
import al_artifacts as A
import al_gates as G
import al_ledger as L
import al_review_history as H
import al_shell
import al_traceability as T
from hook_regression_helpers import invoke,begin,report_data,finish_review,save_report
from batch5_helpers import state,comparison,second_round
from batch6_helpers import ruled,pause,replanned_approval,gated,conflict


@pytest.mark.parametrize('route',['resume','retry'])
def test_changed_rules_replan_then_complete_fresh_approval(cli,repo,tmp_path,state_file,route):
    ruled(cli,tmp_path,repo);old=state(cli)['gate_manifests'][-1]
    if route=='resume':checkpoint=pause(cli,tmp_path)
    else:invoke(cli,'agent-failed','al-engineer','--reason','transient','--retry-safe','yes')
    # A new rule source outside the planned paths needs the Architect's replacement plan.
    (repo/'AGENTS.md').write_text('Revised repository instructions.\n')
    if route=='resume':invoke(cli,'resume','--checkpoint',checkpoint)
    else:invoke(cli,'retry','al-engineer','--reason','reconcile changed rules')
    s=state(cli);assert s['recovery_replans'][-1]['superseded']==old and old in s['gate_manifests']
    assert s['evidence']['invalidated_at'] and not s['phase_occurrences'][-1]['completed']
    replanned_approval(cli,tmp_path,repo);assert state(cli)['status']=='APPROVED'


def test_new_unplanned_units_require_planning(cli,repo,tmp_path):
    ruled(cli,tmp_path,repo);checkpoint=pause(cli,tmp_path)
    (repo/'apps/new').mkdir(parents=True);(repo/'apps/new/a.ts').write_text('server data')
    invoke(cli,'resume','--checkpoint',checkpoint)
    assert state(cli)['phase']=='PLANNING' and state(cli)['recovery_replans'][-1]['reasons']


def test_removed_planned_rule_refreshes_without_replanning(cli,repo,tmp_path):
    ruled(cli,tmp_path,repo);checkpoint=pause(cli,tmp_path)
    (repo/'Makefile').unlink()
    invoke(cli,'resume','--checkpoint',checkpoint)
    s=state(cli)
    assert 'source rule missing: Makefile' in s['gate_refreshes'][-1]['reasons'] and not s.get('recovery_replans')


@pytest.mark.parametrize('artifact',['manifest','checkpoint'])
def test_corrupt_recovery_artifact_refuses_without_state_write(cli,repo,tmp_path,state_file,artifact):
    ruled(cli,tmp_path,repo);checkpoint=pause(cli,tmp_path);s=state(cli)
    ref=s['gate_manifests'][-1] if artifact=='manifest' else s['checkpoints'][-1]
    Path(ref['stored_path']).write_bytes(b'corrupt artifact')
    (repo/'Makefile').write_text('changed rule\n');before=state_file().read_bytes()
    invoke(cli,'resume','--checkpoint',checkpoint,ok=False);assert state_file().read_bytes()==before


def test_manifest_corrupt_metadata_is_not_ordinary_rule_drift(cli,repo,tmp_path,state_file):
    ruled(cli,tmp_path,repo);checkpoint=pause(cli,tmp_path);s=state(cli)
    data=A.read_json(s['gate_manifests'][-1]);data['rules'][0]['sha256']='not-a-hash'
    s['gate_manifests'][-1]=A.snapshot_bytes(s['session_id'],json.dumps(data).encode(),'gate-manifest','corrupt metadata')
    state_file().write_text(json.dumps(s));(repo/'Makefile').write_text('changed\n');before=state_file().read_bytes()
    invoke(cli,'resume','--checkpoint',checkpoint,ok=False);assert state_file().read_bytes()==before


@pytest.mark.parametrize('defect',['log','prerequisite','execution'])
def test_candidate_gate_requires_full_ledger_and_execution_identity(cli,repo,tmp_path,state_file,defect):
    gated(cli,tmp_path,repo);s=state(cli)
    entry=next(e for e in reversed(s['evidence']['entries']) if e['area']=='gate-candidate-loc')
    if defect=='log':Path(entry['log']).write_text('altered log')
    elif defect=='prerequisite':entry['needs']=['not-verified'];state_file().write_text(json.dumps(s))
    else:entry['gate_stage']='base';state_file().write_text(json.dumps(s))
    before=state_file().read_bytes();assert G.candidate_gaps(state(cli))
    invoke(cli,'review-start',ok=False);assert state_file().read_bytes()==before
    invoke(cli,'gates','run','--stage','candidate','--id','loc');finish_review(cli,tmp_path)
    invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved');assert state(cli)['status']=='APPROVED'


@pytest.mark.parametrize('linked',['absent','fixed'])
def test_unsatisfied_criterion_requires_unfixed_stable_finding(cli,repo,tmp_path,state_file,linked):
    begin(cli,tmp_path,repo);invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    data=report_data(cli,'fresh','unsatisfied');data['criteria'][0]['status']='UNSATISFIED';data['status']='CHANGES_REQUIRED'
    if linked=='fixed':data['findings']=[{'id':'F-unsat','source':'AC-01','severity':'BLOCKING','description':'Unresolved criterion',
        'resolution':{'status':'fixed','rationale':'claimed','locations':['svc/app.py:1'],'evidence':[state(cli)['evidence']['entries'][-1]['id']] }}]
    file=tmp_path/'fresh.json';file.write_text(json.dumps(data));before=state_file().read_bytes()
    invoke(cli,'review-fresh','--file',str(file),ok=False);assert state_file().read_bytes()==before


def test_linked_unsatisfied_fresh_has_one_blocker_and_explicit_compare_fix_approves(cli,repo,tmp_path):
    begin(cli,tmp_path,repo);invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    finding={'id':'F-unsat','source':'AC-01','severity':'BLOCKING','description':'Criterion requires explicit comparison fix.'}
    data=report_data(cli,'fresh','linked',findings=[finding]);data['criteria'][0]['status']='UNSATISFIED';data['blocking']=1
    file=tmp_path/'fresh.json';file.write_text(json.dumps(data));invoke(cli,'review-fresh','--file',str(file))
    resolution={'id':'F-unsat','source':'AC-01','status':'fixed','rationale':'Verified explicit correction.',
                'locations':['svc/app.py:1'],'evidence':[state(cli)['evidence']['entries'][-1]['id']]}
    compare=comparison(cli,tmp_path,'linked-compare',[resolution]);invoke(cli,'review-result','--file',str(compare))
    invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved');assert state(cli)['status']=='APPROVED'


@pytest.mark.parametrize('defect',['new','tampered','occurrence','role','empty'])
def test_conflict_transition_and_workflow_share_current_proof(cli,repo,tmp_path,state_file,defect):
    file=conflict(cli,tmp_path,repo);s=state(cli)
    if defect=='new':invoke(cli,'phase','PSEUDOCODE');invoke(cli,'phase','PLAN_CONFLICT')
    elif defect=='tampered':Path(s['conflict_resolution']['artifact']['stored_path']).write_text('tampered')
    else:
        if defect=='occurrence':s['conflict_resolution']['occurrence']-=1
        elif defect=='role':s['conflict_resolution']['role']='al-designer'
        else:s['conflict_resolution']['artifact']=A.snapshot_bytes(s['session_id'],b'','conflict-resolution','empty')
        state_file().write_text(json.dumps(s))
    before=state_file().read_bytes();invoke(cli,'phase','PSEUDOCODE',ok=False);assert state_file().read_bytes()==before
    file.write_text('Architect supplies a new intact resolution.\n')
    invoke(cli,'conflict-resolved','--file',str(file),'--reason','current occurrence resolved');invoke(cli,'phase','PSEUDOCODE')
    if defect=='new':assert state(cli)['conflict_resolution_history']


BAD_GIT=["git -cdiff.external='touch marker; :' diff a b",'git --config-env=diff.external=SECRET diff',
         'git --config-env diff.external=SECRET diff','git diff --ext-diff','git show --textconv',
         'git -c core.pager=touch log','git -c alias.show=touch show','git -c core.fsmonitor=touch status',
         'git -c filter.x.clean=touch diff','git -c credential.helper=touch log','git -c include.path=file status',
         'git --exec-path=/tmp diff','git --paginate log','GIT_EXTERNAL_DIFF=touch git diff',
         'env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external GIT_CONFIG_VALUE_0=touch git diff',
         'env GIT_DIFF_OPTS=--ext-diff git diff','PAGER=touch git log','env GIT_PAGER=touch sh -c "git log"',
         'env GIT_EXTERNAL_DIFF=touch pnpm exec git diff','git -c unknown.key=value status']


@pytest.mark.parametrize('command',BAD_GIT)
def test_explicit_git_execution_is_denied_by_policies_ownership_and_native_hook(cli,command):
    for policy in ('readonly','shared'):assert al_shell.check(command,policy)
    invoke(cli,'start','--spec','Reject explicit executable Git overrides in ownership admission.')
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/notes.md','--engineer-commands',command,ok=False)
    hook=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    if hook.exists():
        result=subprocess.run([sys.executable,str(hook)],input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),capture_output=True,text=True)
        assert result.returncode==2 and 'deny' in result.stdout


@pytest.mark.parametrize('command',["git -c user.name=test -cuser.email=test@example.invalid status",'git -c color.ui=never --no-pager log',
         'git -c core.quotePath=false diff',"git log --grep='--ext-diff -c diff.external=touch'",
         "git log --format='--textconv'",'git grep -e --ext-diff','git diff -- --config-env=SECRET',
         'git diff -- --output=file','env NOTE=GIT_EXTERNAL_DIFF git log','git -c log.decorate=short log',
         "git log --format='%an,%s'","rg 'cp, mv'"])
def test_inert_git_config_and_option_looking_operands_remain_harmless(cli,command):
    for policy in ('readonly','shared'):assert not al_shell.check(command,policy)
    invoke(cli,'start','--spec','Permit inert Git configuration and literal search operands.')
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/notes.md','--engineer-commands',command)
    hook=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    if hook.exists():
        result=subprocess.run([sys.executable,str(hook)],input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),capture_output=True,text=True)
        assert result.returncode==0


@pytest.mark.parametrize('field',['finding','prior'])
def test_fresh_cannot_supply_any_resolution_fields(cli,repo,tmp_path,state_file,field):
    begin(cli,tmp_path,repo);invoke(cli,'review-start');invoke(cli,'review-packet','--stage','fresh')
    data=report_data(cli,'fresh','prohibited');resolution={'id':'F','source':'AC-01','status':'fixed','rationale':'claim','locations':['svc/app.py:1'],'evidence':['E1']}
    if field=='prior':data['prior_resolutions']=[resolution]
    else:data['findings']=[{'id':'F','source':'AC-01','severity':'NON_BLOCKING','description':'claim','resolution':None}]
    file=tmp_path/'fresh.json';file.write_text(json.dumps(data));before=state_file().read_bytes()
    invoke(cli,'review-fresh','--file',str(file),ok=False);assert state_file().read_bytes()==before


def test_embedded_comparison_fix_cannot_replace_explicit_inherited_clearance(cli,repo,tmp_path,state_file):
    resolution=second_round(cli,tmp_path,repo);file=comparison(cli,tmp_path,'embedded')
    data=json.loads(file.read_text());data['findings']=[{'id':'F-fresh','source':'AC-01','severity':'BLOCKING','description':'Claimed fix','resolution':resolution}]
    file.write_text(json.dumps(data));before=state_file().read_bytes()
    invoke(cli,'review-result','--file',str(file),ok=False);assert state_file().read_bytes()==before


def test_unsatisfied_comparison_cannot_use_a_fixed_finding_as_its_link(cli,repo,tmp_path,state_file):
    resolution=second_round(cli,tmp_path,repo);file=comparison(cli,tmp_path,'invalid-link',[resolution])
    data=json.loads(file.read_text());data['criteria'][0]['status']='UNSATISFIED';data['status']='CHANGES_REQUIRED'
    data['findings']=[{'id':'F-fresh','source':'AC-01','severity':'BLOCKING','description':'Fixed assertion','resolution':resolution}]
    file.write_text(json.dumps(data));before=state_file().read_bytes();invoke(cli,'review-result','--file',str(file),ok=False)
    assert state_file().read_bytes()==before


def test_repeated_finding_id_cannot_change_source_identity():
    old={'id':'F','source':'AC-01','severity':'BLOCKING','description':'unresolved'}
    with pytest.raises(SystemExit):T.obligations([old],[dict(old,source='AC-02')],fresh=True)


def test_environment_external_diff_is_an_actual_side_effect_in_disposable_repo(repo):
    (repo/'old.txt').write_text('old');(repo/'new.txt').write_text('new')
    command=['git','diff','--no-index','old.txt','new.txt']
    subprocess.run(command,cwd=repo,env={**os.environ,'GIT_EXTERNAL_DIFF':'touch env-marker; :'},capture_output=True)
    assert (repo/'env-marker').is_file()
    assert al_shell.check("GIT_EXTERNAL_DIFF='touch env-marker; :' git diff --no-index old.txt new.txt",'readonly')


@pytest.mark.parametrize('disposition',['unresolved','deferred','disputed'])
def test_comparison_disposition_never_clears_inherited_blocker(cli,repo,tmp_path,state_file,disposition):
    resolution=second_round(cli,tmp_path,repo);resolution['status']=disposition
    file=comparison(cli,tmp_path,'disposition',[resolution]);data=json.loads(file.read_text());data['status']='CHANGES_REQUIRED';file.write_text(json.dumps(data))
    invoke(cli,'review-result','--file',str(file));assert state(cli)['findings']['blocking']==1
    before=state_file().read_bytes();invoke(cli,'phase','FINAL_VERIFICATION',ok=False);assert state_file().read_bytes()==before


def test_old_fresh_stage_receipt_is_material_not_comparison_clearance(cli,repo,tmp_path):
    resolution=second_round(cli,tmp_path,repo);s=state(cli);item=s['review_history'][0];fresh=A.read_json(item['fresh'])
    fresh['findings'][0]['resolution']=resolution;item['fresh']=A.snapshot_bytes(s['session_id'],json.dumps(fresh).encode(),'architect-fresh','legacy')
    data={'schema_version':1,'fresh_sha256':item['fresh']['sha256'],'comparison_sha256':item['comparison']['sha256'],
          'validated_fixes':[{'stage':'fresh','kind':'finding','id':'F-fresh','source':'AC-01','resolution':resolution}]}
    item['resolution_validation']=A.snapshot_bytes(s['session_id'],json.dumps(data).encode(),'review-resolution-validation','legacy')
    assert H.findings(s)[0].get('resolution',{}).get('status')!='fixed'
    assert A.read_json(item['fresh'])['findings'][0]['resolution']['status']=='fixed'


def test_forged_current_receipt_stage_is_rejected(cli,repo,tmp_path):
    resolution=second_round(cli,tmp_path,repo);file=comparison(cli,tmp_path,'fixed',[resolution]);invoke(cli,'review-result','--file',str(file))
    s=state(cli);item=s['review_history'][-1];data=A.read_json(item['resolution_validation']);data['stage']='fresh'
    item['resolution_validation']=A.snapshot_bytes(s['session_id'],json.dumps(data).encode(),'review-resolution-validation','forged')
    with pytest.raises(SystemExit):H.findings(s)


@pytest.mark.parametrize('quote',["'",'"'])
def test_whole_quoted_nonexistent_executable_is_not_unquoted_into_commands(cli,repo,quote):
    missing=repo/'missing Git program';command=quote+str(missing)+quote
    with pytest.raises(FileNotFoundError):subprocess.run([str(missing)],cwd=repo)
    for policy in ('readonly','shared'):assert al_shell.check(command,policy)
    invoke(cli,'start','--spec','Reject a nonexistent quoted executable instead of unquoting it.')
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/notes.md','--engineer-commands',command,ok=False)


def test_declared_quoted_comma_command_list_checks_every_actual_command(cli):
    invoke(cli,'start','--spec','Preserve quoted comma data and check every declared command.')
    safe="git -c color.ui=never status, git log --format='%an,%s'"
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/notes.md','--engineer-commands',safe)
    unsafe="git log --format='%an,%s', git -c diff.external='touch marker; :' diff old new"
    code,out,err=invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/notes.md','--engineer-commands',unsafe,ok=False)
    assert 'diff.external' in out+err
