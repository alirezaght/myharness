"""Safety edges that catch realistic discovery, retry, history and parser bugs."""
import json
import subprocess
import pytest
import al_artifacts as A
import al_discovery_inputs as I
import al_gate_discovery as D
import al_review_history as H
import al_shell
from hook_regression_helpers import invoke, begin
from test_service_coverage import unit
from batch5_helpers import checker, comparison, second_round, state, writing


def discover(cli,paths):
    invoke(cli,'start','--spec','Discover executable sourced repository gates.')
    return D.discover(state(cli),paths)


def gate(manifest,category):return next(g for g in manifest['gates'] if g['category']==category)


def test_full_directory_service_check_and_actual_scope(cli,repo):
    unit(repo,'services/x-service')
    manifest=discover(cli,['services/x-service/src/app.py'])
    g=next(g for g in manifest['gates'] if g['id']=='imports-services-x-service')
    assert g['argv'][-2:]==['--unit','services/x-service']
    assert __import__('al_repository_facts').all_within(g['scope'],['services/x-service','services/x-service/pyproject.toml'])
    binding=next(b for b in manifest['service_coverage']['assessment']['required_bindings'] if b['id']==g['id'])
    assert binding['runner_resource']['name']=='al_architecture_check.py'
    assert binding['execution_signature']==g['execution_signature']
    assert subprocess.run(g['argv'],cwd=repo,capture_output=True).returncode==0
    (repo/'services/x-service/src/app.py').write_text('import forbidden_dependency\n')
    result=subprocess.run(g['argv'],cwd=repo,capture_output=True)
    assert result.returncode!=0 and b'forbidden import forbidden_dependency' in result.stdout


@pytest.mark.parametrize('recipe',[None,'lint-imports:\n\techo unrelated checker\n','other:\n\techo services/$(SVC)-service\n','lint-imports:\n\ttest -d services/$(SVC)/src\n','lint-imports:\n\ttest -d services/$(SVC)-service/src\n'])
def test_unknown_or_missing_service_recipe_is_unresolved(cli,repo,recipe):
    unit(repo,'services/x-service',check=False)
    if recipe:(repo/'Makefile').write_text(recipe)
    discover_manifest=discover(cli,['services/x-service/src/app.py'])
    g=next(g for g in discover_manifest['gates'] if g['id']=='imports-unresolved')
    assert g['applicability']=='UNRESOLVED' and not g['argv']
    assert 'services/x-service' in g['reason']
    assessment=discover_manifest['service_coverage']['assessment']
    assert assessment['outcome']=='incomplete' and assessment['enumeration']['status']=='complete'
    assert assessment['check_discovery']['status']=='incomplete'
    assert any(item['code']=='missing-verification-check' and item['unit']=='services/x-service' for item in assessment['gaps'])
    assert not any('services/x-service' in binding['units'] for binding in assessment['required_bindings'])


def test_loc_directory_dedup_deleted_path_and_expanded_scope(cli,repo):
    checker(repo);(repo/'services/x/src').mkdir(parents=True)
    (repo/'services/x/src/a.py').write_text('x=1\n')
    g=gate(discover(cli,['services/x/src','services/x/src/a.py','services/x/gone/nested/deleted.py']),'loc')
    # The checker predates the run, so attribution keeps it out of the task's scanned changes.
    assert g['argv']==['python3','scripts/check-loc.py','services/x']
    assert {'services/x','scripts/check-loc.py','scripts/check-loc.allowlist'}<=set(g['scope'])
    assert subprocess.run(g['argv'],cwd=repo,capture_output=True).returncode==0


def test_loc_root_directory_and_missing_allowlist_scope(cli,repo):
    checker(repo);(repo/'scripts/check-loc.allowlist').unlink(missing_ok=True)
    g=gate(discover(cli,['.']),'loc')
    assert g['argv']==['python3','scripts/check-loc.py','.']
    assert '.' in g['scope'] and 'scripts' in g['scope']


