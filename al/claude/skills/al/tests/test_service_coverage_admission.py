"""Coverage proof cannot substitute for current requirements or passing gates."""
from copy import deepcopy
import json
import pytest
import al_gate_currency as Currency
from al_repository_facts import digest
from conftest import _git
from hook_regression_helpers import invoke
from test_service_coverage import seed,start,discover,plan,put,unit,reports_uncertain_dependents
from launcher_context import admit_planning


@pytest.mark.parametrize('mutation',['argv','selector','cwd','required','scope','env','source','selected','potential','complete','adapter','signature'])
def test_manifest_tampering_refuses_without_state_mutation(cli,repo,tmp_path,state_file,mutation):
    seed(repo);start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    gate=next(g for g in m['gates'] if g.get('obligation_ids'))
    if mutation=='argv':gate['argv']=['true']
    elif mutation=='selector':gate['argv'][-1]='services/b'
    elif mutation=='cwd':gate['cwd']='services/a'
    elif mutation=='required':gate['required']=False
    elif mutation=='scope':gate['scope']=['services/a/src/app.py']
    elif mutation=='env':gate['env_names']=['NOT_A_REQUIRED_ENV'] # extra names are conservative, valid
    elif mutation=='source':gate['source']=m['rules'][-1]
    elif mutation=='selected':m['service_coverage']['assessment']['selected_services']=[]
    elif mutation=='potential':m['service_coverage']['assessment']['potential_services']=[]
    elif mutation=='complete':m['service_coverage']['assessment']['outcome']='exhaustive'
    elif mutation=='adapter':m['service_coverage']['adapter']['version']=999
    elif mutation=='signature':gate['execution_signature']='0'*64
    p=tmp_path/'forged.json';p.write_text(json.dumps(m));before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(p),ok=mutation=='env')
    if mutation!='env':assert state_file().read_bytes()==before


def test_incomplete_missing_check_blocks_proof_phase_and_degraded_writer(cli,repo,tmp_path,state_file):
    seed(repo);unit(repo,'services/b',check=False);put(repo,'packages/shared/src/common.py','__import__(unknown_name)\n');start(cli)
    _,m=discover(cli,tmp_path,['packages/shared/src/common.py']);a=m['service_coverage']['assessment']
    assert a['outcome']=='incomplete' and a['enumeration']['status']=='complete'
    plan(cli,repo,tmp_path,['packages/shared/src/common.py'])
    before=state_file().read_bytes()
    for args in [('phase','IMPLEMENTATION'),('writer','start','--id','blocked','--role','al-engineer','--files','packages/shared/src/common.py','--degraded','--reason','diagnostic repair')]:
        invoke(cli,*args,ok=False)
        assert state_file().read_bytes()==before
    # Issuance may append immutable planning provenance; only the refused commit must not mutate.
    from launcher_context import _reports
    packet=json.loads(invoke(cli,'planning','packet')[1])
    report=tmp_path/'blocked-planning.json';report.write_text(json.dumps(_reports().report_for(packet)))
    response=json.loads(invoke(cli,'planning','report','--file',str(report))[1])
    before=state_file().read_bytes()
    invoke(cli,'planning','admit','--file',response['materialized_obligations']['stored_path'],ok=False)
    assert state_file().read_bytes()==before


def test_relevant_uncertainty_selects_all_potential_services(cli,repo,tmp_path):
    seed(repo);put(repo,'packages/shared/src/common.py','__import__(unknown_name)\n');start(cli)
    _,m=discover(cli,tmp_path,['packages/shared/src/common.py'])
    a=m['service_coverage']['assessment']
    assert a['outcome']=='exhaustive'
    assert a['selected_services']==a['potential_services']==['services/a','services/b']


