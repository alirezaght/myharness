"""Complete-candidate review cannot approve uncaptured child work."""
import json
import subprocess
import pytest
import al_scope as S
import al_review as R
import al_gate_inventory as V
from evidence_helpers import git
from gitlink_helpers import gitlink,commit_child,state,oid
from hook_regression_helpers import invoke


def test_ignore_submodules_cannot_hide_pointer_or_dirty_attribution(cli,repo,gitlink):
    path,_,base=gitlink
    git(repo,'config','diff.ignoreSubmodules','all')
    commit_child(path,'B')
    assert 'svc/vendor' in S.changed_paths(base)
    (path/'file').write_text('dirty')
    capture=S.capture_candidate(state(base))
    assert S.child_content_gaps(capture)
    invoke(cli,'start','--spec','Keep uncaptured child changes visible in the complete candidate review.')
    st=json.loads(cli('show')[1])
    assert any('uncaptured submodule' in gap for gap in R.approval_gaps(st))


def test_nested_dirt_and_residual_removed_dirt_are_visible(repo,tmp_path,gitlink):
    path,_,_=gitlink
    nested=tmp_path/'nested';nested.mkdir();git(nested,'init','-q');commit_child(nested,'nested')
    git(path,'-c','protocol.file.allow=always','submodule','add','-q',str(nested),'nested')
    git(path,'commit','-qam','nested boundary');git(repo,'add','svc/vendor');git(repo,'commit','-qm','nested baseline')
    (path/'nested/file').write_text('nested dirt')
    git(repo,'update-index','--force-remove','svc/vendor')
    capture=S.capture_candidate(state(oid(repo)))
    assert S.child_content_gaps(capture)
    row=next(r for r in capture['files'] if r['path']=='svc/vendor')
    assert row['worktree']=={'type':'deleted'} and row['child']['dirty']
    assert all(not name.startswith('svc/vendor/') for name in capture['untracked'])


def test_failed_inspection_is_visible_and_never_materializes(repo,gitlink,monkeypatch):
    import al_gitlinks as G
    import al_checkouts as C
    path,_,base=gitlink
    original=G.subprocess.run
    def run(argv,**kwargs):
        if kwargs.get('cwd')==path and 'status' in argv:raise subprocess.TimeoutExpired(argv,30)
        return original(argv,**kwargs)
    monkeypatch.setattr(G.subprocess,'run',run)
    capture=S.capture_candidate(state(base))
    assert S.child_content_gaps(capture)
    with pytest.raises(C.MaterializationError,match='unknown effective'):
        with C.materialize(state(base),'candidate'):pytest.fail('unknown child reached execution')


def test_rule_inventory_does_not_enter_opaque_children(cli,repo,gitlink):
    path,_,_=gitlink
    (path/'AGENTS.md').write_text('private child rule')
    invoke(cli,'start','--spec','Select parent rules without inspecting opaque child repository instructions.')
    st=json.loads(cli('show')[1]);inventory=V.inventory(st,['svc'])
    assert 'svc/vendor' in inventory['paths']
    assert not any(rule['path'].startswith('svc/vendor/') for rule in inventory['rules'])


def test_staged_pointer_is_attributed_when_child_returns_to_base(repo,gitlink):
    path,a,base=gitlink
    b=commit_child(path,'B');git(repo,'add','svc/vendor')
    git(path,'read-tree',a);git(path,'update-ref','HEAD',a);(path/'file').write_text('A')
    row=next(r for r in S.records(base) if r['path']=='svc/vendor')
    assert row['index']['oid']==b and row['worktree']['oid']==a and not row['child']['dirty']
    assert 'svc/vendor' in S.changed_paths(base)


def test_shared_consumer_selection_cannot_read_opaque_child_code_or_config(cli,repo,tmp_path,gitlink):
    path,_,_=gitlink
    git(repo,'-c','protocol.file.allow=always','submodule','add','-q',str(path),'services/opaque-service')
    git(repo,'commit','-qam','opaque service boundary')
    child=repo/'services/opaque-service'
    (child/'src/opaque').mkdir(parents=True)
    (child/'src/opaque/module.py').write_text('from parent_shared import value\n')
    (child/'pyproject.toml').write_text('[tool.importlinter]\nroot_packages=["opaque"]\n')
    (child/'AGENTS.md').write_text('child private rules')
    invoke(cli,'start','--spec','Keep opaque child repositories outside parent consumer and rule discovery.')
    st=json.loads(cli('show')[1]);inventory=V.inventory(st,['packages/shared','services/opaque-service'])
    assert not any(p.startswith('services/opaque-service/') for p in inventory['import_dependencies'])
    assert not any(rule['path'].startswith('services/opaque-service/') for rule in inventory['rules'])
    assert 'services/opaque-service' not in inventory['services']
    assert inventory['unknown_imports']


