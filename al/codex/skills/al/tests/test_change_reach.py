"""A change in one workspace member is judged by what it reaches (issue #38, regression of #13/#15).

Shape of the report: a polyglot workspace (Python services plus Node apps), a change in one
app and one runbook, every service carrying an import-architecture check that already fails
on base, and unreached members without checks. Base failures are the baseline for the
pre-existing comparison, never a blocker; coverage gaps of members the change does not reach
are verification notes, even when consumer discovery matched a shared file name in them.
"""
import json

import al_gates as G
import al_ledger as L
import al_service_coverage as Coverage
from conftest import _git
from test_service_coverage import put

SERVICES = ('a', 'b', 'c')
CHANGED = ['apps/web/src/index.ts', 'docs/runbook.md']


def _workspace(repo, reference='DOC = "runbook.md"\n'):
    """uv workspace of services, pnpm workspace of apps; every service names the changed runbook by file name only."""
    for stale in ('pyproject.toml', 'svc/app.py', 'web/app.js'):
        (repo / stale).unlink(missing_ok=True)
    put(repo, 'pyproject.toml', '[project]\nname="root"\nversion="0"\n[tool.uv.workspace]\nmembers=["services/*"]\n')
    put(repo, 'uv.lock', 'version = 1\n')
    for s in SERVICES:
        put(repo, f'services/{s}/pyproject.toml',
            f'[project]\nname="svc-{s}"\nversion="0"\ndependencies=[]\n[tool.importlinter]\nroot_package="svc_{s}"\n'
            f'[[tool.importlinter.contracts]]\nname="layers"\ntype="layers"\nlayers=["svc_{s}.api","svc_{s}.core"]\n')
        put(repo, f'services/{s}/src/svc_{s}/__init__.py', '')
        # A shared file name, not a reference to the changed file: consumer discovery matches it.
        put(repo, f'services/{s}/src/svc_{s}/refs.py', reference)
    put(repo, 'package.json', '{"name":"root","private":true,"packageManager":"pnpm@9.0.0","scripts":{}}\n')
    put(repo, 'pnpm-workspace.yaml', 'packages:\n  - "apps/*"\n')
    put(repo, 'pnpm-lock.yaml', "lockfileVersion: '9.0'\nimporters:\n  .: {}\n  apps/web: {}\n  apps/admin: {}\n")
    for app in ('web', 'admin'):
        put(repo, f'apps/{app}/package.json',
            json.dumps({'name': app, 'private': True, 'scripts': {'lint': 'eslint .', 'test': 'vitest run'}}) + '\n')
        put(repo, f'apps/{app}/src/index.ts', 'export const x = 1\n')
    put(repo, 'docs/runbook.md', '# runbook\n')
    _git(repo, 'add', '-A');_git(repo, 'commit', '-qm', 'polyglot workspace')
    put(repo, 'apps/web/src/index.ts', 'export const x = 2\n')


def _assess(repo):
    import al_repository_facts as Facts
    facts = Facts.collect(repo, base_oid=None, paths=CHANGED, opaque=[])
    return facts, Coverage.assess(facts)


def _subjects(rows):
    return {g.get('unit') or g.get('path') for g in rows}


def test_unreached_members_gaps_are_notes_even_when_a_shared_file_name_selects_their_checks(repo):
    _workspace(repo)
    facts, assessment = _assess(repo)
    services = {'services/' + s for s in SERVICES}
    # Name-only consumers still select the services' checks (they run and are compared) ...
    assert services <= set(facts['dependencies']['known_affected'])
    assert services <= set(assessment['selected_services'])
    # ... but only literal references reach the change, so their coverage gaps are notes.
    assert not set(facts['dependencies']['change_reach']['units']) & services
    blocking, notes = _subjects(assessment['gaps']), _subjects(assessment['coverage_notes'])
    assert not blocking & (services | {'apps/admin'}), blocking
    assert services | {'apps/admin'} <= notes
    assert blocking <= {'.', 'apps/web'}
    rendered = Coverage.notes({'assessment': assessment})
    assert any('services/a' in n for n in rendered)
    outside = [n for n in rendered if any(s in n for s in services)]
    assert outside and all(n.startswith("coverage note (outside the change's reach") for n in outside)
    # In-scope gaps that only mean AL could not establish a check are unverified notes too (#157).
    assert all(n.startswith(("coverage note (outside the change's reach", 'coverage unverified (in the change'))
               for n in rendered)


def test_a_literal_path_reference_still_reaches_the_member(repo):
    _workspace(repo, reference='RUNBOOK = "docs/runbook.md"\n')
    facts, assessment = _assess(repo)
    assert {'services/' + s for s in SERVICES} <= set(facts['dependencies']['change_reach']['units'])
    assert 'services/a' in _subjects(assessment['gaps'])


def _run(tmp_path, monkeypatch, gate, stage, status, **extra):
    log = tmp_path / (stage + '.log');log.write_text('Contracts: 0 kept, 1 broken.\n- svc_b.core -> svc_b.api (l.3)\n')
    entry = {'id': 'E-' + stage, 'status': status, 'exit': 1, 'log': str(log), 'log_sha256': L.file_sha(log),
             'env': [], 'run': 'run-1', 'argv': gate['argv'], 'cwd': gate['cwd'], 'scope': gate['scope'], 'sources': [],
             **extra}
    run = {'gate': gate['id'], 'stage': stage, 'manifest': 'M', 'fingerprint': 'BASE' if stage == 'base' else 'CAND',
           'evidence': entry['id'], 'execution_kind': 'local', 'env_fingerprint': L.scope_fingerprint([], []), 'sources': []}
    return entry, run


