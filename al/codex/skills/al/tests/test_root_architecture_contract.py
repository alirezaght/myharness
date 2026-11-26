"""Private proposal: existing root architecture contract, not generic verification."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest
import al_artifacts as Artifacts
import al_gate_currency as Currency
import al_gates as Gates
from conftest import _git
from hook_regression_helpers import invoke
from launcher_context import admit_planning
from test_service_coverage import put, start, plan

GUARD = """import ast
import json
from pathlib import Path


def test_allowed_application_dependencies():
    config = json.loads(Path('architecture-rules.json').read_text())
    allowed = set(config['allowed_import_roots'])
    violations = []
    inspected = []
    for raw in config['application_roots']:
        directory = Path(raw)
        assert not directory.is_absolute() and '..' not in directory.parts
        assert directory.is_dir() and not directory.is_symlink()
        for source in sorted(directory.rglob('*.py')):
            assert not source.is_symlink()
            assert source.resolve().is_relative_to(Path.cwd().resolve())
            for parent in source.parents:
                if parent == Path('.'):
                    break
                assert not parent.is_symlink()
            inspected.append(source.as_posix())
            for node in ast.walk(ast.parse(source.read_text(), filename=str(source))):
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [source.parts[0] if node.level else (node.module or '').split('.')[0]]
                else:
                    continue
                violations.extend((source.as_posix(), name) for name in names if name not in allowed)
    assert inspected, 'architecture policy inspected no application modules'
    assert not violations, 'forbidden architecture import: ' + repr(violations)
