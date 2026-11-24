"""Advisory CI steps are informational, and identical pre-existing failures never block (issue #13)."""
import json

import pytest

import al_ci_advisory as Advisory
import al_gate_signature as Signature
from conftest import _git
from hook_regression_helpers import invoke
from launcher_context import admit_planning
from test_service_coverage import discover, put, seed, start, unit

LIB = '''# shared step helpers
run_advisory() {
  local label="$1"; shift
  echo "== $label (advisory)"
  if ! "$@"; then
    echo "::warning:: $label failed (advisory)" >&2
  fi
}
run_step() {
  local label="$1"; shift
  "$@"
}
record_failure() {
  if ! "$@"; then FAILED=1; fi
}
quietly() { "$@" || true; }
relaxed() {
  set +e
  "$@"
  set -e
  return 0
}
'''
FILES = {'ci/lib.sh': LIB}
NAMES = Advisory.tool_names(['uv', 'run', '--frozen', '--extra', 'dev', 'lint-imports', '--no-logo'])


def classify(text):
    return Advisory.classify([('ci', 'source ci/lib.sh\n' + text, None)], NAMES, FILES.get)


def test_wrappers_that_swallow_failure_are_found_by_their_body_not_their_name():
    assert Advisory.wrappers([Advisory.statements(LIB)]) == {'run_advisory', 'quietly', 'relaxed'}


@pytest.mark.parametrize('line,advisory', [
    ('run_advisory import_linter uv run --frozen lint-imports', True),
    ('quietly uv run lint-imports', True),
    ('relaxed uv run lint-imports', True),
    ('uv run lint-imports || true', True),
    ('uv run lint-imports || :', True),
    ('uv run lint-imports || echo "import contracts broken (advisory)"', True),
    ('uv run lint-imports', False),
    ('run_step import_linter uv run lint-imports', False),
    ('record_failure uv run lint-imports', False),
    ('uv run lint-imports || exit 1', False),
    ('uv run lint-imports || echo broken > failures.txt', False),
])
def test_shell_advisory_wrappers_are_detected_generically(line, advisory):
    result = classify(line)
    assert result['mentions'] and result['advisory'] is advisory


def test_one_blocking_invocation_keeps_the_check_required():
    assert not classify('uv run lint-imports || true\nrun_step imports uv run lint-imports')['advisory']
    assert not classify('uv run ruff check .')['mentions']


def test_helper_function_is_advisory_only_when_every_call_is():
    helper = 'check_imports() { uv run lint-imports; }\n'
    assert classify(helper + 'quietly check_imports\n')['advisory']
    assert not classify(helper + 'quietly check_imports\ncheck_imports\n')['advisory']


def test_scripts_run_by_ci_are_followed_and_bound():
    files = {**FILES, 'ci/fast-check.sh': '#!/bin/sh\nsource ci/lib.sh\nrun_advisory import_linter uv run lint-imports\n'}
    result = Advisory.classify([('job', './ci/fast-check.sh', None)], NAMES, files.get)
    assert result['advisory'] and result['sources'] == ['ci/fast-check.sh', 'ci/lib.sh']
    blocking = Advisory.classify([('job', 'bash ci/fast-check.sh || exit 1', None)], NAMES,
                                 {'ci/fast-check.sh': 'uv run lint-imports\n'}.get)
    assert not blocking['advisory']
    inherited = Advisory.classify([('job', 'bash ci/fast-check.sh', 'GitLab allow_failure')], NAMES,
                                  {'ci/fast-check.sh': 'uv run lint-imports\n'}.get)
    assert inherited['advisory']