@pytest.mark.parametrize('path',['../outside.py','/tmp/outside.py','escape/file.py'])
def test_loc_escape_refuses(repo,tmp_path,path):
    outside=tmp_path/'outside';outside.mkdir();(repo/'escape').symlink_to(outside,target_is_directory=True)
    with pytest.raises(SystemExit):I.loc_directories(repo,[path])


def test_unknown_loc_interface_stays_unresolved(cli,repo):
    (repo/'scripts').mkdir();(repo/'scripts/check-loc.py').write_text('print("custom interface")')
    g=gate(discover(cli,['svc/app.py']),'loc')
    assert g['applicability']=='UNRESOLVED' and not g['argv']


@pytest.mark.parametrize('safe,agent,reason',[('no','al-engineer','retry'),('undetermined','al-engineer','retry'),('yes','al-builder','retry'),('yes','al-engineer','')])
def test_invalid_retry_preserves_failure_and_budget(cli,state_file,safe,agent,reason):
    invoke(cli,'start','--spec','Recover only a proven safe matching agent failure.')
    invoke(cli,'agent-failed','al-engineer','--reason','transient','--retry-safe',safe)
    before=state_file().read_bytes();invoke(cli,'retry',agent,'--reason',reason,ok=False)
    assert state_file().read_bytes()==before


def test_retry_without_failure_and_resume_bypass_refuse(cli,state_file,tmp_path):
    invoke(cli,'start','--spec','Do not invent failure or bypass the single retry allowance.')
    before=state_file().read_bytes();invoke(cli,'retry','al-engineer','--reason','retry',ok=False)
    assert state_file().read_bytes()==before
    invoke(cli,'agent-failed','al-engineer','--reason','transient','--retry-safe','yes')
    s=state(cli);assert s['phase_occurrences'][-1]['ended'] and not s['phase_occurrences'][-1]['completed']
    failure=s['failure'];checkpoint=failure['checkpoint']['id']
    assert s['checkpoints'][0]['id']==checkpoint and s['stopping_checkpoint']==checkpoint
    for wrapped in (False,True):
        if wrapped:
            f=tmp_path/'wait.json';f.write_text(json.dumps({'reason':'waiting','resume_instructions':'recover safely'}))
            before=state_file().read_bytes();code,_,_=invoke(cli,'waiting','--file',str(f),ok=False)
            assert state_file().read_bytes()==before
        before=state_file().read_bytes()
        invoke(cli,'resume','--checkpoint',checkpoint,ok=False)
        assert state_file().read_bytes()==before and state(cli)['failure']==failure
    invoke(cli,'retry','al-engineer','--reason','safe retry')
    assert state(cli)['retries']['al-engineer']==1
    assert cli('retry','al-engineer','--reason','exhausted')[0]==7
    assert state(cli)['status']=='STOPPED_WITH_BLOCKERS'


@pytest.mark.parametrize('field',['identity','task_hash'])
def test_retry_checkpoint_identity_mismatch_is_unchanged(cli,state_file,field):
    invoke(cli,'start','--spec','Require exact failure checkpoint identity before retry.')
    invoke(cli,'agent-failed','al-engineer','--reason','transient','--retry-safe','yes')
    s=state(cli);old=s['failure']['checkpoint'];data=A.read_json(old)
    if field=='identity':data[field]=dict(data[field],root='/foreign/repository')
    else:data[field]='wrong-task'
    ref=A.snapshot_bytes(s['session_id'],json.dumps(data).encode(),'checkpoint','fault-injection')
    s['failure']['checkpoint']=ref;s['checkpoints'][-1]=ref;s['stopping_checkpoint']=ref['id'];state_file().write_text(json.dumps(s))
    before=state_file().read_bytes();invoke(cli,'retry','al-engineer','--reason','retry',ok=False)
    assert state_file().read_bytes()==before