def test_exhaustive_can_admit_and_body_only_edit_keeps_plan_but_stales_evidence(cli,repo,tmp_path):
    seed(repo);put(repo,'services/a/src/app.py','def load(name):\n    return __import__(name)\n');start(cli)
    # Unknown imports in a service select every potential service. The module-ban
    # runner fails nonliteral imports, so this test runs known unaffected base.
    m=plan(cli,repo,tmp_path,['packages/shared/src/common.py']);assert reports_uncertain_dependents(m['service_coverage']['assessment'])
    invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','owned','--role','al-engineer','--files','packages/shared/src/common.py');invoke(cli,'writer','finish','--id','owned','--outcome','success')
    st=json.loads(cli('show')[1]);assert Currency.check(st)['current']
    put(repo,'packages/shared/src/common.py','VALUE=2\n');assert Currency.check(st)['current']
    unit(repo,'services/new');invoke(cli,'writer','start','--id','new','--role','al-engineer','--files','packages/shared/src/common.py',ok=False)
    assert not Currency.check(st)['current']


def test_real_architecture_failure_remains_fail_and_prevents_review_and_approval(cli,repo,tmp_path,state_file):
    seed(repo);start(cli);m=plan(cli,repo,tmp_path,['services/a/src/app.py']);invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','owned','--role','al-engineer','--files','services/a/src/app.py')
    put(repo,'services/a/src/app.py','import forbidden_dependency\n');invoke(cli,'writer','finish','--id','owned','--outcome','success')
    # New import changes semantic requirements, requiring replacement planning.
    plan(cli,repo,tmp_path,['services/a/src/app.py']);invoke(cli,'gates','run','--stage','base')
    invoke(cli,'phase','VERIFICATION')
    invoke(cli,'gates','run','--stage','candidate',ok=False)
    state=json.loads(cli('show')[1]);entry=state['evidence']['entries'][-1]
    assert entry['status']=='FAIL'
    from pathlib import Path
    assert 'forbidden import forbidden_dependency' in Path(entry['log']).read_text()
    import al_gates as Gates
    import al_artifacts as Artifacts
    current_manifest=Artifacts.read_json(state['gate_manifests'][-1])
    failed_gate=next(g for g in current_manifest['gates'] if g.get('obligation_ids'))
    assert any(f"candidate gate {failed_gate['id']} requires passing evidence" in gap
               for gap in Gates.candidate_gaps(state))
    assert json.loads(cli('gates','status')[1])['candidate']
    before=state_file().read_bytes();code,out,err=invoke(cli,'review-start',ok=False);assert state_file().read_bytes()==before
    assert f"candidate gate {failed_gate['id']} requires passing evidence" in str(code)+out+err
    invoke(cli,'approved',ok=False)


def test_body_only_currency_and_byte_sensitive_candidate_evidence(cli,repo,tmp_path):
    seed(repo);start(cli);plan(cli,repo,tmp_path,['services/a/src/app.py'])
    invoke(cli,'gates','run','--stage','base');invoke(cli,'gates','run','--stage','candidate')
    st=json.loads(cli('show')[1]);assert not json.loads(cli('gates','status')[1])['candidate']
    put(repo,'services/a/src/app.py','import common\nVALUE=2\n')
    assert Currency.check(st)['current']
    assert json.loads(cli('gates','status')[1])['candidate']


def test_required_contract_environment_cannot_be_removed(cli,repo,tmp_path,state_file):
    from test_repo_adapters import contract
    contract(repo);start(cli);_,m=discover(cli,tmp_path,['services/a-service/src/app.py'])
    gate=next(g for g in m['gates'] if g.get('obligation_ids'));assert 'PATH' in gate['env_names']
    gate['env_names']=[];file=tmp_path/'narrow-env.json';file.write_text(json.dumps(m));before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(file),ok=False);assert state_file().read_bytes()==before


def test_generic_required_ci_can_cover_service_without_architecture_policy(cli,repo,tmp_path,state_file):
    seed(repo);unit(repo,'services/a',check=False)
    put(repo,'.gitlab-ci.yml','syntax-check:\n  script: python3 -m compileall -q services/a/src\n  rules:\n    - changes: [services/a/**/*]\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='identified'
    gate=next(g for g in m['gates'] if g.get('obligation_ids'))
    assert gate['category']=='ci' and 'verification-services-a' in gate['obligation_ids']
    gate['argv']=['true'];file=tmp_path/'fake-ci.json';file.write_text(json.dumps(m));before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(file),ok=False);assert state_file().read_bytes()==before