def test_gitlab_and_github_allow_failure_semantics():
    assert Advisory.gitlab_job({'allow_failure': True, 'script': ['x']})
    assert 'exit_codes' in Advisory.gitlab_job({'allow_failure': {'exit_codes': [1, 3]}})
    assert Advisory.gitlab_job({'allow_failure': False}) is None and Advisory.gitlab_job({}) is None
    assert Advisory.gitlab_job({'rules': [{'if': '$A', 'allow_failure': True}, {'when': 'never'}]})
    assert Advisory.gitlab_job({'allow_failure': True, 'rules': [{'if': '$A', 'allow_failure': False}]}) is None
    assert Advisory.github_job({'continue-on-error': True}) and Advisory.github_job({'continue-on-error': '${{ x }}'}) is None
    job, removed = Advisory.github_steps({'runs-on': 'ubuntu-latest', 'continue-on-error': False, 'steps': [
        {'uses': 'actions/checkout@v4'}, {'run': 'lint-imports', 'continue-on-error': True}, {'run': 'ruff check .'}]})
    assert removed == ['lint-imports'] and 'continue-on-error' not in job and len(job['steps']) == 2
    entries = Advisory.ci_entries({'obligations': [{'source_path': '.gitlab-ci.yml', 'job': 'j', 'definition_variants': [
        {'job': {'script': ['lint-imports'], 'after_script': ['lint-imports']}}]}]})
    assert [reason is not None for _, _, reason in entries] == [False, True]
    blocking, advisory = Advisory.partition_lines(['ruff check .', 'lint-imports || true', 'a && b || true'])
    assert blocking == ['ruff check .', 'a && b || true'] and advisory == [{'line': 'lint-imports || true', 'command': 'lint-imports'}]


def _log(tmp_path, name, text, code=1, argv=('lint-imports',)):
    path = tmp_path / (name + '.log');path.write_text(text)
    return {'id': name, 'status': 'FAIL', 'exit': code, 'log': str(path), 'argv': list(argv), 'cwd': '.'}


LINT = '''argv: ["uv", "run", "lint-imports"]
cwd: /tmp/al-gate-abc/checkout/services/a
=========
Contracts
=========
Analyzed {files} files, 300 dependencies.
Layers BROKEN
Contracts: 0 kept, 1 broken.
Broken contracts
app.domain is not allowed to import app.api:
- app.domain.model -> app.api.views (l.{line})
'''


def test_candidate_failure_identical_to_base_is_pre_existing(tmp_path):
    base = _log(tmp_path, 'base', LINT.format(files=120, line=12))
    shifted = _log(tmp_path, 'shifted', LINT.format(files=121, line=19).replace('/tmp/al-gate-abc', '/tmp/al-gate-xyz'))
    assert Signature.pre_existing(base, shifted) == 'violations'
    added = _log(tmp_path, 'added', LINT.format(files=120, line=12) + '- app.domain.other -> app.api.views (l.3)\n')
    assert Signature.pre_existing(base, added) is None
    # The same import edge seen again is the same finding (contract plus violating edge).
    twice = _log(tmp_path, 'twice', LINT.format(files=120, line=12) + '- app.domain.model -> app.api.views (l.40)\n')
    assert Signature.pre_existing(base, twice) == 'violations'
    assert Signature.pre_existing(base, _log(tmp_path, 'exit', LINT.format(files=120, line=12), code=2)) is None
    assert Signature.pre_existing(base, _log(tmp_path, 'cmd', LINT.format(files=120, line=12), argv=('other',))) is None
    # Without recognisable findings the exit code and the whole normalised output must match.
    quiet = _log(tmp_path, 'quiet', 'done in 2.1s\n')
    assert Signature.pre_existing(quiet, _log(tmp_path, 'quiet2', 'done in 9.4s\n')) == 'output'
    assert Signature.pre_existing(quiet, _log(tmp_path, 'quiet3', 'something else\n')) is None


# Real import-linter layout: contracts and witness chains come out in a non-deterministic order.
IMPORT_LINTER = """=============
Import Linter
=============

---------
Contracts
---------

Analyzed {files} files, {deps} dependencies.
{rule}

Layers contract BROKEN
Forbidden contract BROKEN
Independence contract KEPT

Contracts: 1 kept, 2 broken.


----------------
Broken contracts
----------------
{sections}"""
LAYERS = """
Layers contract
---------------

app.application is not allowed to import app.infrastructure:
{chains}
"""
FORBIDDEN = """
Forbidden contract
------------------

app.domain is not allowed to import requests:

-   app.domain.client -> requests (l.{line})
"""
CHAINS = [
    "-   app.application.orders -> app.infrastructure.db (l.{line})\n",
    "-   app.application.billing -> app.shared.util (l.3)\n    app.shared.util -> app.infrastructure.queue (l.{line})\n",
]


