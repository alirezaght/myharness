"""Focused proofs of the stricter rebase review/runtime contracts."""
import hashlib
import json
import sys
from pathlib import Path
import pytest
import al_artifacts as A
from supported_fixture_helpers import seed_ui
import al_assessments
import al_gates
import al_traceability as T
from hook_regression_helpers import CATEGORIES,begin,fresh_review,invoke,manifest_for,report_data,save_report
from batch9_helpers import compare_start
from launcher_context import admit_planning


def gate_plan(cli,tmp_path,repo,required=False,failure=True):
    st=json.loads(cli('show')[1]);source={'path':'docs/readme.md','sha256':hashlib.sha256((repo/'docs/readme.md').read_bytes()).hexdigest()}
    manifest=manifest_for(st,repo,anticipated_paths=('docs/readme.md',),category='loc' if required else None,
                          argv=[sys.executable,'-c','raise SystemExit(1)' if failure else "print('passed')"] if required else None,
                          source=source)
    p=tmp_path/'gates-proof.json';p.write_text(json.dumps(manifest))
    invoke(cli,'gates','plan','--file',str(p));return p


def comparison(cli,tmp_path,invocation='compare',**kwargs):
    data=report_data(cli,'compare',invocation,**kwargs);st=json.loads(cli('show')[1])
    data['inspected_artifacts'] += [ref['sha256'] for name,ref in st['review']['comparison_inputs'].items() if name in {'plan','pseudocode'}]
    p=tmp_path/'typed-comparison.json';p.write_text(json.dumps(data));return p,data


@pytest.mark.parametrize('phase',['PLANNING','PSEUDOCODE','IMPLEMENTATION'])
def test_changed_resume_preserves_each_unfinished_preverification_phase(cli,repo,tmp_path,state_file,phase):
    invoke(cli,'start','--spec','Preserve unfinished work and actual completion proof when the candidate changes.')
    if phase!='PLANNING':
        invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','COMPLEX' if phase=='PSEUDOCODE' else 'SMALL','--designer','yes' if phase=='PSEUDOCODE' else 'no','--implementers','al-engineer')
        gate_plan(cli,tmp_path,repo);invoke(cli,'phase',phase)
    p=tmp_path/'interrupt-proof.json';p.write_text(json.dumps({'reason':'Interrupted unfinished work','resume_instructions':'Finish the current phase.'}))
    _,out,_=invoke(cli,'interrupt','--file',str(p));(repo/'svc/app.py').write_text('changed after checkpoint')
    invoke(cli,'resume','--checkpoint',out.strip());st=json.loads(state_file().read_text())
    assert st['phase']==phase and phase not in st['completed_phases']
    invoke(cli,'approved',ok=False)


def test_changed_postimplementation_resume_returns_to_verification(cli,repo,tmp_path):
    begin(cli,tmp_path,repo);fresh_review(cli,tmp_path);compare_start(cli,tmp_path)
    p,_=comparison(cli,tmp_path);invoke(cli,'review-result','--file',str(p));invoke(cli,'phase','FINAL_VERIFICATION')
    stop=tmp_path/'stop.json';stop.write_text(json.dumps({'reason':'Checkpoint final work','resume_instructions':'Recheck the candidate.'}))
    _,out,_=invoke(cli,'interrupt','--file',str(stop));(repo/'svc/app.py').write_text('new_candidate = True\n')
    invoke(cli,'resume','--checkpoint',out.strip());st=json.loads(cli('show')[1])
    assert st['phase']=='VERIFICATION' and st['review'] is None


@pytest.mark.parametrize('path',['apps/web/src/domains/listing-wizard/ui/alpine/listingWizard.ts','apps/web/src/client/navigation.js'])
def test_browser_scripts_require_rendered_evidence(cli,repo,tmp_path,path):
    seed_ui(repo,path)
    begin(cli,tmp_path,repo,anticipated_paths=(path,));file=repo/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('export const changed = true;')
    invoke(cli,'gates','run','--stage','candidate')
    fresh_review(cli,tmp_path);compare_start(cli,tmp_path);p,_=comparison(cli,tmp_path)
    invoke(cli,'review-result','--file',str(p),ok=False)


