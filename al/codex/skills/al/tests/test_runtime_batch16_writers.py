"""Every public writer must satisfy active and prospective gate coverage."""
import json
import shutil
from pathlib import Path
import pytest
import al_gate_inventory as V
from hook_regression_helpers import invoke
from batch16_helpers import setup,writer,state,manifest,plan,members


def unchanged_refusal(cli,state_file,path):
    before=state_file().read_bytes();artifacts=members(state(cli));writer(cli,path,identifier='probe-'+str(len(state(cli)['writers'])),ok=False)
    assert state_file().read_bytes()==before and members(state(cli))==artifacts


def test_in_manifest_control_then_sibling_and_new_unit_refuse_atomically(cli,repo,tmp_path,state_file):
    setup(cli,repo,tmp_path);writer(cli,'docs/readme.md');invoke(cli,'writer','finish','--id','writer','--outcome','success')
    for path in ['docs/existing.md','apps/new/app.ts','docs']:unchanged_refusal(cli,state_file,path)


@pytest.mark.parametrize('selected',['docs','.'])
def test_covered_directory_child_and_body_edit_need_no_replanning(cli,repo,tmp_path,selected):
    setup(cli,repo,tmp_path,[selected]);prior=state(cli)
    (repo/'docs/readme.md').write_text('ordinary implementation edit\n')
    writer(cli,'docs/new.md');after=state(cli)
    assert after['writers']['writer']['state']=='ACTIVE' and after['gate_manifests']==prior['gate_manifests']
    assert after['completed_phases']==prior['completed_phases'] and not after.get('gate_recovery')


def test_component_boundary_and_nonexistent_path_do_not_grant_directory_coverage(cli,repo,tmp_path,state_file):
    setup(cli,repo,tmp_path,['docs','future'])
    for path in ['docs-other/file.md','future/child.md']:unchanged_refusal(cli,state_file,path)


@pytest.mark.parametrize('change',['rule','path','unit'])
@pytest.mark.parametrize('phase',['IMPLEMENTATION','FINALIZATION'])
def test_legitimate_extension_keeps_phase_and_requires_new_base_before_writer(cli,repo,tmp_path,state_file,change,phase):
    setup(cli,repo,tmp_path)
    if phase=='FINALIZATION':invoke(cli,'phase','FINALIZATION')
    prior=state(cli)
    target='docs/readme.md'
    if change=='rule':(repo/'Makefile').write_text('verify:\n\t@echo changed policy\n')
    elif change=='path':target='docs/existing.md'
    else:target='apps/new/app.ts'
    unchanged_refusal(cli,state_file,target)
    replacement=manifest(cli,repo,[target]);plan(cli,tmp_path,replacement);st=state(cli)
    assert st['phase']==phase and st['completed_phases']==prior['completed_phases']
    assert st['gate_recovery']['replacement_manifest']==st['gate_manifests'][-1]['sha256']
    unchanged_refusal(cli,state_file,target)
    invoke(cli,'gates','run','--stage','base');writer(cli,target)
    assert state(cli)['writers']['writer']['state']=='ACTIVE' and state(cli)['gate_recovery_history']


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
    (repo/'.gitlab-ci.yml').write_text('web-verify:\n  script: echo verify\n  rules:\n    - changes: [apps/web/**/*]\n')
    setup(cli,repo,tmp_path);inv=V.inventory(state(cli),['apps'])
    assert 'apps/web' in inv['units'] and {j['job'] for j in inv['ci']['obligations']}=={'web-verify'}
    unchanged_refusal(cli,state_file,'apps')
    m=manifest(cli,repo,['apps/web/a.ts']);m['gates']=[g for g in m['gates'] if g['category']!='ci']
    m['gates'].append({'id':'fake-ci','category':'ci','applicability':'NOT_APPLICABLE','required':False,'reason':'Incorrectly omitted job','units':m['units'],'argv':[],'scope':['.'],'cwd':'.','source':m['rules'][0],'execution_kind':'local'})
    p=tmp_path/'missing-job.json';p.write_text(json.dumps(m));before=state_file().read_bytes();artifacts=members(state(cli))
    invoke(cli,'gates','plan','--file',str(p),ok=False);assert state_file().read_bytes()==before and members(state(cli))==artifacts
    plan(cli,tmp_path,manifest(cli,repo,['apps/web/a.ts']));invoke(cli,'gates','run','--stage','base');writer(cli,'apps/web/a.ts')


def test_unknown_prospective_ci_remains_unresolved(cli,repo,tmp_path,state_file):
    (repo/'.gitlab-ci.yml').write_text('verify:\n  script: echo verify\n  rules:\n    - changes: [apps/web/**/*]\n')
    (repo/'apps/web').mkdir(parents=True)
    setup(cli,repo,tmp_path);unchanged_refusal(cli,state_file,'apps/web')
    m=manifest(cli,repo,['apps/web']);assert any(g['category']=='ci' and g['applicability']=='UNRESOLVED' for g in m['gates'])
    plan(cli,tmp_path,m);unchanged_refusal(cli,state_file,'apps/web/a.ts')


def test_new_consumer_or_unknown_import_cannot_hide_behind_existing_shared_path(cli,repo,tmp_path,state_file):
    root=Path(__file__).resolve().parents[4]
    shutil.copytree(root/'ci/scripts/import_review',repo/'ci/scripts/import_review',ignore=shutil.ignore_patterns('__pycache__'))
    for name,text in [('packages/shared/src/common.py','VALUE=1\n'),('services/a/src/app.py','import common\n')]:
        p=repo/name;p.parent.mkdir(parents=True);p.write_text(text)
    setup(cli,repo,tmp_path,['packages/shared/src/common.py'])
    p=repo/'services/b/src/app.py';p.parent.mkdir(parents=True);p.write_text('import common\n')
    unchanged_refusal(cli,state_file,'packages/shared/src/common.py')
    m=manifest(cli,repo,['packages/shared/src/common.py']);assert any('services/b' in g['units'] for g in m['gates'] if g['category']=='lint-imports')
    plan(cli,tmp_path,m);invoke(cli,'gates','run','--stage','base');writer(cli,'packages/shared/src/common.py')
    invoke(cli,'writer','finish','--id','writer','--outcome','success')
    (repo/'packages/shared/src/common.py').write_text('VALUE=__import__("unknown")\n')
    unchanged_refusal(cli,state_file,'packages/shared/src/common.py')


def test_covered_parent_still_requires_prospective_new_unit_obligations(cli,repo,tmp_path,state_file):
    p=repo/'apps/web/a.ts';p.parent.mkdir(parents=True);p.write_text('export const a=1\n')
    setup(cli,repo,tmp_path,['apps'])
    unchanged_refusal(cli,state_file,'apps/new/app.ts')
    plan(cli,tmp_path,manifest(cli,repo,['apps/new/app.ts']))
    unchanged_refusal(cli,state_file,'apps/new/app.ts')
    invoke(cli,'gates','run','--stage','base');writer(cli,'apps/new/app.ts')