def _import_linter(files, line, order, extra_chain=''):
    chains = [CHAINS[i].format(line=line) for i in order]
    layers = LAYERS.format(chains='\n'.join(chains + ([extra_chain] if extra_chain else [])))
    sections = [layers, FORBIDDEN.format(line=line)]
    analysed = 'Analyzed {} files, {} dependencies.'.format(files, files * 3)
    return IMPORT_LINTER.format(files=files, deps=files * 3, rule='-' * len(analysed),
                                sections=''.join(sections if order[0] == 0 else sections[::-1]))


def test_import_linter_failure_reordered_with_more_files_is_pre_existing(tmp_path):
    base = _log(tmp_path, 'base', _import_linter(120, 12, [0, 1]))
    reordered = _log(tmp_path, 'reordered', _import_linter(123, 17, [1, 0]))
    assert Signature.pre_existing(base, reordered) == 'violations'
    # Another witness chain for the same violating edge is the same finding.
    witness = _log(tmp_path, 'witness', _import_linter(123, 12, [0, 1]).replace(
        'app.shared.util -> app.infrastructure.queue', 'app.shared.util -> app.shared.other (l.4)\n    app.shared.other -> app.infrastructure.queue'))
    assert Signature.pre_existing(base, witness) == 'violations'
    found, counters = Signature.findings(Signature.lines(reordered))
    assert counters == {'broken': 2}
    assert ('import-linter | Layers contract | app.application is not allowed to import app.infrastructure'
            ' | app.application.billing -> app.infrastructure.queue') in found


def test_import_linter_new_violating_edge_or_broken_contract_blocks(tmp_path):
    base = _log(tmp_path, 'base', _import_linter(120, 12, [0, 1]))
    edge = _log(tmp_path, 'edge', _import_linter(123, 12, [1, 0], '-   app.application.new -> app.infrastructure.db (l.2)\n'))
    assert Signature.pre_existing(base, edge) is None
    more = _log(tmp_path, 'more', _import_linter(120, 12, [0, 1]).replace('1 kept, 2 broken', '0 kept, 3 broken'))
    assert Signature.pre_existing(base, more) is None
    # The same edge in another contract is a different finding.
    moved = _log(tmp_path, 'moved', _import_linter(120, 12, [0, 1]).replace(
        'app.domain.client -> requests', 'app.application.orders -> app.infrastructure.db'))
    assert Signature.pre_existing(base, moved) is None


def test_generic_findings_ignore_order_and_size_counters_but_count_new_occurrences(tmp_path):
    base = _log(tmp_path, 'base', 'Checked 40 files\na.py:3:1: F401 unused\nb.py:9:2: E501 too long\nFound 2 errors.\n', argv=('ruff',))
    moved = _log(tmp_path, 'moved', 'Checked 44 files\nb.py:12:2: E501 too long\na.py:5:1: F401 unused\nFound 2 errors.\n', argv=('ruff',))
    assert Signature.pre_existing(base, moved) == 'violations'
    again = _log(tmp_path, 'again', 'a.py:3:1: F401 unused\na.py:8:1: F401 unused\nb.py:9:2: E501 too long\nFound 3 errors.\n', argv=('ruff',))
    assert Signature.pre_existing(base, again) is None
    counted = _log(tmp_path, 'counted', 'a.py:3:1: F401 unused\nb.py:9:2: E501 too long\nFound 3 errors.\n', argv=('ruff',))
    assert Signature.pre_existing(base, counted) is None


def test_touched_gate_with_identical_lint_failure_is_reported_not_blocking(tmp_path, monkeypatch):
    import al_gates as G
    import al_ledger as L
    runs = {'B': _log(tmp_path, 'B', LINT.format(files=120, line=12)), 'C': _log(tmp_path, 'C', LINT.format(files=120, line=14)),
            'N': _log(tmp_path, 'N', LINT.format(files=120, line=12) + '- app.x -> app.api.y (l.1)\n')}
    monkeypatch.setattr(L, 'find', lambda st, eid: runs.get(eid))
    monkeypatch.setattr(G, 'touched_units', lambda st, m: {'services/a'})
    st = {'gate_runs': [{'gate': 'g', 'stage': 'base', 'manifest': 'm', 'evidence': 'B'}]}
    run, gate = {'fingerprint': 'f', 'manifest': 'm'}, {'id': 'g', 'units': ['services/a']}
    assert 'pre-existing' in G.accepted_failure(st, {}, gate, 'candidate', runs['C'], run, 'f')
    assert G.accepted_failure(st, {}, gate, 'candidate', runs['N'], run, 'f') is None


