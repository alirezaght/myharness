"""Relevance comes from sourced applicability, never diagnostic wording."""
import al_ci_inventory as CI
import pytest
from test_ci_conventional import project,WORKFLOW
from test_service_coverage import start,discover,put


def test_unrelated_gitlab_job_is_deferred_before_unsupported_execution(repo):
    put(repo,'.gitlab-ci.yml','''verify:
  script: python3 -m compileall -q services/a
  rules:
    - changes: [services/a/**/*]
other:
  image: unrelated/custom-runner:1
  script: unsupported-tool
  rules:
    - changes: [apps/other/**/*]
''')
    result=CI.inventory(repo,['services/a/src/main.py'])
    assert [o['job'] for o in result['obligations']]==['verify']
    assert result['deferred'][0]['job']=='other'
    assert result['deferred'][0]['dependencies']


def test_root_readme_keeps_global_ci_and_defers_disjoint_changes_job(repo):
    put(repo, '.gitlab-ci.yml', '''root-quality:
  script: python3 -m compileall -q .
other:
  image: unrelated/custom-runner:1
  script: unsupported-tool
  rules:
    - changes: [apps/other/**/*]
''')
    result=CI.inventory(repo,['README.md'])
    assert [o['job'] for o in result['obligations']]==['root-quality']
    assert result['obligations'][0]['units']==['.']
    assert [d['job'] for d in result['deferred']]==['other']


def test_disjoint_github_workflow_stays_in_currency_dependencies(repo):
    put(repo,'.github/workflows/other.yml','''on:
  pull_request:
    paths: [apps/other/**]
jobs:
  other:
    uses: unknown/unsupported@v1
''')
    result=CI.inventory(repo,['services/a/src/main.py'],'merge_request_quality')
    assert not result['obligations'] and len(result['deferred'])==1
    assert result['dependencies'][0]['path']=='.github/workflows/other.yml'
    # Shared/unknown effects are not proved irrelevant by a folder name.
    result=CI.inventory(repo,['apps/other/main.py'],'merge_request_quality')
    assert result['obligations'][0]['applicability']=='UNRESOLVED'


def test_concrete_extra_job_with_workflow_word_is_not_unknown_inventory(cli,repo,tmp_path):
    project(repo)
    # An unsupported concrete job's name and diagnostic cannot conceal jobs.
    put(repo,'.github/workflows/extra.yml',WORKFLOW.replace('uv run pytest','workflow-check .'))
    start(cli);_,manifest=discover(cli,tmp_path,['ordinary_project/__init__.py'])
    assessment=manifest['service_coverage']['assessment']
    assert assessment['outcome']=='identified'
    assert not any(g['code']=='unknown-ci-inventory' for g in assessment['gaps'])


def test_selected_compare_to_does_not_borrow_ordinary_task_paths():
    import al_ci_rules as Rules
    selected, _, _ = Rules.rules(
        [{'changes': {'paths': ['services/a/**/*'], 'compare_to': 'refs/heads/main'}}],
        ['services/a/src/main.py'],
    )
    assert selected is None, 'comparison authority is missing'


def test_conditional_definition_disjoint_in_every_variant_is_deferred(repo):
    put(repo, '.gitlab-ci.yml', "include:\n  - local: ci/b.yml\n    rules:\n      - if: '$OPTIONAL_PIPELINE'\n")
    put(repo, 'ci/b.yml', "b:fast_check:\n  image: python:3.13\n  script: unsupported-check\n  rules:\n    - changes: [services/b/**/*]\n")
    result = CI.inventory(repo, ['docs/guide.md'])
    assert result['obligations'] == []
    assert [d['job'] for d in result['deferred']] == ['b:fast_check']


def test_v2_rule_decision_records_only_reached_arms_and_context_fields():
    import al_ci_rules as Rules
    decision=Rules.evaluate_rules([
        {'if':'$CI_PIPELINE_SOURCE == "merge_request_event"','changes':['services/a/**/*']},
        {'changes':{'paths':['services/a/**/*'],'compare_to':'refs/heads/main'}},
    ],['services/a/main.py'],'merge_request_quality')
    assert decision['selected'] is True and decision['complete'] is True
    assert decision['evaluated_arm_indices']==[0]
    assert decision['context_fields_used']==['CI_PIPELINE_SOURCE']
    assert decision['comparison_refs']==[]