def test_retry_requires_writer_reconciliation_and_new_lifetime(cli,repo,tmp_path,state_file):
    writing(cli,tmp_path);invoke(cli,'writer','start','--id','first','--role','al-engineer','--files','svc/app.py')
    invoke(cli,'agent-failed','al-engineer','--reason','transient','--retry-safe','yes')
    before=state_file().read_bytes();invoke(cli,'retry','al-engineer','--reason','retry',ok=False)
    assert state_file().read_bytes()==before
    proof=tmp_path/'proof.txt';proof.write_text('Writer terminated and changes inspected.\n')
    invoke(cli,'writer','reconcile','--id','first','--reason','terminated','--artifact',str(proof))
    (repo/'svc/app.py').write_text('candidate = "changed"\n')
    invoke(cli,'retry','al-engineer','--reason','retry')
    s=state(cli);assert s['phase']=='IMPLEMENTATION' and s['evidence']['invalidated_at']
    assert s['writers']['first']['state']=='RECONCILED' and s['retry_history'][0]['changed']
    invoke(cli,'writer','start','--id','first','--role','al-engineer','--files','svc/app.py',ok=False)
    invoke(cli,'writer','start','--id','second','--role','al-engineer','--files','svc/app.py')


def test_retry_requires_crashed_check_reconciliation(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo)
    s=state(cli);entry=s['evidence']['entries'][-1]
    runner=subprocess.Popen(['python3','-c','pass']);runner.wait()
    entry.update(status='RUNNING',interrupted=True,owner_pid=runner.pid,pid=runner.pid)
    state_file().write_text(json.dumps(s))
    invoke(cli,'agent-failed','al-engineer','--reason','runner failed','--retry-safe','yes')
    before=state_file().read_bytes();invoke(cli,'retry','al-engineer','--reason','retry',ok=False)
    assert state_file().read_bytes()==before
    proof=tmp_path/'proof.txt';proof.write_text('Crashed runner inspected.\n')
    invoke(cli,'check-reconcile','--id',entry['id'],'--reason','dead runner','--artifact',str(proof))
    invoke(cli,'retry','al-engineer','--reason','retry')
    assert state(cli)['evidence']['entries'][-1]['reconciliation']


@pytest.mark.parametrize('label',['unknown','running','completed'])
def test_peer_labels_do_not_prove_safe_retry(cli,tmp_path,state_file,label):
    writing(cli,tmp_path)
    invoke(cli,'agent-failed','al-engineer','--reason','failed','--retry-safe','yes','--peer','al-builder','--peer-status',label)
    before=state_file().read_bytes();invoke(cli,'retry','al-engineer','--reason','retry',ok=False)
    assert state_file().read_bytes()==before
    proof=tmp_path/'peer.txt';proof.write_text('Supervisor confirmed peer termination and inspected its changes.\n')
    invoke(cli,'agent-peer-reconcile','al-designer','--reason','peer stopped','--artifact',str(proof),ok=False)
    invoke(cli,'agent-peer-reconcile','al-builder','--reason','peer stopped','--artifact',str(proof))
    invoke(cli,'retry','al-engineer','--reason','retry')
    assert state(cli)['retry_history'][0]['failure']['peer_reconciliation']['artifact']['sha256']


def test_peer_reconciliation_cannot_hide_active_writer(cli,tmp_path):
    writing(cli,tmp_path);invoke(cli,'writer','start','--id','peer','--role','al-builder','--files','svc/app.py')
    invoke(cli,'agent-failed','al-engineer','--reason','failed','--retry-safe','yes','--peer','al-builder')
    proof=tmp_path/'peer.txt';proof.write_text('A claimed safe label is insufficient.\n')
    invoke(cli,'agent-peer-reconcile','al-builder','--reason','claimed stopped','--artifact',str(proof),ok=False)


def test_peer_reconciliation_cannot_hide_crashed_check(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);s=state(cli)
    runner=subprocess.Popen(['python3','-c','pass']);runner.wait()
    s['evidence']['entries'][-1].update(status='RUNNING',interrupted=True,owner_pid=runner.pid,pid=runner.pid)
    state_file().write_text(json.dumps(s))
    invoke(cli,'agent-failed','al-engineer','--reason','failed','--retry-safe','yes','--peer','al-builder')
    proof=tmp_path/'peer.txt';proof.write_text('Peer proof cannot resolve an interrupted check.\n')
    before=state_file().read_bytes()
    invoke(cli,'agent-peer-reconcile','al-builder','--reason','claimed safe','--artifact',str(proof),ok=False)
    assert state_file().read_bytes()==before


