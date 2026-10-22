"""Public workflow regression checks for bypass, stale approval and guard errors."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import al_store

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
from hook_regression_helpers import manifest_for
from launcher_context import admit_planning

@pytest.fixture
def cli(tmp_path, launcher):
    repo=tmp_path/'core-repo'; repo.mkdir(); runtime=repo/'.claude'/'al-runtime'
    for argv in (['git','init','-q'],['git','config','user.email','test@example.invalid'],['git','config','user.name','test']):
        subprocess.run(argv,cwd=repo,check=True)
    (repo/'code.txt').write_text('implementation\n')
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    subprocess.run(['git','commit','-qm','base'],cwd=repo,check=True)
    environment=dict(os.environ,AL_RUNTIME_DIR=str(runtime), **{"CL"+"AUDE_PROJECT_DIR":str(repo)})
    def raw(*argv):
        # Each command is a real child admitted like the managed launcher child.
        p=subprocess.run(launcher.child_argv(repo,'case',argv[:1]==('start',),argv),cwd=repo,env=environment,capture_output=True,text=True)
        return (0 if p.returncode==0 else p.stderr.strip() or p.returncode), p.stdout, p.stderr
    from launcher_context import current_protocol
    protocol=current_protocol(raw)
    def call(*argv,ok=True):
        code,out,err=protocol(*argv)
        p=subprocess.CompletedProcess(list(argv),0 if code==0 else 1,out,err)
        if ok is True: assert p.returncode==0,p.stdout+p.stderr
        elif ok is False: assert p.returncode!=0,p.stdout+p.stderr
        return p
    call.repo=repo; call.runtime=runtime
    call.state=lambda:json.loads((runtime/'case.json').read_text())
    call.plain=protocol
    return call

def _manifest_for_cli(cli,state,anticipated_paths):
    root=al_store.REPO_ROOT
    al_store.REPO_ROOT=cli.repo
    try:
        return manifest_for(state,cli.repo,anticipated_paths=anticipated_paths)
    finally:
        al_store.REPO_ROOT=root


def begin(cli, start=True, anticipated_paths=()):
    if start: cli('start','--spec','Only approve a complete verified implementation with Architect evidence.')
    cli('spec-gate','SPEC_READY'); cli('classify','SMALL','--designer','no','--implementers','al-engineer')
    st=cli.state()
    manifest=_manifest_for_cli(cli,st,('code.txt',*anticipated_paths))
    f=cli.runtime/'manifest.json';f.write_text(json.dumps(manifest));cli('gates','plan','--file',str(f))
    admit_planning(cli.plain)
    cli('phase','IMPLEMENTATION');cli('writer','start','--id','engineer','--role','al-engineer','--files','code.txt')
    cli('writer','finish','--id','engineer','--outcome','success');cli('phase','VERIFICATION')
    cli('evidence','plan','--area','unit','--tier','broad','--kind','check','--scope','code.txt','--',sys.executable,'-c','print("verified")')
    cli('evidence','run','--area','unit')

def report(cli,stage,invocation):
    st=cli.state();r=st['review'];packet=r['packets'][stage]
    inspected=[packet['sha256']]
    if stage=='compare': inspected += [r['comparison_inputs'][name]['sha256'] for name in ('plan','pseudocode') if name in r['comparison_inputs']]
    return {'schema_version':1,'review_id':r['id'],'stage':stage,'reviewer_role':'al-architect','model':st['model'],'invocation_id':invocation,
            'candidate_hash':r['candidate']['fingerprint'],'task_hash':r['task_hash'],'packet_hash':packet['sha256'],'inspected_artifacts':inspected,
            'criteria':[{'id':'AC-01','status':'SATISFIED','rationale':'Verified requirement','locations':['code.txt:1'],'evidence':['E1']}],
            'findings':[],'prior_resolutions':[],'plan_defects':[],'status':'APPROVED',
            'assessments':{'rollout':{'applicable':False,'reason':'No deployment surface'},'ui':{'applicable':False,'reason':'No rendered interface'}}}

def reviewed(cli):
    cli('review-start');cli('review-packet','--stage','fresh')
    f=cli.runtime/'fresh.json';f.write_text(json.dumps(report(cli,'fresh','fresh-invocation')))
    cli('review-fresh','--file',str(f))
    plan=cli.runtime/'approved-plan.md';plan.write_text('The approved plan verifies the complete candidate and required failure paths.\n')
    cli('review-compare-start','--plan',str(plan));cli('review-packet','--stage','compare')
    f=cli.runtime/'compare.json';f.write_text(json.dumps(report(cli,'compare','compare-invocation')))
    cli('review-result','--file',str(f));cli('phase','FINAL_VERIFICATION')

def test_approval_requires_complete_route_and_refusal_does_not_write(cli):
    cli('start','--spec','Require a verified implementation before Architect approval.')
    before=(cli.runtime/'case.json').read_bytes()
    cli('approved',ok=False);cli('phase','DONE',ok=False);cli('phase','REVIEW',ok=False)
    assert (cli.runtime/'case.json').read_bytes()==before

def test_stale_untracked_bytes_block_approval_even_with_same_metadata(cli):
    begin(cli,anticipated_paths=('new.txt',));(cli.repo/'new.txt').write_text('one');reviewed(cli)
    p=cli.repo/'new.txt';stat=p.stat();p.write_text('two');os.utime(p,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    before=(cli.runtime/'case.json').read_bytes();cli('approved',ok=False)
    assert (cli.runtime/'case.json').read_bytes()==before

def test_full_route_approves_and_clear_keeps_history(cli):
    begin(cli);reviewed(cli);cli('approved')
    assert cli.state()['approval']['review']=='R1'
    cli('clear');assert (cli.runtime/'case.json').is_file()
    cli('start','--spec','A different task must not replace this immutable run history.',ok=False)

def test_writer_scope_cannot_overlap_check_and_barrier(cli):
    begin(cli)
    before=(cli.runtime/'case.json').read_bytes()
    cli('writer','start','--id','late','--role','al-builder','--files','code.txt',ok=False)
    assert (cli.runtime/'case.json').read_bytes()==before

def test_comparison_is_locked_until_fresh_report(cli):
    begin(cli);cli('review-start');before=(cli.runtime/'case.json').read_bytes()
    cli('review-packet','--stage','compare',ok=False);cli('review-compare-start',ok=False)
    assert (cli.runtime/'case.json').read_bytes()==before

def test_shell_harmless_searches_and_real_commands():
    sys.path.insert(0,str(SCRIPTS));import al_shell
    for policy in ('readonly','shared'):
        for cmd in ["rg 'git reset > cp --fix' .",'find . -name "cp --fix"',"find . -name '-delete'",
                    "find . -iname '-exec' -print","find . -regex '-fprint'","find -L . -name '-delete'",
                    "git log --grep='git reset > cp'"]:
            assert al_shell.check(cmd,policy)==[],cmd
        for cmd in ['git -C . reset --hard','echo x > file','printf x $(git reset)',
                    "env X=1 bash -c 'git status; cp a b'",'find . -exec rm {} \\;',
                    "find . '-delete'","find . -name '-exec' -delete",'find . -fprint output',
                    "find . -execdir sh -c 'cp a b' \\;",'find . -name','find . -unsupported']:
            assert al_shell.check(cmd,policy),cmd


def test_gate_generation_runs_only_in_disposable_tree_and_drift_is_failure(cli):
    cli('start','--spec','Generation must not silently repair or alter the user candidate.')
    st=cli.state();import hashlib
    manifest={'schema_version':1,'base_oid':st['identity']['merge_base'],'units':['.'],'rules':[],'gates':[
        {'id':c,'category':c,'applicability':'NOT_APPLICABLE','required':False,'reason':'No obligation in the disposable fixture','units':['.'],'argv':[],'scope':['code.txt'],'source':None,'execution_kind':'local','cwd':'.'}
        for c in ['lint-imports','loc','contracts','pre-commit','ci']]}
    manifest['gates'][2].update(applicability='APPLICABLE',required=True,argv=[sys.executable,'-c','from pathlib import Path; Path("code.txt").write_text("generated")'],
                                 source={'path':'code.txt','sha256':hashlib.sha256((cli.repo/'code.txt').read_bytes()).hexdigest()})
    f=cli.runtime/'manifest.json';f.write_text(json.dumps(manifest));cli('gates','plan','--file',str(f))
    before=(cli.repo/'code.txt').read_bytes();cli('gates','run','--stage','base','--id','contracts',ok=False)
    assert (cli.repo/'code.txt').read_bytes()==before
    assert cli.state()['evidence']['entries'][-1]['status']=='FAIL'

def test_distribution_backs_up_and_preserves_unmanaged_files_and_rejects_drift(tmp_path):
    sys.path.insert(0,str(SCRIPTS));import al_distribution as d
    source=tmp_path/'source';source.mkdir();(source/'script.py').write_text('new implementation')
    target=tmp_path/'target';target.mkdir();(target/'script.py').write_text('old implementation');(target/'custom.conf').write_text('keep local settings')
    identity=d.repo_identity(target);rules=d.instructions(target,identity)
    inventory=[{'path':str(target),'platform':'codex','identity':identity,'instructions':rules}]
    manifest=d.plan(source,inventory,{str(target):{'reviewed':True,'instructions':rules}},registry=tmp_path/'registry')
    (target/'script.py').write_text('concurrent edit')
    with pytest.raises(ValueError,match='target drift'):d.apply(manifest,tmp_path/'backup-refused')
    assert (target/'script.py').read_text()=='concurrent edit'
    (target/'script.py').write_text('old implementation')
    records=d.apply(manifest,tmp_path/'backup')
    assert (target/'script.py').read_text()=='new implementation'
    assert (target/'custom.conf').read_text()=='keep local settings'
    assert Path(records[0]['backup']).read_text()=='old implementation'


def test_explicit_whole_mr_target_includes_earlier_commits_and_working_edits(cli):
    subprocess.run(['git','branch','target'],cwd=cli.repo,check=True)
    (cli.repo/'earlier.txt').write_text('earlier committed fix')
    subprocess.run(['git','add','earlier.txt'],cwd=cli.repo,check=True)
    subprocess.run(['git','commit','-qm','earlier fix'],cwd=cli.repo,check=True)
    (cli.repo/'code.txt').write_text('later working fix')
    cli('start','--review-mode','whole-mr','--target','target','--spec','Review earlier committed fixes and the current working candidate.')
    begin(cli,start=False)
    cli('review-start');cli('review-packet','--stage','fresh')
    state=cli.state();packet=json.loads(Path(state['review']['packets']['fresh']['stored_path']).read_text())
    assert 'earlier committed fix' in packet['candidate']['diff']
    assert 'later working fix' in packet['candidate']['diff']


def test_whole_mr_start_requires_an_explicit_target(cli):
    cli('start','--review-mode','whole-mr','--spec','Review every commit in the merge request against its target branch.',ok=False)


def test_distribution_removes_previously_managed_resources_after_a_split(tmp_path):
    sys.path.insert(0,str(SCRIPTS));import al_distribution as d
    repo=tmp_path/'distribution-repo';repo.mkdir();subprocess.run(['git','init','-q'],cwd=repo,check=True)
    subprocess.run(['git','config','user.email','test@example.invalid'],cwd=repo,check=True)
    subprocess.run(['git','config','user.name','test'],cwd=repo,check=True)
    (repo/'seed').write_text('initial')
    subprocess.run(['git','add','.'],cwd=repo,check=True);subprocess.run(['git','commit','-qm','base'],cwd=repo,check=True)
    source=tmp_path/'source';source.mkdir();(source/'test_evidence.py').write_text('old suite')
    target=repo/'.claude/skills/al';target.mkdir(parents=True)
    identity=d.repo_identity(target);inventory=[{'path':str(target),'platform':'claude','identity':identity,'instructions':d.instructions(target,identity)}]
    review={str(target):{'reviewed':True,'instructions':inventory[0]['instructions']}}
    first=d.plan(source,inventory,review,registry=tmp_path/'registry');d.apply(first,tmp_path/'backup-first')
    (source/'test_evidence.py').unlink()
    (source/'test_evidence_execution.py').write_text('split execution suite')
    (source/'test_evidence_integrity.py').write_text('split integrity suite')
    (target/'local-notes.txt').write_text('preserve local resource')
    second=d.plan(source,inventory,review,registry=tmp_path/'registry');d.apply(second,tmp_path/'backup-second')
    assert not (target/'test_evidence.py').exists()
    assert (target/'test_evidence_execution.py').is_file()
    assert (target/'test_evidence_integrity.py').is_file()
    assert (target/'local-notes.txt').read_text()=='preserve local resource'