@pytest.mark.parametrize('filename',['apps/web/src/server/listings.ts','apps/web/src/components/listing.server.ts'])
def test_server_only_typescript_does_not_require_a_browser(cli,repo,tmp_path,filename):
    seed_ui(repo,filename)
    begin(cli,tmp_path,repo,anticipated_paths=(filename,));path=repo/filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('export const serverValue = true;')
    invoke(cli,'gates','run','--stage','candidate')
    fresh_review(cli,tmp_path);compare_start(cli,tmp_path);p,_=comparison(cli,tmp_path)
    invoke(cli,'review-result','--file',str(p));invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')


def test_correction_fresh_is_independent_and_comparison_requires_fixed_prior_resolution(cli,repo,tmp_path):
    begin(cli,tmp_path,repo);fresh_review(cli,tmp_path);compare_start(cli,tmp_path)
    finding={'id':'F-1','source':'AC-01','severity':'BLOCKING','description':'A source defect must be corrected.'}
    p,_=comparison(cli,tmp_path,findings=[finding]);invoke(cli,'review-result','--file',str(p))
    invoke(cli,'correction','--route','al-engineer');invoke(cli,'phase','FINALIZATION')
    (repo/'svc/app.py').write_text('fixed_source = True\n');invoke(cli,'phase','VERIFICATION');invoke(cli,'gates','run','--stage','candidate');invoke(cli,'evidence','run','--area','unit','--fresh')
    fresh_review(cli,tmp_path,'second-fresh');compare_start(cli,tmp_path)
    p,data=comparison(cli,tmp_path,'second-comparison');invoke(cli,'review-result','--file',str(p),ok=False)
    eid=json.loads(cli('show')[1])['evidence']['entries'][-1]['id']
    data['prior_resolutions']=[{'id':'F-1','source':'AC-01','status':'unresolved','rationale':'Still needs a fix','locations':['svc/app.py:1'],'evidence':[eid]}]
    p.write_text(json.dumps(data));invoke(cli,'review-result','--file',str(p),ok=False)
    data['prior_resolutions'][0].update(status='fixed',rationale='The current source and valid evidence demonstrate the correction.')
    p.write_text(json.dumps(data));invoke(cli,'review-result','--file',str(p));invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')


def test_uninspected_plan_hash_refuses_comparison_without_state_write(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);fresh_review(cli,tmp_path);compare_start(cli,tmp_path)
    p=save_report(cli,tmp_path,'compare','uninspected-plan')
    report=json.loads(p.read_text());plan_hash=json.loads(cli('show')[1])['review']['comparison_inputs']['plan']['sha256']
    report['inspected_artifacts'].remove(plan_hash);p.write_text(json.dumps(report));before=state_file().read_bytes()
    invoke(cli,'review-result','--file',str(p),ok=False);assert state_file().read_bytes()==before


def test_new_manifest_needs_base_obligations_at_review_and_approval(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);fresh_review(cli,tmp_path);compare_start(cli,tmp_path)
    p,_=comparison(cli,tmp_path);invoke(cli,'review-result','--file',str(p));invoke(cli,'phase','FINAL_VERIFICATION')
    # Extend the active manifest as allowed at verification. Both barriers
    # independently recheck its base obligations, even if an earlier review passed.
    st=json.loads(state_file().read_text());st['phase']='VERIFICATION';state_file().write_text(json.dumps(st))
    gate_plan(cli,tmp_path,repo,required=True,failure=False)
    invoke(cli,'gates','run','--stage','candidate','--id','loc')
    invoke(cli,'review-start',ok=False)
    st=json.loads(state_file().read_text());st['phase']='FINAL_VERIFICATION';state_file().write_text(json.dumps(st))
    invoke(cli,'approved',ok=False)