def test_retry_does_not_resurrect_a_finished_writer(cli,tmp_path):
    writing(cli,tmp_path);invoke(cli,'writer','start','--id','finished','--role','al-engineer','--files','svc/app.py')
    invoke(cli,'writer','finish','--id','finished','--outcome','success')
    invoke(cli,'agent-failed','al-engineer','--reason','transient','--retry-safe','yes');invoke(cli,'retry','al-engineer','--reason','retry')
    assert state(cli)['writers']['finished']['state']=='FINISHED'
    invoke(cli,'writer','start','--id','finished','--role','al-engineer','--files','svc/app.py',ok=False)


def test_changed_planning_retry_preserves_unfinished_work(cli,repo):
    invoke(cli,'start','--spec','Preserve unfinished planning after failed-agent changes.')
    invoke(cli,'agent-failed','al-architect','--reason','transient','--retry-safe','yes')
    (repo/'svc/app.py').write_text('changed candidate\n');invoke(cli,'retry','al-architect','--reason','retry safely')
    s=state(cli);assert s['phase']=='PLANNING' and not s['phase_occurrences'][-1]['completed']


@pytest.mark.parametrize('defect',['missing','stale','source','duplicate'])
def test_second_round_fixed_resolution_requires_valid_proof(cli,repo,tmp_path,state_file,defect):
    resolution=second_round(cli,tmp_path,repo)
    if defect=='missing':resolution['evidence']=[]
    elif defect=='stale':resolution['evidence']=['E1']
    elif defect=='source':resolution['source']='INV-01'
    report=comparison(cli,tmp_path,'compare-two',[resolution])
    if defect=='duplicate':
        data=json.loads(report.read_text());data['prior_resolutions'].append(dict(resolution,status='unresolved'));report.write_text(json.dumps(data))
    before=state_file().read_bytes()
    invoke(cli,'review-result','--file',str(report),ok=False)
    assert state_file().read_bytes()==before


def test_second_round_explicit_evidenced_fix_approves_with_history_provenance(cli,repo,tmp_path):
    resolution=second_round(cli,tmp_path,repo);report=comparison(cli,tmp_path,'compare-two',[resolution])
    invoke(cli,'review-result','--file',str(report));invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')
    s=state(cli);assert s['status']=='APPROVED'
    receipt=s['review_history'][-1]['resolution_validation'];proof=A.read_json(receipt)
    assert proof['validated_fixes'][0]['id']=='F-fresh'
    assert H.findings(s)[0]['resolution']['status']=='fixed'


def test_unvalidated_historical_fixed_dictionary_cannot_clear_blocker(cli,repo,tmp_path):
    resolution=second_round(cli,tmp_path,repo);s=state(cli);item=s['review_history'][0]
    report=A.read_json(item['comparison']);report['prior_resolutions']=[dict(resolution,evidence=['E1'])]
    item.pop('resolution_validation',None)
    item['comparison']=A.snapshot_bytes(s['session_id'],json.dumps(report).encode(),'architect-compare','legacy')
    with pytest.raises(SystemExit):H.findings(s)


@pytest.mark.parametrize('command',["git log --grep='--output=file'","git log --grep '--output'",'git diff -- --output=file',
                                    'git log --author --output=file','git log --grep=--output=file', 'git diff --color -- svc/app.py'])
def test_git_option_values_and_separator_are_harmless(command):
    for policy in ('readonly','shared'):assert not al_shell.check(command,policy)


@pytest.mark.parametrize('command',['git log --grep needle --output=written','git diff --color --output written',
                                    'git diff --output','git log --grep','git show --output=written'])
def test_git_output_and_missing_value_fail_closed(command):
    for policy in ('readonly','shared'):assert al_shell.check(command,policy)
