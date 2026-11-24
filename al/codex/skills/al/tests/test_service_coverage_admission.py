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


def test_incomplete_coverage_is_recorded_at_admission_and_blocks_only_final_proof(cli,repo,tmp_path,state_file):
    # services/a literally imports the changed module (in scope); services/b is selected only
    # through dependency uncertainty, so its missing check is a verification note (#15).
    seed(repo);unit(repo,'services/a','import common\n',check=False);unit(repo,'services/b',check=False)
    put(repo,'packages/shared/src/common.py','__import__(unknown_name)\n');start(cli)
    _,m=discover(cli,tmp_path,['packages/shared/src/common.py']);a=m['service_coverage']['assessment']
    assert a['outcome']=='incomplete' and a['enumeration']['status']=='complete'
    assert {g['unit'] for g in a['gaps']}=={'services/a'}
    assert 'services/b' in {g['unit'] for g in a['coverage_notes']}
    plan(cli,repo,tmp_path,['packages/shared/src/common.py'])
    # Incomplete coverage is verification debt: the Architect plan is admitted and
    # every reconstructed gap is recorded as an implementation-readiness obligation.
    from launcher_context import _reports
    packet=json.loads(invoke(cli,'planning','packet')[1])
    report=tmp_path/'planning.json';report.write_text(json.dumps(_reports().report_for(packet)))
    response=json.loads(invoke(cli,'planning','report','--file',str(report))[1])
    invoke(cli,'planning','admit','--file',response['materialized_obligations']['stored_path'])
    st=json.loads(cli('show')[1])
    assert st['proof_admission']['status']=='admitted' and st['phase']=='PLANNING'
    obligations=st['implementation_readiness']['obligations']
    assert any(o.startswith('coverage incomplete: missing-check: services/a') for o in obligations)
    assert not any('services/b' in o for o in obligations)
    # The admitted plan leaves the run where an ordinary scoped writer starts directly:
    # writer start records the documented PLANNING -> IMPLEMENTATION edge itself.
    code,out,err=invoke(cli,'writer','start','--id','scoped','--role','al-engineer','--files','packages/shared/src/common.py')
    assert 'IMPLEMENTATION entered by writer start' in out
    st=json.loads(cli('show')[1])
    assert st['phase']=='IMPLEMENTATION' and st['writers']['scoped']['state']=='ACTIVE'
    assert any(o.startswith('coverage incomplete:') for o in st['implementation_readiness']['obligations'])
    invoke(cli,'writer','finish','--id','scoped','--outcome','success')
    invoke(cli,'phase','VERIFICATION')
    before=state_file().read_bytes()
    code,out,err=invoke(cli,'review-start',ok=False)
    # Final proof still enforces the in-scope coverage debt: it names the uncovered unit, its
    # repair and the exact rediscovery commands (#14 item 4).
    message=str(code)+out+err
    assert 'coverage incomplete' in message and 'services/a' in message
    assert 'missing-check at unit services/a' in message
    assert 'gates discover --extend current --paths-file' in message and 'gates plan --file' in message
    assert 'services/b' not in message
    assert state_file().read_bytes()==before
    invoke(cli,'approved',ok=False)
    status=json.loads(cli('gates','status')[1])
    assert any('services/b' in note for note in status['coverage_notes'])
    assert not any('services/b' in blocker for blocker in status['coverage_blockers'])


def test_designer_route_writer_refusal_names_the_next_phase_command(cli,repo,tmp_path,state_file):
    seed(repo)
    invoke(cli,'start','--spec','Implement the scoped change and prove service coverage before verified completion.')
    writer=('writer','start','--id','w','--role','al-engineer','--files','services/a/src/app.py')
    code,out,err=invoke(cli,*writer,ok=False)
    assert '`spec-gate SPEC_READY`' in str(code)+out+err
    invoke(cli,'spec-gate','SPEC_READY')
    invoke(cli,'classify','COMPLEX','--designer','yes','--implementers','al-engineer','--reason','Coupled state invariant needs pseudocode.')
    before=state_file().read_bytes()
    code,out,err=invoke(cli,*writer,ok=False)
    assert '`phase PSEUDOCODE`' in str(code)+out+err and state_file().read_bytes()==before
    invoke(cli,'phase','PSEUDOCODE')
    invoke(cli,*writer)
    assert json.loads(cli('show')[1])['phase']=='IMPLEMENTATION'


