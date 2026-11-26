"""Direct regressions for workspace membership, stopping recovery and literal Hatch layouts."""
import copy
import hashlib
import json
import subprocess
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
import al_evidence as Evidence
import al_gates as Gates
import al_ledger as Ledger
import al_package_checks as Package
import al_recovery as Recovery
import al_recovery_subjects as Subjects
import al_repository_facts as Facts
import al_repo_python as Python


class Files(dict):
    @property
    def records(self):
        return {p: {'sha256': hashlib.sha256(v).hexdigest()} for p, v in self.items()}


def scan(tmp_path, files):
    files = Files({p: v.encode() for p, v in files.items()})
    members = set(files)
    for p in files:
        members.update(str(a) + '/' for a in PurePosixPath(p).parents if str(a) != '.')
    result = SimpleNamespace(root=tmp_path, files=files, members=members, rules=set(), gaps=[])
    def text(p, **kw):
        if kw.get('rule') and p in files:result.rules.add(p)
        return files.get(p, b'').decode()
    result.text = text
    return result


def workspace(tmp_path, patterns, importers=True):
    return scan(tmp_path, {
        'package.json': '{"name":"root","packageManager":"pnpm@10.0.0"}',
        'pnpm-workspace.yaml': 'packages: ' + json.dumps(patterns),
        'pnpm-lock.yaml': 'importers:\n  apps/web: {}\n' if importers else 'importers: {}',
        'apps/web/package.json': '{"name":"web"}',
        'packages/python/pyproject.toml': '[project]\nname="python"',
        'services/go/go.mod': 'module example.test/go',
    })


def test_workspace_globs_skip_non_js_manifests_and_deduplicate(tmp_path):
    s = workspace(tmp_path, ['apps/*', 'apps/**', 'packages/**', 'services/*'])
    row, reasons = Package.manager(s, 'apps/web')
    assert reasons == []
    assert set(row['members']) == {'.', 'apps/web'}
    paths = [r['path'] for r in row['inputs']]
    assert paths.count('apps/web/package.json') == 1
    assert 'pnpm-workspace.yaml' in s.rules


@pytest.mark.parametrize('patterns,importers,reason', [
    (['apps/*', 'packages/python'], True, 'declared workspace member manifest unavailable'),
    (['apps/*'], False, 'member absent from governing pnpm lock'),
    (['apps/*', '!apps/hidden'], True, 'unsupported workspace selection closure'),
])
def test_workspace_literal_missing_lock_and_unsupported_stay_errors(tmp_path, patterns, importers, reason):
    _, reasons = Package.manager(workspace(tmp_path, patterns, importers), 'apps/web')
    assert reason in reasons


def gate_stop(tmp_path, monkeypatch, cli, run_state, decision):
    run_state('implemented')
    st = cli.state()
    log = tmp_path / 'base.log'; log.write_text('baseline failure')
    entry = {'id': 'E-test', 'status': 'FAIL', 'log': str(log), 'log_sha256': Ledger.file_sha(log)}
    gate = {'id': 'baseline', 'env_names': []}
    manifest = {'gates': [gate], 'base_oid': 'base'}
    st['gate_runs'] = [{'gate': 'baseline', 'stage': 'base', 'manifest': st['gate_manifests'][-1]['sha256'],
                        'evidence': entry['id'], 'env_fingerprint': Ledger.scope_fingerprint([], [])}]
    monkeypatch.setattr(Gates, 'manifest', lambda st: manifest)
    monkeypatch.setattr(Ledger, 'find', lambda st, eid: entry)
    monkeypatch.setattr(Gates.al_store, 'load', lambda sid: st)
    monkeypatch.setattr(Gates.al_store, 'save', lambda sid, st: None)
    args = SimpleNamespace(action='decision', id='baseline', decision=decision, source='user test', reason='pause')
    Gates.cmd_gates(args, Recovery.al_store.CURRENT.read_text().strip())
    return st


@pytest.mark.parametrize('decision', ['stop', 'defer'])
def test_gate_stop_has_bound_checkpoint_and_public_resume(tmp_path, monkeypatch, cli, run_state, decision, capsys):
    st = gate_stop(tmp_path, monkeypatch, cli, run_state, decision)
    checkpoint = st.get('stopping_checkpoint')
    assert checkpoint and st['checkpoints'][-1]['id'] == checkpoint
    assert st['phase_occurrences'][-1]['ended'] is not None
    recovery = Subjects.issues(st)[0]
    assert recovery['code'] == 'GATE_DECISION_STOPPED'
    assert checkpoint in json.dumps(recovery)
    assert 'resume --checkpoint ' + checkpoint in capsys.readouterr().out
    args = SimpleNamespace(cmd='resume', checkpoint=checkpoint)
    sid = Recovery.al_store.CURRENT.read_text().strip()
    with pytest.raises(SystemExit, match='latest stopping checkpoint'):
        Recovery.cmd_checkpoint(SimpleNamespace(cmd='resume', checkpoint='stale'), sid)
    clean = copy.deepcopy(st)
    for mutation, message in [
        (lambda: st.update(failure={'pending': True}), 'pending agent failure'),
        (lambda: st['writers'].update(w={'id': 'w', 'state': 'ACTIVE'}), 'outstanding operations'),
        (lambda: st['evidence']['entries'].append({'id': 'E-live', 'status': 'RUNNING'}), 'outstanding operations'),
        (lambda: st['gate_decisions']['baseline'].update(reason='foreign'), 'not resumably stopped'),
        (lambda: st.update(identity={'foreign': True}), 'not resumably stopped'),
        (lambda: st.update(rework_recovery={'stop': {'kind': 'cap'}}), 'not resumably stopped'),
        (lambda: st.pop('stopping_checkpoint'), 'latest stopping checkpoint'),
    ]:
        mutation()
        with pytest.raises(SystemExit, match=message):
            Recovery.cmd_checkpoint(args, sid)
        st.clear(); st.update(copy.deepcopy(clean))
    Recovery.cmd_checkpoint(args, sid)
    assert st['status'] == 'RUNNING' and st['resumes'][-1]['checkpoint'] == checkpoint