def test_ci_include_into_child_stays_unresolved_without_reading_child_yaml(cli,repo,gitlink):
    path,_,_=gitlink
    (path/'child-ci.yml').write_text('child-check:\n  script: echo child contents\n')
    (repo/'.gitlab-ci.yml').write_text('include:\n  - local: svc/vendor/child-ci.yml\n')
    invoke(cli,'start','--spec','Keep child CI includes explicit and unavailable at the parent boundary.')
    inventory=V.inventory(json.loads(cli('show')[1]),['svc/app.py'])
    assert [d['path'] for d in inventory['ci']['dependencies']]==['.gitlab-ci.yml']
    obligations=inventory['ci']['obligations']
    assert obligations and all(g['applicability']=='UNRESOLVED' for g in obligations)
    assert any('child-content' in g['reason'] for g in obligations)


def test_failed_child_can_be_reported_from_initial_start_and_disjoint_check(cli,repo,gitlink):
    from evidence_helpers import ok_cmd
    path,_,base=gitlink
    (path/'.git').write_text('gitdir: missing\n')
    invoke(cli,'start','--spec','Report unavailable child metadata while checking a disjoint parent scope.')
    fingerprint,changed=S.tree_fingerprint()
    assert fingerprint and changed>0
    invoke(cli,'evidence','run','--area','disjoint','--scope','web','--',*ok_cmd('1 passed'))
    assert S.child_content_gaps(S.capture_candidate(state(base)))


@pytest.mark.parametrize('namespace',['services','apps','packages'])
@pytest.mark.parametrize('operation',['add','update','remove'])
def test_opaque_unit_root_discovery_and_passed_candidate_gates_agree(cli,repo,tmp_path,gitlink,namespace,operation):
    import sys
    from batch13_helpers import start,finished
    from gitlink_helpers import gate_plan
    seed,_,_=gitlink;relative=namespace+'/opaque-unit';child=repo/relative
    if operation=='add':start(cli,repo)
    git(repo,'-c','protocol.file.allow=always','submodule','add','-q',str(seed),relative)
    if operation!='add':
        git(repo,'commit','-qam','unit-root child');start(cli,repo)
    expected=oid(child)
    if operation=='update':expected=commit_child(child,'updated pointer')
    elif operation=='remove':git(repo,'update-index','--force-remove',relative);expected=None
    check='import subprocess; actual=subprocess.check_output(["git","ls-files","--stage",'+repr(relative)+']); assert '
    check+='actual.decode().startswith('+repr('160000 '+expected+' ')+')' if expected else 'actual==b""'
    declarations=[{'path':relative,'capability':'parent-entry'}]
    gate_plan(cli,tmp_path,[sys.executable,'-c',check],[namespace],declarations)
    import al_artifacts as A
    st=json.loads(cli('show')[1]);manifest=A.read_json(st['gate_manifests'][-1])
    assert manifest['units']==['.']
    invoke(cli,'gates','run','--stage','candidate')
    finished(cli,'PASS')
    assert json.loads(cli('gates','status')[1])['candidate']==[]


@pytest.mark.parametrize('namespace',['services','apps','packages'])
@pytest.mark.parametrize('nested_child',[False,True])
def test_ordinary_units_keep_discovered_obligations_with_nested_gitlinks(cli,repo,tmp_path,gitlink,namespace,nested_child):
    from batch13_helpers import start
    seed,_,_=gitlink;unit=namespace+'/ordinary-unit'
    if nested_child:
        git(repo,'-c','protocol.file.allow=always','submodule','add','-q',str(seed),unit+'/vendor')
        git(repo,'commit','-qam','nested child boundary');start(cli,repo)
        commit_child(repo/unit/'vendor','new nested pointer')
    else:
        target=repo/unit/'app.ts';target.parent.mkdir(parents=True);target.write_text('before')
        start(cli,repo);target.write_text('changed')
    file=tmp_path/'selected-paths.json';file.write_text(json.dumps([unit]))
    manifest=json.loads(invoke(cli,'gates','discover','--paths-file',str(file))[1])
    assert unit in manifest['units']
    if namespace=='services':
        assert any(g['category']=='lint-imports' and unit in g['units'] and g['applicability']!='NOT_APPLICABLE' for g in manifest['gates'])