def _facts_with_unit_gaps(repo,paths):
    import al_repository_facts as Facts
    facts=Facts.collect(repo,base_oid=None,paths=paths,opaque=[])
    for owner in ('services/a','services/b'):
        facts['checks']['gaps'].append({'code':'package-execution-pending','path':owner+'/package.json','unit':owner,
                                        'category':'ci','reason':'package script execution is not supported'})
    return facts


def test_untouched_unit_check_gaps_are_not_task_obligations(repo):
    import al_service_coverage as Coverage
    seed(repo)
    # A change inside services/b leaves services/a's own check gap deferred (still a
    # visible global diagnostic); a shared-package change widens through its consumers.
    assessment=Coverage.assess(_facts_with_unit_gaps(repo,['services/b/src/app.py']))
    assert assessment['selected_services']==['services/b']
    assert {g['unit'] for g in assessment['gaps'] if g['code']=='package-execution-pending'}=={'services/b'}
    assert assessment['global_inventory']['status']=='incomplete'
    widened=Coverage.assess(_facts_with_unit_gaps(repo,['packages/shared/src/common.py']))
    assert widened['selected_services']==['services/a']
    assert {g['unit'] for g in widened['gaps'] if g['code']=='package-execution-pending'}=={'services/a'}


def _structure_gaps(repo,paths):
    """Repository-wide structure a large monorepo carries regardless of the change (#15)."""
    facts=_facts_with_unit_gaps(repo,paths)
    facts['enumeration']['gaps']+=[
        {'code':'symlink-boundary','path':'tools/skills/linked','reason':'symlinked directory','unit':None},
        {'code':'unclassified-unit','path':'tools/pkg','reason':'unit directory has no supported language/manifest','unit':'tools/pkg'},
        {'code':'unknown-ci-inventory','path':'.gitlab-ci.yml','reason':'CI definition-world limit','unit':None}]
    facts['unsupported_boundaries'].append({'code':'opaque-boundary','path':'vendor/child','reason':'opaque child content cannot be enumerated','unit':None})
    return facts


def test_structure_gaps_outside_the_change_reach_are_notes_not_obligations(repo):
    import al_service_coverage as Coverage
    seed(repo);put(repo,'packages/shared/src/common.py','__import__(unknown_name)\n')
    facts=_structure_gaps(repo,['packages/shared/src/common.py'])
    # Dependency uncertainty selects every potential service for checks to run ...
    assert facts['dependencies']['uncertainties']
    assessment=Coverage.assess(facts)
    assert set(assessment['selected_services'])>={'services/a','services/b'}
    # ... but only the literally reached unit's gap blocks; the rest are verification notes.
    blocking={(g['code'],g.get('unit') or g['path']) for g in assessment['gaps']}
    notes={(g['code'],g.get('unit') or g['path']) for g in assessment['coverage_notes']}
    assert ('package-execution-pending','services/a') in blocking
    assert {('package-execution-pending','services/b'),('symlink-boundary','tools/skills/linked'),
            ('unclassified-unit','tools/pkg'),('unknown-ci-inventory','.gitlab-ci.yml'),
            ('opaque-boundary','vendor/child')}<=notes
    assert not blocking&notes
    assert assessment['global_inventory']['status']=='incomplete'
    rendered=Coverage.notes({'assessment':assessment})
    assert rendered and all(n.startswith('coverage note (outside the change') for n in rendered)


def test_structure_gaps_the_change_reaches_still_block(repo):
    import al_service_coverage as Coverage
    seed(repo)
    # A changed path inside an unclassified directory, a CI file the change edits, and a link a
    # reached unit literally consumes are in scope.
    facts=_structure_gaps(repo,['services/a/src/app.py','tools/pkg/run.sh','.gitlab-ci.yml'])
    facts['dependencies'].setdefault('boundary_consumers',{})['vendor/child']=['services/a/src/app.py']
    blocking={(g['code'],g.get('unit') or g['path']) for g in Coverage.assess(facts)['gaps']}
    assert {('unclassified-unit','tools/pkg'),('unknown-ci-inventory','.gitlab-ci.yml'),
            ('opaque-boundary','vendor/child'),('package-execution-pending','services/a')}<=blocking
    assert ('symlink-boundary','tools/skills/linked') not in blocking
    assert ('package-execution-pending','services/b') not in blocking
    guidance=Coverage.coverage_guidance([g for g in Coverage.assess(facts)['gaps'] if g['code']=='opaque-boundary'],{'discovery_mode':'bounded'})
    assert 'opaque-boundary at path vendor/child' in guidance and 'run AL rooted in its target' in guidance
    assert 'gates discover --extend current --paths-file <file containing []> --bounded' in guidance


