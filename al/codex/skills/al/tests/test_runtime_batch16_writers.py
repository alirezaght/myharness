"""Writers outside active or prospective gate coverage record it as debt; the final proof requires it."""
import json
from test_service_coverage import unit,put
import pytest
import al_gate_inventory as V
from hook_regression_helpers import invoke
from batch16_helpers import setup,writer,state,manifest,plan,members


def recorded_debt(cli,repo,path,edit=None,named=True,new=True):
    """Since #9 (6677112) and b5735a9 an uncovered writer path is verification debt, not a refusal:
    the writer is admitted and the gap is recorded as an implementation-readiness obligation."""
    prior=set(state(cli).get('implementation_readiness',{}).get('obligations',[]))
    identifier='probe-'+str(len(state(cli)['writers']));writer(cli,path,identifier=identifier);st=state(cli)
    obligations=st['implementation_readiness']['obligations']
    assert st['writers'][identifier]['state']=='ACTIVE' and (set(obligations)-prior or not new),obligations
    if named:assert any(o.endswith(': '+path) for o in obligations),obligations
    if edit is not None:put(repo,path,edit)
    invoke(cli,'writer','finish','--id',identifier,'--outcome','success')


def final_proof_refuses(cli,state_file,expected):
    """Review and approval still enforce the recorded debt, atomically."""
    if state(cli)['phase']!='VERIFICATION':invoke(cli,'phase','VERIFICATION')
    before=state_file().read_bytes();artifacts=members(state(cli))
    code,out,err=invoke(cli,'review-start',ok=False);assert expected in str(code)+out+err,str(code)+out+err
    invoke(cli,'approved',ok=False)
    assert state_file().read_bytes()==before and members(state(cli))==artifacts


def test_sibling_and_new_unit_writers_record_debt_and_final_proof_refuses(cli,repo,tmp_path,state_file):
    setup(cli,repo,tmp_path);writer(cli,'docs/readme.md');invoke(cli,'writer','finish','--id','writer','--outcome','success')
    recorded_debt(cli,repo,'docs/existing.md',edit='sibling edit\n');recorded_debt(cli,repo,'apps/new/app.ts',edit='export const a=1\n')
    recorded_debt(cli,repo,'docs')
    final_proof_refuses(cli,state_file,'replanning')


@pytest.mark.parametrize('selected',['docs','.'])
def test_covered_directory_child_and_body_edit_need_no_replanning(cli,repo,tmp_path,selected):
    setup(cli,repo,tmp_path,[selected]);prior=state(cli)
    (repo/'docs/readme.md').write_text('ordinary implementation edit\n')
    writer(cli,'docs/new.md');after=state(cli)
    assert after['writers']['writer']['state']=='ACTIVE' and after['gate_manifests']==prior['gate_manifests']
    assert after['completed_phases']==prior['completed_phases'] and not after.get('gate_recovery')


def test_component_boundary_and_nonexistent_path_do_not_grant_directory_coverage(cli,repo,tmp_path,state_file):
    setup(cli,repo,tmp_path,['docs','future'])
    for path in ['docs-other/file.md','future/child.md']:recorded_debt(cli,repo,path,edit='uncovered edit\n')
    final_proof_refuses(cli,state_file,'replanning')