"""

CONFIG = {'application_roots':['tools'], 'allowed_import_roots':['tools','json','pathlib']}
APP = 'import json\nfrom tools.core import VALUE\n\ndef render():\n    return json.dumps(VALUE)\n'


def root_contract(repo):
    for name in ('pyproject.toml','svc/app.py','web/app.js'):
        (repo/name).unlink(missing_ok=True)
    put(repo,'tools/__init__.py','# private application package\n')
    put(repo,'tools/core.py','VALUE=1\n')
    put(repo,'tools/pkg.py',APP)
    _git(repo,'add','.')
    _git(repo,'commit','-qm','private application baseline')
    put(repo,'tests/test_architecture.py',GUARD)
    put(repo,'architecture-rules.json',json.dumps(CONFIG,sort_keys=True))
    contract = {
        'schema_version':1,'kind':'al-repository-contract-v1',
        'units':[{'path':'.','language':'python'}], 'service_roots':['.'],
        'architecture_check':{
            'argv':[sys.executable,'-m','pytest','tests/test_architecture.py'],
            'applies_to':'python-services',
            'chain':['tests/test_architecture.py','architecture-rules.json'],
            'env_names':['PATH'],
        },
    }
    put(repo,'.al/repository.json',json.dumps(contract,sort_keys=True))
    # Policy lands separately before AL freezes its base.
    _git(repo,'add','.')
    _git(repo,'commit','-qm','reviewed private architecture policy')


def started_contract(cli,repo,tmp_path,run_state):
    def build():
        root_contract(repo)
        start(cli)
        plan(cli,repo,tmp_path,['tools/pkg.py'])
        invoke(cli,'gates','run','--stage','base','--id','imports-root')
        admit_planning(cli)
        invoke(cli,'phase','IMPLEMENTATION')
        invoke(cli,'gates','run','--stage','candidate','--id','imports-root')
    # Genuine public execution once; standard byte-verified private restoration.
    run_state('root-architecture-contract-passing',build)
    state=json.loads(cli('show')[1])
    return state,Artifacts.read_json(state['gate_manifests'][-1])


def architecture_entry(state,stage):
    return next(e for e in reversed(state['evidence']['entries'])
                if e.get('area')=='gate-'+stage+'-imports-root')


def assert_exact_contract_binding(repo,manifest):
    coverage=manifest['service_coverage']
    assert coverage['adapter']=={'id':'contract','version':2}
    assert coverage['assessment']['outcome']=='identified',coverage['assessment']['gaps']
    unit=next(u for u in coverage['semantic_requirements']['units'] if u['id']=='.')
    assert unit['source_roots']==['.'] and unit['languages']==['python']
    assert 'pyproject.toml' not in unit['provenance']
    assert not (repo/'pyproject.toml').exists()
    binding=next(b for b in coverage['assessment']['required_bindings'] if b['id']=='imports-root')
    gate=next(g for g in manifest['gates'] if g['id']=='imports-root')
    expected=[sys.executable,'-m','pytest','tests/test_architecture.py']
    assert binding['argv']==expected and gate['argv']==expected
    assert binding['category']=='lint-imports' and gate['category']=='lint-imports'
    assert binding['env_names']==['PATH'] and binding['obligation_ids']==['imports-root']
    assert gate['execution_signature']==binding['execution_signature']
    assert 'pyproject.toml' in binding['required_scope']
    assert all(Path(name).as_posix()==name for name in binding['required_scope'])
    chain={row['path']:row for row in binding['source_chain']}
    assert set(chain)=={'.al/repository.json','tests/test_architecture.py','architecture-rules.json'}
    for name,row in chain.items():
        assert row['sha256']==hashlib.sha256((repo/name).read_bytes()).hexdigest()
        assert row['selector']=='repository contract architecture chain'
    return binding


def test_real_root_architecture_contract_regression_and_repair(cli,repo,tmp_path,run_state,state_file):
    state,manifest=started_contract(cli,repo,tmp_path,run_state)
    assert_exact_contract_binding(repo,manifest)
    assert architecture_entry(state,'base')['status']=='PASS'
    assert architecture_entry(state,'candidate')['status']=='PASS'
    invoke(cli,'writer','start','--id','application-change','--role','al-engineer','--files','tools/pkg.py')
    put(repo,'tools/pkg.py',APP+'import socket\n')
    invoke(cli,'writer','finish','--id','application-change','--outcome','success')
    plan(cli,repo,tmp_path,['tools/pkg.py'])
    invoke(cli,'gates','run','--stage','base','--id','imports-root')
    invoke(cli,'phase','VERIFICATION')
    invoke(cli,'gates','run','--stage','candidate','--id','imports-root',ok=False)
    failed_state=json.loads(cli('show')[1])
    failure=deepcopy(architecture_entry(failed_state,'candidate'))
    assert failure['status']=='FAIL' and failure['exit']!=0
    assert 'forbidden architecture import' in Path(failure['log']).read_text()
    missing='candidate gate imports-root requires passing evidence'
    assert missing in Gates.candidate_gaps(failed_state)
    assert missing in json.loads(cli('gates','status')[1])['candidate']
    unchanged=state_file().read_bytes()
    code,out,err=invoke(cli,'review-start',ok=False)
    assert missing in str(code)+out+err and state_file().read_bytes()==unchanged
    # This real unrelated check cannot satisfy the architecture binding.
    invoke(cli,'evidence','plan','--area','unrelated-check','--kind','check','--scope','docs','--',
        sys.executable,'-I','-c',
        "from pathlib import Path; assert Path('docs/readme.md').read_text()=='hi\\n'; print('unrelated check')")
    invoke(cli,'evidence','run','--area','unrelated-check')
    manual=json.loads(cli('show')[1])
    assert any(e['area']=='unrelated-check' and e['status']=='PASS' for e in manual['evidence']['entries'])
    assert missing in Gates.candidate_gaps(manual)
    put(repo,'tools/pkg.py',APP+'# forbidden dependency removed\n')
    plan(cli,repo,tmp_path,['tools/pkg.py'])
    invoke(cli,'gates','run','--stage','base','--id','imports-root')
    invoke(cli,'gates','run','--stage','candidate','--id','imports-root')
    fixed=json.loads(cli('show')[1])
    assert architecture_entry(fixed,'candidate')['status']=='PASS'
    assert missing not in Gates.candidate_gaps(fixed)
    assert next(e for e in fixed['evidence']['entries'] if e['id']==failure['id'])==failure


@pytest.mark.parametrize('change',['contract-policy','missing-chain','check-source'])
def test_prior_contract_binding_cannot_survive_policy_or_chain_changes(
        cli,repo,tmp_path,run_state,change):
    state,manifest=started_contract(cli,repo,tmp_path,run_state)
    original=assert_exact_contract_binding(repo,manifest)
    assert architecture_entry(state,'candidate')['status']=='PASS'
    assert Currency.check(state)['current']
    if change=='contract-policy':
        file=repo/'.al/repository.json'
        data=json.loads(file.read_text());data['architecture_check']['env_names'].append('PYTHONPATH')
        file.write_text(json.dumps(data,sort_keys=True))
    elif change=='missing-chain':
        (repo/'architecture-rules.json').unlink()
    else:
        put(repo,'tests/test_architecture.py',GUARD+'\n# source changed; prior bytes cannot certify it\n')
    assert not Currency.check(state)['current']
    assert Gates.candidate_gaps(state)
    from test_service_coverage import discover
    _,current=discover(cli,tmp_path,['tools/pkg.py'])
    coverage=current['service_coverage']
    if change=='contract-policy':
        assert any(g['code']=='contract-changed' for g in coverage['assessment']['gaps'])
    elif change=='missing-chain':
        assert any(g['code']=='unsupported-architecture-chain' for g in coverage['assessment']['gaps'])
    else:
        replacement=next(b for b in coverage['assessment']['required_bindings'] if b['id']=='imports-root')
        assert replacement['execution_signature']!=original['execution_signature']