def test_v2_unknown_never_arm_cannot_select_a_job_or_exclude_a_later_fallback():
    import al_ci_rules as Rules
    never=[{'if':'$UNKNOWN_FLAG','when':'never'}]
    assert Rules.evaluate_rules(never,['docs/readme.md'])['selected'] is False
    assert Rules.evaluate_rules(never+[{'when':'always'}],['docs/readme.md'])['selected'] is None


def test_applicable_library_ci_has_a_sourced_binding_with_zero_services(cli,repo,tmp_path):
    from test_service_coverage import seed
    seed(repo)
    put(repo,'.gitlab-ci.yml','shared-verify:\n  script: python3 -m compileall -q packages/shared/src\n  rules:\n    - changes: [packages/shared/**/*]\n')
    start(cli)
    _,manifest=discover(cli,tmp_path,['packages/shared/README.md'])
    assessment=manifest['service_coverage']['assessment']
    assert assessment['selected_services']==[]
    assert any(b['category']=='ci' and b['source_chain'][0]['selector']=='shared-verify'
               for b in assessment['required_bindings'])


@pytest.mark.parametrize('provider',['gitlab','github'])
def test_directory_scope_retains_possible_future_ci_check(cli,repo,tmp_path,state_file,provider):
    import json
    from hook_regression_helpers import invoke
    from test_service_coverage import seed
    cli=cli.raw
    seed(repo)
    if provider=='gitlab':
        put(repo,'.gitlab-ci.yml','future-document-check:\n  script: unsupported-check\n  rules:\n    - changes: [services/a/src/**/*.md]\n')
    else:
        put(repo,'.github/workflows/quality.yml','on:\n  pull_request:\n    paths: [services/a/src/**/*.md]\njobs:\n  future-document-check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: unsupported-check\n')
    start(cli)
    _,manifest=discover(cli,tmp_path,['services/a/src'])
    coverage=manifest['service_coverage']
    selected=[row['record']['job'] for row in coverage['projection']['ci_records'] if row['relevant']]
    assert selected==['future-document-check']
    gates=[g for g in manifest['gates'] if g['category']=='ci' and g.get('source',{}).get('job')=='future-document-check']
    assert len(gates)==1 and gates[0]['required'] is True
    assert gates[0]['applicability']=='UNRESOLVED' and gates[0]['argv']==[]
    path=tmp_path/'directory-plan.json';path.write_text(json.dumps(manifest))
    invoke(cli,'gates','plan','--file',str(path))
    invoke(cli,'gates','run','--stage','base')
    status=json.loads(invoke(cli,'gates','status')[1])
    unverified=[row for row in status['unverified'] if row['gate']==gates[0]['id']]
    assert len(unverified)==1 and unverified[0]['verified_by']=='not verified'
    assert not any(row['gate']==gates[0]['id'] for row in status['evidence'])
    before=state_file().read_bytes()
    result=invoke(cli,'gates','run','--stage','base','--id',gates[0]['id'],ok=False)
    assert 'unverified' in ''.join(map(str,result))
    assert state_file().read_bytes()==before


@pytest.mark.parametrize('boundary',['.github','.github/workflows'])
def test_opaque_workflow_directory_is_not_enumerated(repo,tmp_path,monkeypatch,boundary):
    from pathlib import Path
    outside=tmp_path/'workflow-target';outside.mkdir()
    if boundary=='.github':
        (outside/'workflows').mkdir()
    else:
        (repo/'.github').mkdir()
    (repo/boundary).symlink_to(outside,target_is_directory=True)
    directory=repo/'.github/workflows'
    original=Path.glob
    def no_target_glob(path,pattern,*args,**kwargs):
        assert path!=directory,'opaque workflow target enumeration attempted'
        return original(path,pattern,*args,**kwargs)
    monkeypatch.setattr(Path,'glob',no_target_glob)
    result=CI.inventory(repo,['docs/readme.md'])
    assert len(result['obligations'])==1
    job=result['obligations'][0]
    assert job['job']=='<unresolved-workflows>' and job['execution'] is None
    assert job['impact']['inventory_complete'] is False


