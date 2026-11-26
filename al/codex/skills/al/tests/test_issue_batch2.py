"""Current-source boundaries and unavailable remote qualification regressions."""
import al_ci_inventory as CI
import al_repository_audit as Audit
import al_repository_facts as Facts
from conftest import _git
from test_service_coverage import put
from test_remote_github import configured, inventory


def test_ignored_dependency_symlinks_are_excluded_before_inspection(repo):
    put(repo, 'apps/web/package.json', '{"name":"web"}')
    put(repo, 'apps/web/.gitignore', 'node_modules/\n')
    target = repo/'apps/web/node_modules/dependency'
    target.parent.mkdir(parents=True)
    target.symlink_to('../../../packages/shared')
    facts = Facts.collect(repo, base_oid=None, paths=['apps/web'], opaque=[])
    assert not any(g['path'] == 'apps/web/node_modules/dependency' for g in facts['enumeration']['gaps'])


def test_protected_symlinks_and_ignored_source_still_inspected(repo):
    put(repo, 'apps/web/.gitignore', 'node_modules/\nignored/\n')
    put(repo, 'apps/web/ignored/authored.py', 'VALUE=1\n')
    link = repo/'apps/web/node_modules/source.py'; link.parent.mkdir(parents=True)
    link.symlink_to('../ignored/authored.py')
    for paths in [['apps/web/node_modules/source.py']]:
        facts = Facts.collect(repo, base_oid=None, paths=paths, opaque=[])
        assert any(g['code']=='symlink-boundary' and g['path']==paths[0] for g in facts['enumeration']['gaps'])
    _git(repo, 'add', '-f', 'apps/web/node_modules/source.py')
    facts = Facts.collect(repo, base_oid=None, paths=['apps/web'], opaque=[])
    assert any(g['code']=='symlink-boundary' and g['path']==link.relative_to(repo).as_posix() for g in facts['enumeration']['gaps'])
    facts = Facts.collect(repo, base_oid=None, paths=['apps/web/ignored'], opaque=[])
    assert any(r['path']=='apps/web/ignored/authored.py' for r in facts['scan_inputs'])


def test_audit_ignored_worktrees_do_not_displace_current_lead(repo):
    put(repo, '.gitignore', '.worktrees/\n')
    for i in range(25):
        put(repo, f'.worktrees/agent{i}/package.json', '{"name":"stale"}')
        put(repo, f'.worktrees/agent{i}/old.py', 'current_bug = 1\n')
    put(repo, 'current.py', 'current_bug = 2\n')
    put(repo, 'dist/intentional.py', 'current_bug = 3\n')
    _git(repo, 'add', '-f', 'dist/intentional.py')
    result = Audit.audit(repo, 'Repair current_bug')
    assert all(not u['root'].startswith('.worktrees') for u in result['units'])
    assert {r['path'] for r in result['existing_implementation_leads']} >= {'current.py', 'dist/intentional.py'}
    assert result['inventory_complete']


def test_remote_pytest_is_delegated_without_executable_gate(repo):
    configured(repo)
    job = inventory(repo)['obligations'][0]
    assert job['applicability']=='UNRESOLVED'
    assert job['execution'] is None
    assert 'guarded pytest qualification' in job['reason']
    assert CI.pipeline_verified(job)
    assert job['dependencies'] and job['inputs']
    assert job['unavailable_verification']['remote_source']['pytest_guard_status']=='UNAVAILABLE'
    for mutation in [{'advisory': {'reason': 'nonblocking'}}, {'job': '<unresolved-workflow>'}, {'impact': {'inventory_complete': False}}, {'job': 'deploy', 'definition_variants': [{'present': True, 'job': {'stage': 'deploy', 'script': ['deploy.sh']}}]}]:
        assert not CI.pipeline_verified(dict(job, **mutation))


def test_literal_declared_generated_source_keeps_containment(repo):
    put(repo, 'pyproject.toml', '[project]\nname="fixture"\n[tool.setuptools.packages.find]\nwhere=["node_modules/authored"]\n')
    put(repo, '.gitignore', 'node_modules/\n')
    put(repo, 'node_modules/authored/real.py', 'VALUE=1\n')
    (repo/'node_modules/authored/link.py').symlink_to('real.py')
    facts = Facts.collect(repo, base_oid=None, paths=['pyproject.toml'], opaque=[])
    assert any(g['code']=='symlink-boundary' and g['path']=='node_modules/authored/link.py' for g in facts['enumeration']['gaps'])
    assert any(r['path']=='node_modules/authored/real.py' for r in facts['scan_inputs'])