def test_gate_environment_scrubs_undeclared_installation_switches(monkeypatch):
    import al_gate_process as P
    monkeypatch.delenv('VIRTUAL_ENV', raising=False)
    monkeypatch.setenv('UV_NO_SYNC', '1');monkeypatch.setenv('PIP_NO_INDEX', '1');monkeypatch.setenv('UV_OFFLINE', '1')
    env, removed = P.gate_environment(['UV_OFFLINE'])
    assert removed == ['PIP_NO_INDEX', 'UV_NO_SYNC'] and 'UV_NO_SYNC' not in env and env['UV_OFFLINE'] == '1'
    assert 'PATH' in env


def _advisory_repo(repo, script):
    seed(repo)
    unit(repo, 'services/a', 'import common\n', check=False, extra='[tool.importlinter]\nroot_packages=["app"]\n')
    put(repo, 'ci/lib.sh', LIB)
    put(repo, 'ci/fast-check.sh', '#!/bin/sh\nset -eu\n. ci/lib.sh\n' + script)
    put(repo, '.gitlab-ci.yml', 'fast-check:\n  script:\n    - sh ci/fast-check.sh\n')
    _git(repo, 'add', '.');_git(repo, 'commit', '-qm', 'advisory import check')


def _imports_gate(manifest):
    return next(g for g in manifest['gates'] if g['category'] == 'lint-imports' and g.get('obligation_ids'))


def test_import_check_the_ci_runs_only_as_advisory_is_an_informational_gate(cli, repo, tmp_path, state_file):
    _advisory_repo(repo, 'run_advisory import_linter uv run --frozen lint-imports\n')
    start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    gate = _imports_gate(m)
    assert gate['required'] is False and 'run_advisory' in gate['advisory'] and gate['applicability'] == 'APPLICABLE'
    assert {'ci/fast-check.sh', 'ci/lib.sh'} <= {r['path'] for r in m['rules']}
    path = tmp_path / 'advisory.json';path.write_text(json.dumps(m))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    # No sourced advisory step: the flag cannot be invented for another gate.
    forged = json.loads(path.read_text())
    ci = next(g for g in forged['gates'] if g['category'] == 'ci' and g['applicability'] == 'APPLICABLE')
    ci.update(required=False, advisory='planner says so')
    path.write_text(json.dumps(forged));before = state_file().read_bytes()
    invoke(cli, 'gates', 'plan', '--file', str(path), ok=False)
    assert state_file().read_bytes() == before


def test_import_check_the_ci_runs_blocking_stays_required(cli, repo, tmp_path, state_file):
    _advisory_repo(repo, 'run_step import_linter uv run --frozen lint-imports\n')
    start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    gate = _imports_gate(m)
    assert gate['required'] is True and 'advisory' not in gate
    gate.update(required=False, advisory='forged')
    path = tmp_path / 'forged.json';path.write_text(json.dumps(m));before = state_file().read_bytes()
    invoke(cli, 'gates', 'plan', '--file', str(path), ok=False)
    assert state_file().read_bytes() == before


def test_allow_failure_job_runs_informationally_and_never_needs_a_base_decision(cli, repo, tmp_path):
    seed(repo)
    put(repo, '.gitlab-ci.yml', 'advisory-check:\n  allow_failure: true\n  script:\n    - python3 -c "raise SystemExit(3)"\n')
    _git(repo, 'add', '.');_git(repo, 'commit', '-qm', 'advisory job')
    start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    gate = next(g for g in m['gates'] if g['category'] == 'ci' and g['source']['job'] == 'advisory-check')
    assert gate['required'] is False and 'allow_failure' in gate['advisory']
    path = tmp_path / 'plan.json';path.write_text(json.dumps(m))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    _, out, _ = invoke(cli, 'gates', 'run', '--stage', 'base')
    assert 'does not block: advisory in CI' in out
    status = json.loads(cli('gates', 'status')[1])
    assert not any(gate['id'] in g for g in status['base'])
    assert any(row['gate'] == gate['id'] and row.get('advisory') for row in status['unverified'])