@pytest.mark.parametrize('kind',['symlink','dangling','empty'])
def test_unavailable_root_gitlab_keeps_governing_obligation(cli,repo,tmp_path,state_file,monkeypatch,kind):
    import json
    from pathlib import Path
    from hook_regression_helpers import invoke
    from launcher_context import _reports
    cli=cli.raw
    entry=repo/'.gitlab-ci.yml'
    if kind=='empty':
        entry.write_text('')
    else:
        target=tmp_path/'unread-ci-target.yml'
        if kind=='symlink':target.write_text('never-read:\n  script: unsupported-check\n')
        entry.symlink_to(target)
    original=Path.read_bytes
    def no_alias_read(path,*args,**kwargs):
        assert path!=entry or kind=='empty','root CI alias target read attempted'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_bytes',no_alias_read)
    start(cli)
    _,manifest=discover(cli,tmp_path,['docs/readme.md'])
    coverage=manifest['service_coverage']
    # The unavailable root CI stays a visible diagnostic and an unresolved CI obligation, but a
    # change that does not edit it records its unknown inventory as a verification note (#15).
    assert coverage['global_inventory']['status']=='incomplete'
    unknown=lambda rows:any(g['code']=='unknown-ci-inventory' and g['path']=='.gitlab-ci.yml' for g in rows)
    assert unknown(coverage['assessment']['coverage_notes']) and not unknown(coverage['assessment']['gaps'])
    jobs=[row['record'] for row in coverage['projection']['ci_records'] if row['relevant']]
    assert len(jobs)==1 and jobs[0]['impact']['inventory_complete'] is False
    assert jobs[0]['execution'] is None
    path=tmp_path/'unavailable-ci-plan.json';path.write_text(json.dumps(manifest))
    invoke(cli,'gates','plan','--file',str(path))
    if kind!='empty':
        from copy import deepcopy
        for mutation in ('executable','optional','argv','non-placeholder','remote','foreign-path','foreign-placeholder'):
            forged=deepcopy(manifest)
            gate=next(g for g in forged['gates'] if g['category']=='ci' and (g.get('source') or {}).get('sha256')=='0'*64)
            if mutation=='executable':gate.update(applicability='APPLICABLE',argv=['true'])
            elif mutation=='optional':gate['required']=False
            elif mutation=='non-placeholder':gate['source']['job']='invented-known-job'
            elif mutation=='remote':gate['remote_source']={'provider':'invented'}
            elif mutation=='foreign-path':gate['source']['path']='unobserved-ci.yml'
            elif mutation=='foreign-placeholder':gate['source']['job']='<unresolved-definition-999>'
            else:gate['argv']=['true']
            path.write_text(json.dumps(forged));before=state_file().read_bytes()
            invoke(cli,'gates','plan','--file',str(path),ok=False)
            assert state_file().read_bytes()==before
    packet=json.loads(invoke(cli,'planning','packet')[1])
    report=tmp_path/'unavailable-ci-report.json';report.write_text(json.dumps(_reports().report_for(packet)))
    response=json.loads(invoke(cli,'planning','report','--file',str(report))[1])
    # Unknown root CI inventory outside the change's reach is no admission obligation.
    invoke(cli,'planning','admit','--file',response['materialized_obligations']['stored_path'])
    state=json.loads(cli('show')[1])
    assert state['proof_admission']['status']=='admitted'
    assert not any('unknown-ci-inventory' in o for o in state['implementation_readiness']['obligations'])
    import al_service_coverage as Coverage
    assert not any('unknown-ci-inventory' in g for g in Coverage.admission_gaps(state,current=True))
    assert any('unknown-ci-inventory' in n for n in Coverage.coverage_notes(state))