@pytest.mark.parametrize('change',['rule','path','unit'])
@pytest.mark.parametrize('phase',['IMPLEMENTATION','FINALIZATION'])
def test_legitimate_extension_keeps_phase_and_admits_writer_before_new_base(cli,repo,tmp_path,state_file,change,phase):
    setup(cli,repo,tmp_path)
    if phase=='FINALIZATION':invoke(cli,'phase','FINALIZATION')
    prior=state(cli)
    target='docs/readme.md'
    if change=='rule':(repo/'Makefile').write_text('verify:\n\t@echo changed policy\n')
    elif change=='path':target='docs/existing.md'
    else:target='apps/new/app.ts'
    recorded_debt(cli,repo,target,named=change!='rule')
    if change=='rule':
        # Since a556bc4 (#32) writer finish rediscovers a changed sourced rule itself: no
        # replacement plan, the phase is kept and the refreshed manifest is current.
        st=state(cli);assert st['phase']==phase and st['completed_phases']==prior['completed_phases']
        assert st['gate_refreshes'][-1]['manifest']==st['gate_manifests'][-1]['sha256'] and not st.get('gate_recovery')
        writer(cli,target);assert state(cli)['writers']['writer']['state']=='ACTIVE'
        invoke(cli,'gates','run','--stage','base')
        return
    if change=='unit':
        put(repo,'apps/new/package.json','{"name":"new"}')
        put(repo,'.gitlab-ci.yml','new-verify:\n  script: python3 -m json.tool apps/new/package.json\n  rules:\n    - changes: [apps/new/**/*]\n')
    replacement=manifest(cli,repo,[target]);plan(cli,tmp_path,replacement);st=state(cli)
    assert st['phase']==phase and st['completed_phases']==prior['completed_phases']
    assert st['gate_recovery']['replacement_manifest']==st['gate_manifests'][-1]['sha256']
    # Base gates run alongside implementation; review still requires them and completes the
    # recovery (b5735a9), so it stays open with the current replacement.
    writer(cli,target);st=state(cli)
    assert st['writers']['writer']['state']=='ACTIVE' and st['gate_recovery']['replacement_manifest']==st['gate_manifests'][-1]['sha256']
    invoke(cli,'gates','run','--stage','base')
    if change=='unit':
        st=state(cli)
        from al_artifacts import read_json
        current=read_json(st['gate_manifests'][-1])
        gate=next(g for g in current['gates'] if g['category']=='ci' and g.get('source',{}).get('job')=='new-verify')
        run=next(row for row in reversed(st['gate_runs']) if row['gate']==gate['id'] and row['stage']=='base')
        evidence=next(e for e in st['evidence']['entries'] if e['id']==run['evidence'])
        assert evidence['status']=='FAIL' and evidence['gate_stage']=='base'


@pytest.mark.parametrize('phase',['IMPLEMENTATION','FINALIZATION'])
def test_current_manifest_cannot_request_replacement_privilege_or_replan_active_writer(cli,repo,tmp_path,state_file,phase):
    m=setup(cli,repo,tmp_path)
    if phase=='FINALIZATION':invoke(cli,'phase','FINALIZATION')
    p=tmp_path/'unchanged.json';p.write_text(json.dumps(m));before=state_file().read_bytes();artifacts=members(state(cli))
    invoke(cli,'gates','plan','--file',str(p),ok=False);assert state_file().read_bytes()==before and members(state(cli))==artifacts
    writer(cli,'docs/readme.md');p.write_text(json.dumps(manifest(cli,repo,['docs/existing.md'])));before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(p),ok=False);assert state_file().read_bytes()==before


def test_directory_inventory_discovers_units_and_sourced_ci_then_missing_job_refuses(cli,repo,tmp_path,state_file):
    p=repo/'apps/web/a.ts';p.parent.mkdir(parents=True);p.write_text('export const a=1\n')
    (repo/'.gitlab-ci.yml').write_text('web-verify:\n  script: python3 -m json.tool apps/web/package.json\n  rules:\n    - changes: [apps/web/**/*]\n')
    put(repo,'apps/web/package.json','{"name":"web"}')
    setup(cli,repo,tmp_path);inv=V.inventory(state(cli),['apps'])
    assert 'apps/web' in inv['units'] and {j['job'] for j in inv['ci']['obligations']}=={'web-verify'}
    recorded_debt(cli,repo,'apps')
    m=manifest(cli,repo,['apps/web/a.ts']);m['gates']=[g for g in m['gates'] if g['category']!='ci']
    m['gates'].append({'id':'fake-ci','category':'ci','applicability':'NOT_APPLICABLE','required':False,'reason':'Incorrectly omitted job','units':m['units'],'argv':[],'scope':['.'],'cwd':'.','source':m['rules'][0],'execution_kind':'local'})
    p=tmp_path/'missing-job.json';p.write_text(json.dumps(m));before=state_file().read_bytes();artifacts=members(state(cli))
    invoke(cli,'gates','plan','--file',str(p),ok=False);assert state_file().read_bytes()==before and members(state(cli))==artifacts
    plan(cli,tmp_path,manifest(cli,repo,['apps/web/a.ts']));invoke(cli,'gates','run','--stage','base');writer(cli,'apps/web/a.ts')


