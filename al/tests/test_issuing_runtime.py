"""Real old public authority must satisfy the full installed current validator."""
import json
import shutil
import pytest
from al.tests.old_issuer_workflow import Workflow
from al.tests.old_issuer_negatives import before_approval,terminal_refusals


@pytest.mark.parametrize('platform',['codex','claude'])
def test_old_public_issuer_replans_and_new_installed_validator_survives_removal(tmp_path,platform):
    workflow=Workflow(tmp_path,platform)
    workflow.verification()
    initial=workflow.state()
    assert 'service_coverage' not in json.loads(workflow.original_raw)
    assert initial['phase']=='VERIFICATION'
    for declaration in ('.pre-commit-config.yaml','Makefile'):
        path=workflow.repo/declaration;path.write_text('declared required check\n')
        state=(workflow.runtime/(workflow.session+'.json')).read_bytes();index=(workflow.repo/'.git/index').read_bytes()
        try:
            refused=workflow.proof('replan-proof',allow=True)
            assert refused.returncode!=0 and 'unsupported declared check interface' in refused.stdout,refused.stdout[-2000:]
            assert (workflow.runtime/(workflow.session+'.json')).read_bytes()==state and (workflow.repo/'.git/index').read_bytes()==index
        finally:path.unlink()
    replacement=workflow.replacement()
    assert 'actual-pytest' in replacement['retained_broad_plan_binding']
    assert replacement['budget_binding']['rework']==initial['rework']
    before_approval(workflow)
    workflow.approve()
    terminal_refusals(workflow)
    before=workflow.terminal('before-removal')
    shutil.rmtree(workflow.issuer_home)
    after=workflow.terminal('after-removal')
    assert before['source']==after['source'] and before['approval']==after['approval']
    final=workflow.state()
    assert final['rework']==initial['rework'] and final['retries']==initial['retries']
    assert all(p in final['phase_occurrences'] for p in replacement['completed_phases'])
    assert workflow.original_raw==open(workflow.original_ref['stored_path'],'rb').read()


@pytest.mark.parametrize('platform',['codex','claude'])
def test_original_old_terminal_approval_without_coverage_stays_blocked(tmp_path,platform):
    workflow=Workflow(tmp_path,platform);workflow.verification()
    workflow.cli('gates','run','--stage','candidate');workflow.broad();workflow.approve()
    assert 'service_coverage' not in json.loads(workflow.original_raw)
    path=workflow.runtime/(workflow.session+'.json');before=path.read_bytes();index=(workflow.repo/'.git/index').read_bytes()
    refused=workflow.candidate('source-proof',allow=True)
    assert refused.returncode!=0 and 'coverage' in refused.stdout.lower(),refused.stdout[-2500:]
    assert path.read_bytes()==before and (workflow.repo/'.git/index').read_bytes()==index