def test_unknown_import_can_use_exhaustive_generic_ci_checks_through_admission(cli,repo,tmp_path):
    seed(repo);unit(repo,'services/a',check=False);unit(repo,'services/b',check=False)
    put(repo,'packages/shared/src/common.py','def load(name):\n    return __import__(name)\n')
    put(repo,'.gitlab-ci.yml',(
        'verify-a:\n  script: python3 -m compileall -q services/a/src\n'
        '  rules:\n    - changes: [services/a/**/*]\n'
        'verify-b:\n  script: python3 -m compileall -q services/b/src\n'
        '  rules:\n    - changes: [services/b/**/*]\n'
    ))
    put(repo,'.gitignore','__pycache__/\n')
    _git(repo,'add','.')
    _git(repo,'commit','-qm','commit real syntax checks and bytecode ignore')
    start(cli);manifest=plan(cli,repo,tmp_path,['packages/shared/src/common.py'])
    assessment=manifest['service_coverage']['assessment']
    assert reports_uncertain_dependents(assessment)
    assert assessment['check_discovery']['status']=='complete'
    selected=set(assessment['selected_services'])
    assert selected<={'services/a','services/b'}
    assert {gate['source']['job'] for gate in manifest['gates'] if gate.get('obligation_ids')}=={'verify-'+s.split('/')[1] for s in selected}
    invoke(cli,'gates','run','--stage','base');admit_planning(cli);invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','shared','--role','al-engineer','--files','packages/shared/src/common.py')
    invoke(cli,'writer','finish','--id','shared','--outcome','success');invoke(cli,'phase','VERIFICATION')
    invoke(cli,'gates','run','--stage','candidate')


def test_current_source_import_change_and_new_module_stale_admission(cli,repo,tmp_path):
    seed(repo);start(cli);plan(cli,repo,tmp_path,['services/a/src'])
    st=json.loads(cli('show')[1]);assert Currency.check(st)['current']
    # A new module inside a planned unit and path is an ordinary edit; the review-start
    # inventory checks the candidate's obligations against the plan.
    put(repo,'services/a/src/new_leaf.py','VALUE=1\n');assert not Currency.check(st)['current']
    invoke(cli,'phase','IMPLEMENTATION',ok=False)
    plan(cli,repo,tmp_path,['services/a/src'])
    admit_planning(cli)
    invoke(cli,'phase','IMPLEMENTATION')