def test_outside_layout_consumers_block_only_when_they_reach_the_change(repo):
    """#15: helper scripts that merely share a name with a changed file are notes."""
    import al_repository_facts as Facts
    import al_service_coverage as Coverage
    seed(repo)
    put(repo,'docs/prompt.json','{"prompt": "x"}\n')
    put(repo,'tooling/report.js','const PROMPT = "docs/prompt.json";\n')   # names the changed path
    put(repo,'tooling/other.js','const NOTE = "prompt.json";\n')           # shares only the file name
    # Unowned dependents that are not consumers themselves defeat the leaf proof for both.
    put(repo,'scripts/run_report.js','require("../tooling/report.js");\n')
    put(repo,'scripts/run_other.js','require("../tooling/other.js");\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','outside-layout consumers with dependents')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/prompt.json'],opaque=[])
    reach=facts['dependencies']['runtime_consumer_reach']
    assert reach['tooling/report.js'] is True and reach['tooling/other.js'] is False
    assessment=Coverage.assess(facts)
    blocking={g['path'] for g in assessment['gaps'] if g['code']=='unclassified-runtime-consumer'}
    notes={g['path'] for g in assessment['coverage_notes'] if g['code']=='unclassified-runtime-consumer'}
    assert 'tooling/report.js' in blocking and 'tooling/other.js' in notes
    assert 'tooling/other.js' not in blocking
    # Editing the unrelated script itself puts it in scope.
    facts=Facts.collect(repo,base_oid=None,paths=['docs/prompt.json','tooling/other.js'],opaque=[])
    assert 'tooling/other.js' in {g['path'] for g in Coverage.assess(facts)['gaps'] if g['code']=='unclassified-runtime-consumer'}


def test_unknown_root_ci_definition_limit_is_a_note_unless_edited(cli,repo,tmp_path,monkeypatch):
    """#15: the root CI's definition-world limit names the CI file, not '.', so it is scoped."""
    import al_ci_inventory as CI
    monkeypatch.setattr(CI.Inventory,'MAX_WORLDS',0)
    seed(repo);put(repo,'.gitlab-ci.yml','verify:\n  script: python3 -m pytest\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','ci fixture');start(cli)
    _,m=discover(cli,tmp_path,['services/a/src/app.py']);a=m['service_coverage']['assessment']
    limit=[g for g in a['coverage_notes'] if g['code']=='unknown-ci-inventory']
    assert limit and all(g['path']=='.gitlab-ci.yml' and 'definition-world limit' in g['reason'] for g in limit)
    assert not any(g['code']=='unknown-ci-inventory' for g in a['gaps'])
    _,m=discover(cli,tmp_path,['services/a/src/app.py','.gitlab-ci.yml'])
    assert any(g['code']=='unknown-ci-inventory' for g in m['service_coverage']['assessment']['gaps'])