def test_unknown_prospective_ci_remains_unresolved(cli,repo,tmp_path,state_file):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script: python3 -m json.tool apps/web/package.json\n  rules:\n    - changes: [apps/web/**/*]\n')
    (repo/'apps/web').mkdir(parents=True)
    put(repo,'apps/web/package.json','{"name":"web"}')
    setup(cli,repo,tmp_path);recorded_debt(cli,repo,'apps/web')
    m=manifest(cli,repo,['apps/web']);assert any(g['category']=='ci' and g['applicability']=='UNRESOLVED' for g in m['gates'])
    # Since ad07a0d (#17) an unmodelable required CI job is a pipeline-verified obligation: it is
    # listed as unverified (never reported as passing) instead of blocking the writer or coverage.
    plan(cli,tmp_path,m);recorded_debt(cli,repo,'apps/web/a.ts',named=False,new=False)
    status=json.loads(invoke(cli,'gates','status')[1])
    assert not status['coverage_blockers']
    unverified=[row for row in status['unverified'] if row['category']=='ci']
    assert unverified and all(row['verified_by']=='not verified' for row in unverified)


def test_new_consumer_or_unknown_import_cannot_hide_behind_existing_shared_path(cli,repo,tmp_path,state_file):
    (repo/'pyproject.toml').unlink();(repo/'svc/app.py').unlink();(repo/'web/app.js').unlink()
    for name,text in [('packages/shared/src/common.py','VALUE=1\n'),('services/a/src/app.py','import common\n')]:
        p=repo/name;p.parent.mkdir(parents=True);p.write_text(text)
    for path in ('packages/shared','services/a'):unit(repo,path)
    setup(cli,repo,tmp_path,['packages/shared/src/common.py'])
    p=repo/'services/b/src/app.py';p.parent.mkdir(parents=True);p.write_text('import common\n')
    unit(repo,'services/b')
    recorded_debt(cli,repo,'packages/shared/src/common.py',named=False)
    m=manifest(cli,repo,['packages/shared/src/common.py']);assert any('services/b' in g['units'] for g in m['gates'] if g['category']=='lint-imports')
    plan(cli,tmp_path,m);invoke(cli,'gates','run','--stage','base')
    st=state(cli)
    failed_run=next(row for row in reversed(st['gate_runs']) if row['gate']=='imports-services-b' and row['stage']=='base')
    failed=failed_run['gate']
    failed_entry=next(e for e in st['evidence']['entries'] if e['id']==failed_run['evidence'])
    assert failed_entry['status']=='FAIL' and failed_entry['gate_stage']=='base'
    invoke(cli,'gates','decision','--id',failed,'--action','fix','--source','Explicit fixture developer instruction','--reason','Implement this newly admitted service, absent in the pinned baseline.')
    writer(cli,'packages/shared/src/common.py')
    invoke(cli,'writer','finish','--id','writer','--outcome','success')
    (repo/'packages/shared/src/common.py').write_text('VALUE=__import__("unknown")\n')
    # Unknown consumers invalidate coverage even inside an already covered path: the
    # inventory names the gap, a writer records it as debt and the final proof refuses.
    recorded_debt(cli,repo,'packages/shared/src/common.py',named=False)
    st=state(cli);import al_artifacts as A
    assert V.gaps(st,A.read_json(st['gate_manifests'][-1]))
    final_proof_refuses(cli,state_file,'coverage')


def test_covered_parent_still_requires_prospective_new_unit_obligations(cli,repo,tmp_path,state_file):
    p=repo/'apps/web/a.ts';p.parent.mkdir(parents=True);p.write_text('export const a=1\n')
    put(repo,'apps/web/package.json','{"name":"web"}')
    put(repo,'.gitlab-ci.yml','web-verify:\n  script: python3 -m json.tool apps/web/package.json\n')
    setup(cli,repo,tmp_path,['apps'])
    recorded_debt(cli,repo,'apps/new/app.ts')
    put(repo,'apps/new/package.json','{"name":"new"}')
    put(repo,'.gitlab-ci.yml','web-verify:\n  script:\n    - python3 -m json.tool apps/web/package.json\n    - python3 -m json.tool apps/new/package.json\n')
    plan(cli,tmp_path,manifest(cli,repo,['apps/new/app.ts']))
    writer(cli,'apps/new/app.ts')


def test_a_writers_own_check_may_overlap_its_files_but_others_may_not(cli,repo,tmp_path):
    import sys
    setup(cli,repo,tmp_path);writer(cli,'docs/readme.md')
    command=[sys.executable,'-c','print("1 passed")']
    invoke(cli,'evidence','run','--area','own','--scope','docs/readme.md','--',*command,ok=False)
    invoke(cli,'evidence','run','--area','own','--scope','docs/readme.md','--writer','writer','--',*command)
    invoke(cli,'evidence','run','--area','foreign','--scope','docs/readme.md','--writer','nobody','--',*command,ok=False)