@pytest.mark.parametrize('header',['image: python:3.13\n','variables:\n  MODE: ignored\n','default:\n  before_script: echo setup\n'])
def test_ci_global_environment_is_bound_or_explicitly_unavailable(cli,repo,tmp_path,header):
    seed(repo);unit(repo,'services/a',check=False)
    put(repo,'.gitlab-ci.yml',header+'syntax-check:\n  script: python3 -m compileall -q services/a/src\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    a=m['service_coverage']['assessment']
    if 'before_script' in header:
        assert a['outcome']=='identified'
        assert any('echo setup' in g['argv'][-1] for g in m['gates'] if g.get('obligation_ids'))
    else:
        assert a['outcome']=='incomplete' and any(g['code']=='missing-verification-check' for g in a['gaps'])
        assert any(g['category']=='ci' and g['applicability']=='UNRESOLVED'
                   and g['source'].get('job')=='syntax-check' for g in m['gates'])


def test_ci_script_bytes_are_check_policy_not_implementation_currency(cli,repo,tmp_path):
    seed(repo);unit(repo,'services/a',check=False)
    put(repo,'services/a/src/verify.py','import ast\nfrom pathlib import Path\nast.parse(Path("services/a/src/app.py").read_text())\n')
    put(repo,'.gitlab-ci.yml','syntax-check:\n  script:\n    - python3 -m compileall -q services/a/src\n    - python3 services/a/src/verify.py\n')
    start(cli);plan(cli,repo,tmp_path,['services/a/src/app.py'])
    st=json.loads(cli('show')[1]);assert Currency.check(st)['current']
    put(repo,'services/a/src/verify.py','import ast\nfrom pathlib import Path\nraise SystemExit(0)\n')
    assert not Currency.check(st)['current']


@pytest.mark.parametrize('command',['echo verify', 'python3 -c \'print("verified")\'', 'python3 -m compileall -q services/b/src'])
def test_print_only_or_different_target_ci_cannot_certify_service_checks(cli,repo,tmp_path,command):
    seed(repo);unit(repo,'services/a',check=False)
    put(repo,'.gitlab-ci.yml','verify:\n  script: '+command+'\n  rules:\n    - changes: [services/a/**/*]\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    a=m['service_coverage']['assessment'];assert a['outcome']=='incomplete'
    assert any(g['code']=='missing-verification-check' for g in a['gaps'])


@pytest.mark.parametrize('lines',[
    ['exit 0','python3 -m compileall -q services/a/src'],
    ['set +e','python3 -m compileall -q services/a/src'],
    ['cd services/b','python3 -m compileall -q ../a/src'],
    ['if false','then','python3 -m compileall -q services/a/src','fi'],
    ['./tools/pytest'],
])
def test_control_flow_and_custom_tool_paths_cannot_certify_verification(cli,repo,tmp_path,lines):
    seed(repo);unit(repo,'services/a',check=False)
    put(repo,'tools/pytest','#!/bin/sh\necho fake\nexit 0\n')
    put(repo,'.gitlab-ci.yml','verify:\n  script:\n'+''.join('    - '+line+'\n' for line in lines)+'  rules:\n    - changes: [services/a/**/*]\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='incomplete'


def test_incomplete_coverage_blocks_all_shared_boundaries_even_with_admitted_proof(cli,repo,tmp_path,state_file):
    seed(repo);start(cli);plan(cli,repo,tmp_path,['packages/shared/src/common.py'])
    assert json.loads(cli('show')[1])['proof_admission']['status']=='admitted'
    unit(repo,'services/b',check=False);put(repo,'packages/shared/src/common.py','__import__(unknown)\n')
    _,m=discover(cli,tmp_path,['packages/shared/src/common.py'])
    st=json.loads(cli('show')[1]);before=deepcopy(st);disk=state_file().read_bytes()
    from al_new_origin import require_admitted,prepare_save
    from al_gate_coverage import writer_ready
    from al_gate_recovery import ready
    from al_transitions import require_transition
    from al_review import approval_gaps
    from al_gates import gate_gaps
    for check in [lambda:require_admitted(st),lambda:writer_ready(st,['packages/shared/src/common.py']),lambda:ready(st),lambda:require_transition(st,'review-start','REVIEW'),lambda:require_transition(st,'approved','DONE')]:
        with pytest.raises(SystemExit,match='coverage'):check()
        assert st==before and state_file().read_bytes()==disk
    assert any('coverage incomplete' in g for g in gate_gaps(st,'candidate'))
    assert any('coverage incomplete' in g for g in approval_gaps(st))
    previous=deepcopy(st);previous['proof_admission']['status']='pending'
    with pytest.raises(SystemExit,match='coverage'):prepare_save(st,previous)
    assert st==before and state_file().read_bytes()==disk


def test_known_extra_unverified_job_does_not_hide_unknown_job_inventory(cli,repo,tmp_path,state_file):
    seed(repo)
    put(repo,'.gitlab-ci.yml','known:\n  script: echo unsupported-extra\n  image: python:3.13\n')
    start(cli);_,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert m['service_coverage']['assessment']['outcome']=='identified'
    assert any(g['category']=='ci' and g['applicability']=='UNRESOLVED' for g in m['gates'])
    put(repo,'.gitlab-ci.yml','include:\n  - remote: https://example.invalid/jobs.yml\n')
    _,m=discover(cli,tmp_path,['services/a/src/app.py'])
    assert any(g['code']=='unknown-ci-inventory' for g in m['service_coverage']['assessment']['gaps'])
    before=state_file().read_bytes();invoke(cli,'phase','IMPLEMENTATION',ok=False)
    assert state_file().read_bytes()==before


def test_unsupported_ci_provider_blocks_even_without_selected_services(cli,repo,tmp_path):
    seed(repo);put(repo,'Jenkinsfile','pipeline {}\n');start(cli)
    _,m=discover(cli,tmp_path,['docs/readme.md'])
    assert any(g['code']=='unsupported-ci-provider' for g in m['service_coverage']['assessment']['gaps'])