def test_runtime_consumer_outside_unit_layout_does_not_widen_coverage(repo):
    import al_repository_facts as Facts
    import al_service_coverage as Coverage
    seed(repo)
    put(repo,'docs/prompt.json','{"prompt": "x"}\n')
    put(repo,'tooling/report.js','const PROMPT = "docs/prompt.json";\n')
    put(repo,'tooling/other.js','const NOTE = "prompt.json";\n')
    _git(repo,'add','.');_git(repo,'commit','-qm','outside-layout consumers')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/prompt.json'],opaque=[])
    assert any(g['code']=='unclassified-runtime-consumer' and g['path']=='tooling/report.js' for g in facts['enumeration']['gaps'])
    assessment=Coverage.assess(facts)
    # Leaf scripts outside every unit add no service-wide uncertainty and no task gap.
    assert not assessment['dependency_uncertainties'] and assessment['selected_services']==[]
    assert not any(g['code']=='unclassified-runtime-consumer' for g in assessment['gaps'])
    assert assessment['global_inventory']['status']=='incomplete'
    # A unit that literally references the outside consumer is selected through it.
    put(repo,'services/b/src/run.py','SCRIPT = "tooling/report.js"\n')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/prompt.json'],opaque=[])
    assessment=Coverage.assess(facts)
    assert 'services/b' in assessment['selected_services'] and not assessment['dependency_uncertainties']
    # Touching the outside consumer itself makes its gap a task obligation.
    facts=Facts.collect(repo,base_oid=None,paths=['docs/prompt.json','tooling/report.js'],opaque=[])
    assert any(g['code']=='unclassified-runtime-consumer' and g['path']=='tooling/report.js' for g in Coverage.assess(facts)['gaps'])


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
    unit(repo,'services/new');invoke(cli,'writer','start','--id','new','--role','al-engineer','--files','packages/shared/src/common.py')
    assert json.loads(cli('show')[1])['implementation_readiness']['obligations']
    invoke(cli,'writer','finish','--id','new','--outcome','success')
    invoke(cli,'approved',ok=False)
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
    invoke(cli,'phase','IMPLEMENTATION')
    assert json.loads(cli('show')[1])['implementation_readiness']['obligations']
    invoke(cli,'review-start',ok=False)
    plan(cli,repo,tmp_path,['services/a/src'])
    admit_planning(cli)
    state=json.loads(cli('show')[1])
    assert state['phase']=='IMPLEMENTATION' and state['proof_admission']['status']=='admitted'


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
        # A job AL cannot run locally is pipeline-verified (#17): an unverified obligation,
        # never a certified check and never a coverage gap that blocks review.
        assert a['outcome']=='identified' and not any(b.get('conventional_verification') for b in a['required_bindings'])
        delegated=m['service_coverage']['semantic_requirements']['checks']['pipeline_verified']
        assert [(d['unit'],[j['job'] for j in d['jobs']]) for d in delegated]==[('services/a',['syntax-check'])]
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
    a=m['service_coverage']['assessment']
    # Nothing certifies the service: a job AL can map stays a coverage gap; one it cannot
    # run locally is left to the pipeline as an unverified obligation (#17).
    assert not any(b.get('conventional_verification') for b in a['required_bindings'])
    delegated=m['service_coverage']['semantic_requirements']['checks'].get('pipeline_verified',[])
    if delegated:
        assert a['outcome']=='identified' and [d['unit'] for d in delegated]==['services/a']
        assert any(g['category']=='ci' and g['applicability']=='UNRESOLVED' and g['source'].get('job')=='verify' for g in m['gates'])
    else:
        assert a['outcome']=='incomplete' and any(g['code']=='missing-verification-check' for g in a['gaps'])


def test_incomplete_coverage_preserves_proof_guards_but_allows_writer_readiness(cli,repo,tmp_path,state_file):
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
    writing=deepcopy(st)
    writer_ready(writing,['packages/shared/src/common.py'])
    assert writing['implementation_readiness']['obligations']
    assert st==before and state_file().read_bytes()==disk
    for check in [lambda:require_admitted(st),lambda:ready(st)]:
        with pytest.raises(SystemExit,match='coverage'):check()
        assert st==before and state_file().read_bytes()==disk
    for command,destination,diagnostic in [
        ('review-start','REVIEW','review-start is not allowed from PLANNING'),
        ('approved','DONE','approved is not allowed from PLANNING'),
    ]:
        with pytest.raises(SystemExit,match=diagnostic):require_transition(st,command,destination)
        assert st==before and state_file().read_bytes()==disk
    assert any('coverage incomplete' in g for g in gate_gaps(st,'candidate'))
    assert any('coverage incomplete' in g for g in approval_gaps(st))
    # Admission records incomplete coverage as verification debt instead of refusing.
    previous=deepcopy(st);previous['proof_admission']['status']='pending'
    admitted=deepcopy(st);prepare_save(admitted,previous)
    assert any(o.startswith('coverage incomplete') for o in admitted['implementation_readiness']['obligations'])
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
    invoke(cli,'phase','IMPLEMENTATION')
    assert json.loads(cli('show')[1])['implementation_readiness']['obligations']
    before=state_file().read_bytes();invoke(cli,'review-start',ok=False)
    assert state_file().read_bytes()==before
    invoke(cli,'approved',ok=False)


def test_unsupported_ci_provider_is_a_note_unless_the_change_edits_it(cli,repo,tmp_path):
    # An unenumerable CI job universe is an unverified check: a verification note for a
    # change elsewhere (#15), a blocking gap for a change to the CI definition itself.
    seed(repo);put(repo,'Jenkinsfile','pipeline {}\n');start(cli)
    _,m=discover(cli,tmp_path,['docs/readme.md']);a=m['service_coverage']['assessment']
    assert not any(g['code']=='unsupported-ci-provider' for g in a['gaps'])
    assert any(g['code']=='unsupported-ci-provider' for g in a['coverage_notes'])
    _,m=discover(cli,tmp_path,['docs/readme.md','Jenkinsfile'])
    assert any(g['code']=='unsupported-ci-provider' for g in m['service_coverage']['assessment']['gaps'])