def test_baseline_fix_decision_is_bound_to_manifest_and_failed_evidence(cli,repo,tmp_path):
    invoke(cli,'start','--spec','Only a specific failed baseline execution can be acknowledged by the user.')
    gate_plan(cli,tmp_path,repo,required=True);invoke(cli,'gates','run','--stage','base','--id','loc',ok=False)
    invoke(cli,'gates','decision','--id','loc','--action','fix','--source','User approved in-scope baseline fix','--reason','Fix this recorded baseline failure.')
    st=json.loads(cli('show')[1]);assert not al_gates.base_gaps(st)
    gate_plan(cli,tmp_path,repo,required=True);invoke(cli,'gates','run','--stage','base','--id','loc',ok=False)
    st=json.loads(cli('show')[1]);assert al_gates.base_gaps(st)


def test_wrappers_normalize_paths_and_refuse_unknown_options():
    import al_shell
    for policy in ('readonly','shared'):
        assert not al_shell.check("/usr/bin/env X=1 /usr/bin/command /usr/bin/env rg 'cp > --fix' .",policy)
        for cmd in ['/usr/bin/env cp source target','env -S "cp source target"','env -Scp','env --split-string="cp a b"',
                    'env --unknown rg x','timeout 1 /usr/bin/env cp a b','nice -n 1 /usr/bin/command cp a b']:
            assert al_shell.check(cmd,policy),cmd


def test_designer_comparison_requires_pseudocode_and_inspected_typed_hashes(cli,repo,tmp_path,state_file):
    invoke(cli,'start','--spec','Complete Designer work and inspect its approved pseudocode before comparison.')
    invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'classify','COMPLEX','--designer','yes','--implementers','al-engineer,al-builder')
    gate_plan(cli,tmp_path,repo);invoke(cli,'phase','PSEUDOCODE');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'phase','FINALIZATION');invoke(cli,'phase','VERIFICATION')
    invoke(cli,'evidence','plan','--area','unit','--tier','broad','--kind','check','--scope','svc/app.py','--',sys.executable,'-c',"print('verified')")
    invoke(cli,'evidence','run','--area','unit');fresh_review(cli,tmp_path)
    plan=tmp_path/'only-plan.md';plan.write_text('Approved plan to implement the specified Designer transformation.')
    before=state_file().read_bytes();invoke(cli,'review-compare-start','--plan',str(plan),ok=False)
    assert state_file().read_bytes()==before
    compare_start(cli,tmp_path,designer=True);p,data=comparison(cli,tmp_path)
    pseudocode=json.loads(cli('show')[1])['review']['comparison_inputs']['pseudocode']['sha256']
    data['inspected_artifacts']=[item for item in data['inspected_artifacts'] if item!=pseudocode];p.write_text(json.dumps(data))
    invoke(cli,'review-result','--file',str(p),ok=False)
    data['inspected_artifacts'].append(pseudocode);p.write_text(json.dumps(data))
    invoke(cli,'review-result','--file',str(p));invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')


def test_approval_rechecks_actual_completion_proof_instead_of_completed_labels(cli,repo,tmp_path,state_file):
    begin(cli,tmp_path,repo);fresh_review(cli,tmp_path);compare_start(cli,tmp_path)
    p,_=comparison(cli,tmp_path);invoke(cli,'review-result','--file',str(p));invoke(cli,'phase','FINAL_VERIFICATION')
    st=json.loads(state_file().read_text())
    next(o for o in reversed(st['phase_occurrences']) if o['phase']=='PLANNING')['completed']=False
    state_file().write_text(json.dumps(st));before=state_file().read_bytes()
    invoke(cli,'approved',ok=False);assert state_file().read_bytes()==before