def test_identical_pre_existing_candidate_failure_does_not_block(cli, repo, tmp_path):
    seed(repo)
    put(repo, 'services/a/src/app.py', 'import common\nimport forbidden_dependency\n')
    _git(repo, 'add', '.');_git(repo, 'commit', '-qm', 'pre-existing violation')
    start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    path = tmp_path / 'plan.json';path.write_text(json.dumps(m))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    invoke(cli, 'gates', 'run', '--stage', 'base', ok=False)
    gate = next(g for g in m['gates'] if g['category'] == 'lint-imports' and 'services/a' in g['units'] and g.get('obligation_ids'))
    invoke(cli, 'gates', 'decision', '--id', gate['id'], '--action', 'exception', '--source',
           'user: the module ban already fails on main', '--reason', 'pre-existing violation; repair tracked outside this task')
    admit_planning(cli);invoke(cli, 'phase', 'IMPLEMENTATION')
    invoke(cli, 'writer', 'start', '--id', 'owned', '--role', 'al-engineer', '--files', 'services/a/src/app.py')
    put(repo, 'services/a/src/app.py', 'import common\nimport forbidden_dependency\nVALUE = 2\n')
    invoke(cli, 'writer', 'finish', '--id', 'owned', '--outcome', 'success')
    invoke(cli, 'phase', 'VERIFICATION')
    _, out, _ = invoke(cli, 'gates', 'run', '--stage', 'candidate')
    assert 'pre-existing failure, identical at base' in out
    status = json.loads(cli('gates', 'status')[1])
    assert not status['candidate']
    note = next(row for row in status['unverified'] if row['gate'] == gate['id'] and row['stage'] == 'candidate')
    assert 'pre-existing' in note['reason'] and 'baseline exception' in note['reason']


def test_candidate_stage_runs_every_gate_after_a_blocking_failure(cli, repo, tmp_path):
    seed(repo);start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py', 'services/b/src/app.py'])
    path = tmp_path / 'plan.json';path.write_text(json.dumps(m))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    admit_planning(cli);invoke(cli, 'phase', 'IMPLEMENTATION')
    invoke(cli, 'writer', 'start', '--id', 'owned', '--role', 'al-engineer', '--files', 'services/a/src/app.py')
    put(repo, 'services/a/src/app.py', 'import common\nimport forbidden_dependency\n')
    invoke(cli, 'writer', 'finish', '--id', 'owned', '--outcome', 'success')
    invoke(cli, 'phase', 'VERIFICATION')
    _, out, _ = invoke(cli, 'gates', 'run', '--stage', 'candidate', ok=False)
    ran = [g['id'] for g in m['gates'] if g['applicability'] == 'APPLICABLE']
    blocking = next(g['id'] for g in m['gates'] if g['category'] == 'lint-imports' and 'services/a' in g['units'] and g.get('obligation_ids'))
    assert ran.index(blocking) < len(ran) - 1, 'the blocking gate must not be the last one'
    assert 'gate(s) did not pass: ' + blocking in out
    for gate in ran:
        assert 'gate-candidate-' + gate in out, gate + ' did not run after the blocking failure'


def test_verification_only_route_is_named_and_needs_no_writer(cli, repo, tmp_path):
    seed(repo);start(cli)
    _, m = discover(cli, tmp_path, ['services/a/src/app.py'])
    path = tmp_path / 'plan.json';path.write_text(json.dumps(m))
    invoke(cli, 'gates', 'plan', '--file', str(path))
    invoke(cli, 'gates', 'run', '--stage', 'base')
    admit_planning(cli)
    code, out, err = invoke(cli, 'phase', 'VERIFICATION', ok=False)
    assert 'PLANNING → IMPLEMENTATION → VERIFICATION' in str(code) + out + err
    put(repo, 'services/a/src/app.py', 'import common\nVALUE = 3\n')
    invoke(cli, 'phase', 'IMPLEMENTATION')
    invoke(cli, 'phase', 'VERIFICATION')
    assert json.loads(cli('show')[1])['phase'] == 'VERIFICATION'