def scoped_docs_fixture(repo):
    import subprocess
    seed(repo)
    unit(repo, 'services/b', check=False)
    put(repo, 'docs/policy.json', '{"valid": true}\n')
    put(repo, '.gitlab-ci.yml', "include:\n  - local: ci/unused.yml\n    rules:\n      - if: '$OPTIONAL_PIPELINE'\nverify-documents:\n  script: python3 -m json.tool docs/policy.json\n  rules:\n    - changes: [docs/**/*]\n")
    put(repo, 'ci/unused.yml', 'b:fast_check:\n  image: python:3.13\n  script: unsupported-check\n  rules:\n    - changes: [services/b/**/*]\n')
    (repo / '.codex').mkdir(exist_ok=True)
    put(repo, 'tooling/shared-skills/sentinel.md', 'Contained fixture target; discovery must not follow the link.\n')
    (repo / '.codex/skills').symlink_to('../tooling/shared-skills', target_is_directory=True)
    (repo / 'vendor.wiki').mkdir()
    oid = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    _git(repo, 'add', '.')
    _git(repo, 'update-index', '--add', '--cacheinfo', '160000', oid, 'vendor.wiki')
    _git(repo, 'commit', '-qm', 'task-scoped boundary and generated CI fixture')


def test_docs_only_public_admission_currency_and_writer_keep_global_diagnostics(cli, repo, tmp_path, state_file):
    cli=cli.raw
    scoped_docs_fixture(repo)
    start(cli)
    put(repo, 'docs/readme.md', 'Updated narrative\n')
    manifest = plan(cli, repo, tmp_path, ['docs/readme.md'])
    coverage = manifest['service_coverage']
    assert coverage['schema_version'] == 2
    assert coverage['assessment']['selected_services'] == []
    assert coverage['assessment']['outcome'] == 'identified'
    assert coverage['global_inventory']['status'] == 'incomplete'
    records = coverage['projection']['records']
    assert any(r['record']['path'] == 'vendor.wiki' for r in records)
    assert any(r['record']['path'] == '.codex/skills' for r in records)
    assert any(r['record'].get('unit') == 'services/b' for r in records)
    assert [g['source']['job'] for g in manifest['gates'] if g['category'] == 'ci' and g['applicability'] != 'NOT_APPLICABLE'] == ['verify-documents']
    assert any(row['record'].get('job') == 'b:fast_check' and not row['relevant'] for row in coverage['projection']['ci_records'])
    invoke(cli, 'gates', 'run', '--stage', 'base')
    admit_planning(cli)
    state = json.loads(cli('show')[1])
    assert state['proof_admission']['status'] == 'admitted'
    assert Currency.check(state)['current']
    invoke(cli, 'phase', 'IMPLEMENTATION')
    invoke(cli, 'writer', 'start', '--id', 'docs', '--role', 'al-engineer', '--files', 'docs/readme.md')
    invoke(cli, 'writer', 'finish', '--id', 'docs', '--outcome', 'success')
    from al_gate_coverage import prospective_gaps
    state=json.loads(cli('show')[1])
    debt=prospective_gaps(state,['services/b/src/app.py'])
    assert any('not anticipated' in reason for reason in debt)
    assert any('unplanned unit' in reason for reason in debt)
    before = state_file().read_bytes()
    invoke(cli,'writer','start','--id','unsafe','--role','al-engineer','--files','../outside.py',ok=False)
    assert state_file().read_bytes() == before
    invoke(cli,'writer','start','--id','unplanned','--role','al-engineer','--files','services/b/src/app.py')
    admitted=json.loads(cli('show')[1])
    assert set(debt)<=set(admitted['implementation_readiness']['obligations'])
    put(repo,'services/b/src/app.py','VALUE = 2\n')
    invoke(cli,'writer','finish','--id','unplanned','--outcome','success')
    invoke(cli,'phase','VERIFICATION')
    before=state_file().read_bytes()
    code,out,err=invoke(cli,'review-start',ok=False)
    assert 'replanning' in str(code)+out+err or 'coverage' in str(code)+out+err
    assert state_file().read_bytes()==before