def test_cleanup_permission_guidance_is_specific_and_preserves_uncertainty():
    cleanup = {'errors': ["[Errno 1] Operation not permitted: '/bin/ps'"], 'complete': False, 'unresolved': []}
    guidance = Evidence.process_inspection_guidance(cleanup)
    assert 'require_escalated' in guidance and 'same protected evidence' in guidance
    assert 'positive' in guidance and 'INFRA' in guidance
    assert Evidence.process_inspection_guidance({'errors': ['process still alive']}) == ''
    st = {'writers': {}, 'evidence': {'entries': [{'id': 'E1', 'status': 'INFRA', 'cleanup': cleanup}]}}
    assert 'require_escalated' in json.dumps(Subjects.operations(st))
    assert st['evidence']['entries'][0]['status'] == 'INFRA'


def hatch(tmp_path, packages, extra=''):
    config = '[project]\nname="example"\nversion="0"\n[build-system]\nbuild-backend="hatchling.build"\n[tool.hatch.build.targets.wheel]\npackages=' + packages + '\n' + extra
    return scan(tmp_path, {'pyproject.toml': config, 'example/__init__.py': '', 'src/example/__init__.py': '',
                           'src/sibling/__init__.py': '', 'tests/test_x.py': ''})


@pytest.mark.parametrize('package,root', [('example', '.'), ('src/example', 'src')])
def test_literal_hatch_packages_have_import_roots_and_selection(tmp_path, package, root):
    s = hatch(tmp_path, json.dumps([package])); facts = Python.observation(s)
    unit = Python.add_unit(s, facts, '.')
    assert not facts['enumeration']['gaps']
    assert unit['source_roots'] == [root]
    assert unit['python_packages'] == [package]
    assert not any(Facts.within(e['path'], package) for e in facts['enumeration']['exclusions'])
    assert 'pyproject.toml' in s.rules


@pytest.mark.parametrize('packages,extra', [
    ('"example"', ''), ('[]', ''), ('[1]', ''), ('["../escape"]', ''),
    ('["/absolute"]', ''), ('["src/$dynamic"]', ''), ('["example","example"]', ''),
    ('["src/*"]', ''), ('["missing"]', ''), ('["src/example"]', 'sources={"src"=""}'),
    ('["src/example"]', '[tool.hatch.build.hooks.custom]\npath="hook.py"'),
])
def test_unsupported_hatch_settings_stay_named_gaps(tmp_path, packages, extra):
    s = hatch(tmp_path, packages, extra); facts = Python.observation(s)
    Python.add_unit(s, facts, '.')
    assert any('hatch' in g['code'] for g in facts['enumeration']['gaps'])


def test_hatch_module_identity_consumers_and_declaration_currency(repo):
    from test_service_coverage import put
    declaration = '[project]\nname="example"\nversion="0"\n[build-system]\nbuild-backend="hatchling.build"\n[tool.hatch.build.targets.wheel]\npackages=["src/example"]\n'
    put(repo, 'pyproject.toml', declaration)
    put(repo, 'src/example/__init__.py', '')
    put(repo, 'src/example/core.py', 'VALUE = 1\n')
    put(repo, 'src/sibling/__init__.py', '')
    put(repo, 'services/client/pyproject.toml', '[project]\nname="client"\nversion="0"\n[tool.uv.sources]\nexample={path="../.."}\n')
    put(repo, 'services/client/src/client/__init__.py', 'from example.core import VALUE\n')
    from conftest import _git
    _git(repo, 'add', '.'); _git(repo, 'commit', '-qm', 'literal package fixture')
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo).decode().strip()
    put(repo, 'src/example/core.py', 'VALUE = 2\n')
    facts = Facts.collect(repo, base_oid=base, paths=['src/example/core.py'], opaque=[])
    membership = facts['dependencies']['source_membership']
    assert ['current', 'src/example/core.py', '.', 'example.core'] in membership
    assert ['base', 'src/example/core.py', '.', 'example.core'] in membership
    assert not any(row[1].startswith('src/sibling/') for row in membership)
    assert ['services/client/src/client/__init__.py', 'src/example/core.py'] in facts['dependencies']['edges']
    assert 'services/client' in facts['dependencies']['known_affected']
    source = next(r for r in facts['rule_sources'] if r['path'] == 'pyproject.toml')
    put(repo, 'pyproject.toml', declaration.replace('src/example', 'src/sibling'))
    changed = Facts.collect(repo, base_oid=None, paths=['src/example/core.py'], opaque=[])
    assert source != next(r for r in changed['rule_sources'] if r['path'] == 'pyproject.toml')
    assert not any(row[1].startswith('src/example/') for row in changed['dependencies']['source_membership'])