def _state(tmp_path, monkeypatch, runs, touched=('apps/web',)):
    entries = {e['id']: e for e, _ in runs}
    monkeypatch.setattr(L, 'find', lambda st, eid: entries.get(eid))
    monkeypatch.setattr(L, 'entry_verdict', lambda st, e: ('FAIL' if e['status'] == 'FAIL' else 'VALID', ''))
    monkeypatch.setattr(G.Exceptions, 'current', lambda st, m, g: None)
    monkeypatch.setattr(G, 'touched_units', lambda st, m: set(touched))
    return {'run_id': 'run-1', 'gate_manifests': [{'sha256': 'M'}], 'gate_decisions': {}, 'gate_runs': [r for _, r in runs]}


GATE = {'id': 'imports-services-b', 'category': 'lint-imports', 'units': ['services/b'], 'argv': ['uv', 'run', 'lint-imports'],
        'cwd': 'services/b', 'scope': ['services/b'], 'env_names': [], 'sources': [], 'execution_kind': 'local',
        'applicability': 'APPLICABLE', 'required': True}


def test_a_base_failure_is_the_baseline_and_an_identical_candidate_failure_is_pre_existing(tmp_path, monkeypatch):
    base = _run(tmp_path, monkeypatch, GATE, 'base', 'FAIL')
    st = _state(tmp_path, monkeypatch, [base])
    # The base stage no longer asks for a decision: the failure is the comparison point.
    assert G.gate_result(st, {'base_oid': 'BASE'}, GATE, 'base', 'BASE') in G.ACCEPTED
    assert 'baseline failure' in G.accepted_failure(st, {'base_oid': 'BASE'}, GATE, 'base', base[0], base[1], 'BASE')
    # The same gate failing again at candidate, in a member the change does not touch, is pre-existing.
    candidate = _run(tmp_path, monkeypatch, GATE, 'candidate', 'FAIL')
    st = _state(tmp_path, monkeypatch, [base, candidate])
    assert 'pre-existing' in G.accepted_failure(st, {'base_oid': 'BASE'}, GATE, 'candidate', candidate[0], candidate[1], 'CAND')


def test_only_a_finished_base_failure_of_this_runs_command_is_the_baseline(tmp_path, monkeypatch):
    for status, extra in (('INFRA', {}), ('TORN', {}), ('FAIL', {'run': 'older-run'}), ('FAIL', {'argv': ['other']})):
        base = _run(tmp_path, monkeypatch, GATE, 'base', status, **extra)
        st = _state(tmp_path, monkeypatch, [base])
        assert G.accepted_failure(st, {'base_oid': 'BASE'}, GATE, 'base', base[0], base[1], 'BASE') is None, (status, extra)
        assert G.gate_result(st, {'base_oid': 'BASE'}, GATE, 'base', 'BASE') not in G.ACCEPTED, (status, extra)


def test_a_new_candidate_failure_still_blocks(tmp_path, monkeypatch):
    # The base passed: the candidate failure is new, whatever unit it belongs to.
    base = _run(tmp_path, monkeypatch, GATE, 'base', 'PASS')
    candidate = _run(tmp_path, monkeypatch, GATE, 'candidate', 'FAIL')
    st = _state(tmp_path, monkeypatch, [base, candidate])
    assert G.accepted_failure(st, {'base_oid': 'BASE'}, GATE, 'candidate', candidate[0], candidate[1], 'CAND') is None



def comparison_fixture(gate, entries, base_id):
    """Real retained log/ledger bindings for pure base/candidate comparison controls."""
    gate = dict(GATE, **gate)
    gate['scope'] = list(gate['units'])
    manifest = {'base_oid': 'BASE'}
    state = {'run_id': 'comparison-run', 'gate_manifests': [{'sha256': 'M'}],
             'gate_decisions': {}, 'gate_runs': [],
             'evidence': {'seq': len(entries), 'entries': [], 'plan': {}}}
    records = {}
    for identifier, entry in entries.items():
        stage = 'base' if identifier == base_id else 'candidate'
        entry = dict(entry, run=state['run_id'], argv=gate['argv'], cwd=gate['cwd'],
                     scope=gate['scope'], sources=gate['sources'], env=sorted(gate['env_names']),
                     log_sha256=L.file_sha(entry['log']), kind='check',
                     area='gate-'+stage+'-'+gate['id'], gate_stage=stage, gate_manifest='M')
        record = {'gate': gate['id'], 'stage': stage, 'manifest': 'M',
                  'fingerprint': 'BASE' if stage == 'base' else 'CAND', 'evidence': identifier,
                  'execution_kind': gate['execution_kind'], 'sources': gate['sources'],
                  'env_fingerprint': L.scope_fingerprint([], gate['env_names'])}
        state['evidence']['entries'].append(entry);state['gate_runs'].append(record)
        records[identifier] = record
    return state, manifest, gate, {e['id']: e for e in state['evidence']['entries']}, records