def test_formerly_unrelated_consumer_invalidates_currency_and_admission(cli, repo, tmp_path, state_file):
    cli=cli.raw
    scoped_docs_fixture(repo)
    start(cli)
    put(repo, 'docs/readme.md', 'Updated narrative\n')
    plan(cli, repo, tmp_path, ['docs/readme.md'])
    invoke(cli, 'gates', 'run', '--stage', 'base')
    admit_planning(cli)
    previous = json.loads(cli('show')[1])
    assert Currency.check(previous)['current']
    put(repo, 'services/b/src/prompt.py', 'PROMPT = "docs/readme.md"\n')
    currency = Currency.check(previous)
    assert not currency['current'] and currency['pathless']
    _, manifest = discover(cli, tmp_path, ['docs/readme.md'])
    assert 'services/b' in manifest['service_coverage']['assessment']['selected_services']
    assert manifest['service_coverage']['assessment']['outcome'] == 'incomplete'
    import al_service_coverage as Coverage
    with pytest.raises(SystemExit,match='coverage'):Coverage.require_ready(previous,current=True)
    assert json.loads(cli('show')[1])['proof_admission']['status']=='admitted'
    # Prior authored admission stays historical; a distinct disposable public
    # session proves current pending-proof admission on both host protocols.
    previous_cli=cli
    session='consumer-current'
    cli=lambda *args: previous_cli('--session',session,*args)
    start(cli)
    plan(cli, repo, tmp_path, ['docs/readme.md'])
    from launcher_context import _reports
    packet = json.loads(invoke(cli, 'planning', 'packet')[1])
    report = tmp_path / 'consumer-planning.json'
    report.write_text(json.dumps(_reports().report_for(packet)))
    response = json.loads(invoke(cli, 'planning', 'report', '--file', str(report))[1])
    invoke(cli, 'planning', 'admit', '--file', response['materialized_obligations']['stored_path'])
    current = json.loads(cli('show')[1])
    assert current['proof_admission']['status'] == 'admitted'
    assert any(o.startswith('coverage incomplete') for o in current['implementation_readiness']['obligations'])
    with pytest.raises(SystemExit, match='coverage'):
        Coverage.require_ready(current, current=True)


def test_v1_document_fallback_stays_v1_and_requires_v2_replanning(cli, repo, tmp_path, monkeypatch, state_file):
    import al_service_coverage as Coverage
    import al_repository_facts as Facts
    import al_artifacts as Artifacts
    import al_gate_refresh as Refresh
    seed(repo)
    put(repo, 'packages/shared/guide.md', '# Guide\n')
    start(cli)
    _, current = discover(cli, tmp_path, ['packages/shared/guide.md'])
    facts = Facts.collect(repo, base_oid=None, paths=['packages/shared/guide.md'], opaque=[])
    facts = deepcopy(facts)
    facts.update(schema_version=1, policy_version=1)
    facts['dependencies']['uncertainties'] = [{'code':'uncertain-artifact-consumer','unit':None,
        'path':'packages/shared/guide.md','location':None,'target':None,
        'reason':'documentation has no literal consumer reference; all enumerated services require verification'}]
    assessment = Coverage.assess(facts)
    assert assessment['schema_version'] == 1 and assessment['outcome'] == 'exhaustive'
    assert assessment['selected_services'] == ['services/a', 'services/b']
    # The retained V1 model remains independently reconstructible. Its unbound
    # checks are not falsely made executable in this structural fixture.
    facts['checks'] = {'obligations':[], 'bindings':[], 'gaps':[]}
    assessment = Coverage.assess(facts)
    legacy = Coverage.declaration(facts, assessment, [])
    Coverage.validate_declaration(legacy, [])
    assert legacy['schema_version'] == 1 and legacy['policy_version'] == 1
    hybrid = deepcopy(legacy)
    hybrid['schema_version'] = 2
    with pytest.raises(ValueError):Coverage.validate_declaration(hybrid, [])
    manifest = deepcopy(current)
    manifest['service_coverage'] = legacy
    manifest['gates'] = [{**g} for g in manifest['gates'] if not g.get('obligation_ids')]
    # Currency rejects a retained policy transition before any automatic refresh.
    saved = Artifacts.read_json
    monkeypatch.setattr(Artifacts, 'read_json', lambda ref: manifest if ref == 'retained-v1' else saved(ref))
    state = json.loads(cli('show')[1])
    state['gate_manifests'] = ['retained-v1']
    result = Currency.check(state)
    assert not result['current'] and result['pathless']
    assert not Refresh.refreshable(state, result)


@pytest.mark.parametrize('mutation',['deferred','relevant','global','mixed'])
def test_v2_projection_tampering_refuses_without_state_mutation(cli,repo,tmp_path,state_file,mutation):
    scoped_docs_fixture(repo)
    start(cli)
    _,manifest=discover(cli,tmp_path,['docs/readme.md'])
    value=manifest['service_coverage']
    if mutation=='deferred':value['projection']['deferred']=[]
    elif mutation=='relevant':value['projection']['relevant_ci_ids']=[]
    elif mutation=='global':value['global_inventory']['status']='complete'
    else:value['policy_version']=1
    path=tmp_path/'forged-projection.json'
    path.write_text(json.dumps(manifest))
    before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(path),ok=False)
    assert state_file().read_bytes()==before


def test_self_consistent_removed_obligation_is_not_current_source_authority(cli,repo,tmp_path,state_file):
    import al_gate_inventory as Inventory
    import al_service_coverage as Coverage
    seed(repo)
    unit(repo,'services/b',check=False)
    start(cli)
    _,manifest=discover(cli,tmp_path,['services/b/src/app.py'])
    state=json.loads(cli('show')[1])
    inventory=Inventory.inventory(state,['services/b/src/app.py'])
    facts=deepcopy(inventory['repository_facts'])
    facts['checks']['gaps']=[]
    facts['checks']['obligations']=[]
    facts['checks']['bindings']=[]
    assessment=Coverage.assess(facts)
    assert assessment['outcome']=='identified'
    forged=deepcopy(manifest)
    forged['gates']=[g for g in forged['gates'] if not g.get('obligation_ids')]
    forged['service_coverage']=Coverage.declaration(facts,assessment,forged['gates'])
    Coverage.validate_declaration(forged['service_coverage'],forged['gates'])
    path=tmp_path/'forged-source-model.json';path.write_text(json.dumps(forged))
    before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(path),ok=False)
    assert state_file().read_bytes()==before


def test_changed_unmapped_ci_semantics_is_pathless_even_on_a_planned_path(cli,repo,tmp_path):
    import al_gate_refresh as Refresh
    seed(repo)
    put(repo,'.gitlab-ci.yml','extra:\n  image: python:3.13\n  script: unsupported-check\n')
    start(cli)
    plan(cli,repo,tmp_path,['services/a/src/app.py','.gitlab-ci.yml'])
    state=json.loads(cli('show')[1])
    assert Currency.check(state)['current']
    put(repo,'.gitlab-ci.yml','extra:\n  image: different/runtime:1\n  script: different-check\n')
    result=Currency.check(state)
    assert not result['current'] and result['pathless']
    assert not Refresh.refreshable(state,result)


@pytest.mark.parametrize('compact',[False,True])
def test_docs_scope_real_checks_and_bound_behavior_unlock_review(cli,repo,tmp_path,state_file,compact):
    import sys
    from launcher_context import observe_behavior
    from al_behavior_observations import behavior_gaps
    cli=cli.raw
    scoped_docs_fixture(repo)
    start(cli)
    manifest=plan(cli,repo,tmp_path,['docs/readme.md'])
    assert manifest['service_coverage']['assessment']['outcome']=='identified'
    assert json.loads(cli('show')[1])['proof_admission']['status']=='pending'
    invoke(cli,'gates','run','--stage','base')
    admit_planning(cli)
    invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','docs','--role','al-engineer','--files','docs/readme.md')
    put(repo,'docs/readme.md','Actual owned narrative edit\n')
    invoke(cli,'writer','finish','--id','docs','--outcome','success')
    invoke(cli,'gates','run','--stage','candidate')
    invoke(cli,'phase','VERIFICATION')
    invoke(cli,'evidence','plan','--area','document-policy','--tier','broad','--kind','check',
           '--scope','docs','--',sys.executable,'-m','json.tool','docs/policy.json')
    invoke(cli,'evidence','run','--area','document-policy')
    before=state_file().read_bytes()
    code,out,err=invoke(cli,'review-start',ok=False)
    assert 'missing current behavior scenario' in str(code)+out+err
    assert state_file().read_bytes()==before
    directory=tmp_path/'behavior';directory.mkdir()
    published=observe_behavior(cli,path='docs/policy.json',directory=directory,compact=compact)
    state=json.loads(cli('show')[1])
    assert not behavior_gaps(state)
    if compact:
        # One file, every scenario; AL derived the source hashes and per-item evidence itself.
        import hashlib
        assert len(published['artifacts'])==len(state['behavior_observations'])>=1
        stored=json.loads(open(state['behavior_observations'][0]['stored_path']).read())
        digest_=hashlib.sha256((repo/'docs/policy.json').read_bytes()).hexdigest()
        assert {row['source_sha256'] for row in stored['assertion_locations']}=={digest_}
        assert all(row['evidence_ids']==stored['evidence_ids'] for row in stored['observed_outcomes'])
    assert state['proof_admission']['status']=='admitted'
    assert Currency.check(state)['current']
    invoke(cli,'review-start')
    state=json.loads(cli('show')[1])
    assert state['phase']=='REVIEW' and state['review']['stage']=='fresh'
    assert state['status']=='RUNNING' and not state.get('approval')


def test_missing_projection_requires_rediscovery_even_without_service_units(cli,repo,tmp_path,state_file):
    import al_service_coverage as Coverage
    import al_repository_facts as Facts
    (repo/'pyproject.toml').unlink()
    (repo/'svc/app.py').unlink()
    (repo/'web/app.js').unlink()
    start(cli)
    _,manifest=discover(cli,tmp_path,['docs/readme.md'])
    assert manifest['service_coverage']['assessment']['selected_services']==[]
    legacy=deepcopy(manifest);legacy.pop('service_coverage')
    facts=Facts.collect(repo,base_oid=None,paths=['docs/readme.md'],opaque=[])
    assert Coverage.current_gaps(facts,Coverage.assess(facts),legacy)
    path=tmp_path/'missing-projection.json';path.write_text(json.dumps(legacy))
    before=state_file().read_bytes()
    invoke(cli,'gates','plan','--file',str(path),ok=False)
    assert state_file().read_bytes()==before


@pytest.mark.parametrize('quality',['architecture','ci'])
def test_versioned_schema_accepts_actual_v2_summary_and_retained_v1(cli,repo,tmp_path,quality):
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from referencing import Registry,Resource
    import al_gate_inventory as Inventory
    import al_service_coverage as Coverage
    seed(repo)
    if quality=='ci':
        unit(repo,'services/a',check=False)
        put(repo,'.gitlab-ci.yml','verify-a:\n  script: python3 -m compileall -q services/a/src\n')
    start(cli)
    _,manifest=discover(cli,tmp_path,['services/a/src/app.py'])
    schemas=Path(__file__).resolve().parents[1]/'reference/schemas'
    registry=Registry().with_resources((path.name,Resource.from_contents(json.loads(path.read_text())))
                                       for path in schemas.glob('*.json'))
    outer=Draft202012Validator(json.loads((schemas/'gate-manifest.json').read_text()),registry=registry)
    errors=[(list(error.absolute_path),error.validator) for error in outer.iter_errors(manifest)]
    assert errors==[]
    assert manifest['schema_version']==1 and manifest['service_coverage']['schema_version']==2
    assert isinstance(manifest['service_coverage']['provenance']['scan_inputs'],dict)
    state=json.loads(cli('show')[1])
    facts=deepcopy(Inventory.inventory(state,['services/a/src/app.py'])['repository_facts'])
    facts_validator=Draft202012Validator(json.loads((schemas/'repository-facts.json').read_text()),registry=registry)
    errors=[(list(error.absolute_path),error.validator) for error in facts_validator.iter_errors(facts)]
    assert errors==[]
    assert isinstance(facts['scan_inputs'],list)
    import al_repository_facts as Facts
    standalone=Facts.collect(repo,base_oid=None,paths=['services/a/src/app.py'],opaque=[])
    assert not list(facts_validator.iter_errors(standalone))
    for field in ('capture','question'):
        missing=deepcopy(facts);missing.pop(field)
        assert list(facts_validator.iter_errors(missing))
    facts.update(schema_version=1,policy_version=1)
    assert not list(facts_validator.iter_errors(facts))
    hybrid_facts=deepcopy(facts);hybrid_facts['policy_version']=2
    assert list(facts_validator.iter_errors(hybrid_facts))
    legacy=Coverage.declaration(facts,Coverage.assess(facts),manifest['gates'])
    Coverage.validate_declaration(legacy,manifest['gates'])
    validator=Draft202012Validator(json.loads((schemas/'service-coverage.json').read_text()),registry=registry)
    errors=[(list(error.absolute_path),error.validator) for error in validator.iter_errors(legacy)]
    assert errors==[]
    hybrid=deepcopy(legacy);hybrid['schema_version']=2
    assert list(validator.iter_errors(hybrid))
