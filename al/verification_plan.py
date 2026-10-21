"""Complete collections, conservative tiers and independently checked equivalence."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from al.verification_profile import digest, read_json, write_json
from al.verification_isolation import isolation_current, chunk_size

SUITES = {'installer': ('.', 'al/tests'), 'codex': ('al/codex/skills/al', 'tests'), 'claude': ('al/claude/skills/al', 'tests')}
PLATFORM_BOUNDARIES = ('cli', 'execution', 'evidence', 'runtime', 'hook', 'recovery', 'gate', 'review', 'native', 'session', 'config', 'role', 'resource', 'parity', 'consistency', 'docs')


def environment(root, home, *, profile=False, cache_mode='cold'):
    home = Path(home)
    for name in ('tmp', 'home', 'runtime', 'distribution', 'harness'):(home / name).mkdir(parents=True, exist_ok=True)
    allowed = ('PATH', 'SYSTEMROOT', 'COMSPEC', 'LANG', 'LC_ALL', 'SDKROOT')
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update(HOME=str(home / 'home'), USERPROFILE=str(home / 'home'), TMPDIR=str(home / 'tmp'),
               TMP=str(home / 'tmp'), TEMP=str(home / 'tmp'), AL_RUNTIME_DIR=str(home / 'runtime'),
               AL_DISTRIBUTION_STATE=str(home / 'distribution'), HARNESS_HOME=str(home / 'harness'),
               PYTHONPATH=os.pathsep.join(dict.fromkeys([str(Path(root).resolve()), str(Path(__file__).resolve().parents[1])])), PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTEST_ADDOPTS='', AL_PROFILE='1' if profile else '0', AL_ANALYSIS_CACHE='0' if cache_mode == 'disabled' else '1',
               CODEX_HOME=str(home / 'home/.codex'), CLAUDE_CONFIG_DIR=str(home / 'home/.claude'))
    return env


def pytest_argv(cwd):
    return [sys.executable, '-m', 'pytest', '-q', '--rootdir', str(cwd), '-o', 'addopts=', '-p', 'no:cacheprovider', '-p', 'al.verification_pytest']


def collect_suites(root, context):
    root = Path(root).resolve()
    artifact_root = Path(context['artifact_root'])
    collections = {}
    for suite, (relative, tests) in SUITES.items():
        directory = artifact_root / ('collect-' + suite);directory.mkdir(parents=True,mode=0o700)
        report = directory / 'collection.json';log = directory / 'collection.log'
        cwd = root / relative
        argv = pytest_argv(cwd) + ['--collect-only', '--al-collection-report', str(report), tests]
        started = time.monotonic()
        with log.open('wb') as output:
            from al.verification_process import execute
            owner = execute(argv, cwd=cwd, env=environment(root, directory), output=output, directory=directory, assignment='collection:'+suite, timeout=120)
            process = owner.process
        if not report.is_file():raise ValueError('missing collection report: ' + suite + '; log=' + str(log))
        value = read_json(report)
        if value.get('launch_token') != owner.token:raise ValueError('collection launch token mismatch')
        if process.returncode or value.get('exitstatus') != 0 or value.get('errors') or value.get('deselected') or not value.get('nodes') or value.get('finalized') is not True:
            raise ValueError('incomplete/unfiltered collection: ' + suite + '; log=' + str(log))
        if len({n['nodeid'] for n in value['nodes']}) != len(value['nodes']):raise ValueError('duplicate collection identity')
        collections[suite] = {'suite': suite, 'cwd': str(cwd), 'relative': relative, 'nodes': value['nodes'],
                              'config': value['effective_config'], 'pytest_version': value['pytest_version'],
                              'collection_seconds': time.monotonic() - started,
                              'log': str(log), 'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()}
    return collections


def qualified(suite, nodeid):
    return suite + ':' + nodeid


def validate_equivalence(declaration, collections, binding):
    try:
        omitted, executed = declaration['omitted'], declaration['executed']
        a, b = (collections[row['suite']]['nodes_by_id'][row['nodeid']] for row in (omitted, executed))
        if omitted['suite'] == executed['suite'] or {omitted['suite'], executed['suite']} != {'codex', 'claude'}:
            raise ValueError('shared execution must map the two platform suites')
        if any(word in a['nodeid'].lower() for word in PLATFORM_BOUNDARIES):raise ValueError('platform boundary must execute on both')
        if a != b or any(f.get('dynamic') for f in a['fixtures']):raise ValueError('test/parameter/fixture equality not established')
        closure = declaration['closure']
        if not closure or declaration.get('reason', '').strip() == '':raise ValueError('equivalence closure/reason required')
        left, right = binding['files'], binding['files']
        checked = []
        for relative in closure:
            paths = ['al/' + suite + '/skills/al/' + relative for suite in ('codex', 'claude')]
            if left.get(paths[0]) != right.get(paths[1]) or paths[0] not in left:raise ValueError('shared source/resource drift')
            checked.append([relative, left[paths[0]]])
        # Both autouse conftests and the complete implementation/helper tree are mandatory.
        required = {'tests/conftest.py', a['source']['path']}
        required.update(p.split('/skills/al/', 1)[1] for p in binding['files'] if p.startswith('al/codex/skills/al/scripts/'))
        required.update(f['source']['path'] for f in a['fixtures'] if not f['source']['path'].startswith('external:'))
        if not required <= set(closure):raise ValueError('equivalence omits implementation/fixture inputs')
        return {'valid': True, 'omitted': qualified(**omitted), 'executed': qualified(**executed), 'proof': checked, 'proof_digest': digest(checked)}
    except (KeyError, TypeError, ValueError) as exc:
        return {'valid': False, 'reason': str(exc)}


def build_plan(collections, declarations, *, tier, changed_paths, binding):
    if tier not in {'full', 'fast', 'affected'} or declarations.get('schema_version') != 1:
        raise ValueError('invalid verification tier/declarations')
    all_nodes = {}
    for suite, collection in collections.items():
        collection['nodes_by_id'] = {n['nodeid']: n for n in collection['nodes']}
        for node in collection['nodes']:
            all_nodes[qualified(suite, node['nodeid'])] = {'suite': suite, **node}
    if set(collections) != set(SUITES) or not all_nodes:raise ValueError('all three complete collections required')
    selection = set(all_nodes)
    if tier == 'fast':
        patterns = declarations.get('fast', [])
        selection = {q for q in all_nodes if any(fnmatch.fnmatchcase(q, p) for p in patterns)}
        selection.update(q for q in all_nodes if all_nodes[q]['suite'] == 'installer' and any(name in q for name in ('test_verification', 'test_release')))
    elif tier == 'affected':
        mapping = declarations.get('affected', [])
        covered = [row for path in changed_paths for row in mapping if fnmatch.fnmatchcase(path, row['source'])]
        if changed_paths and all(any(fnmatch.fnmatchcase(path, row['source']) for row in mapping) for path in changed_paths):
            patterns = [pattern for row in covered for pattern in row['tests']]
            selection = {q for q in all_nodes if any(fnmatch.fnmatchcase(q, p) for p in patterns)}
        # Unknown/empty change information broadens to all; no absent-mapping exemption.
    # Classification is explicit: newly collected nodes never inherit an omission.
    classified = set(declarations.get('classified_nodes', []))
    selection.update(set(all_nodes) - classified)
    if not selection:raise ValueError('empty verification selection')
    equivalents, rejected, omitted = [], [], set()
    for declaration in declarations.get('shared', []):
        result = validate_equivalence(declaration, collections, binding)
        if not result['valid']:
            rejected.append(result);continue
        a, b = result['omitted'], result['executed']
        if a in omitted or b in omitted or any(e['executed'] == a for e in equivalents):
            raise ValueError('duplicate/chained/cyclic equivalence')
        if a in selection and b in selection:
            omitted.add(a);equivalents.append(result)
    isolation = declarations.get('isolated_files', {})
    assignments = [];group_offsets = {}
    closure_current = isolation_current(declarations, binding)
    for q in sorted(selection - omitted):
        node = all_nodes[q];suite = node['suite'];file = node['source']['path']
        file_key = suite + ':' + file
        conftest = ('al/tests/conftest.py' if suite == 'installer' else 'al/' + suite + '/skills/al/tests/conftest.py')
        expected = {'test': node['source']['sha256'], 'fixtures': binding['files'].get(conftest)}
        isolated = closure_current and isolation.get(file_key) == expected
        size = chunk_size(declarations, suite, file, isolated)
        offset = group_offsets.get(file_key, 0);group_offsets[file_key] = offset + 1
        group = file_key + (':chunk-' + str(offset // size) if size else '')
        assignments.append({'id': q, 'suite': suite, 'node': {k: v for k, v in node.items() if k != 'suite'},
                            'group': group, 'serialization_group': None if isolated else 'unclassified-external-resource'})
    return {'schema_version': 1, 'run_id': uuid.uuid4().hex, 'tier': tier, 'source_binding': binding,
            'environment_binding': {'python': sys.version, 'executable': sys.executable, 'pytest': {s: c['pytest_version'] for s, c in collections.items()},
                                    'isolation_version': 1, 'inherited_pytest_addopts_ignored': bool(os.environ.get('PYTEST_ADDOPTS'))},
            'collection_digest': digest({s: c['nodes'] for s, c in collections.items()}), 'declarations_digest': digest(declarations), 'isolation_closure_current': closure_current,
            'collected_obligations': sorted(all_nodes), 'execution_assignments': assignments, 'shared_equivalents': equivalents,
            'rejected_equivalences': rejected, 'intentionally_unselected': sorted(set(all_nodes) - selection),
            'serialization_groups': sorted({a['serialization_group'] for a in assignments if a['serialization_group']}),
            'complete_required': tier == 'full' and selection == set(all_nodes),
            'collections': {s: {k: v for k, v in c.items() if k not in {'nodes', 'nodes_by_id'}} for s, c in collections.items()}}


def reconcile(plan, reports):
    observed = {}
    errors = []
    for report in reports:
        errors.extend(report.get('errors', []))
        for node, status in report.get('outcomes', {}).items():
            if node in observed:errors.append('duplicate execution: ' + node)
            observed[node] = status
    required = {a['id'] for a in plan['execution_assignments']}
    missing = sorted(required - observed.keys())
    extra = sorted(observed.keys() - required)
    failed = sorted(q for q in required & observed.keys() if observed[q] != 'passed')
    equivalents = [e['omitted'] for e in plan['shared_equivalents'] if observed.get(e['executed']) == 'passed']
    accounted = {q for q, status in observed.items() if status == 'passed'} | set(equivalents)
    unaccounted = sorted(set(plan['collected_obligations']) - accounted - set(plan['intentionally_unselected']))
    return {'complete': plan['complete_required'] and not (missing or extra or failed or unaccounted or errors),
            'selected_success': not (missing or extra or failed or unaccounted or errors),
            'executed_passed': sum(v == 'passed' for v in observed.values()),
            'executed_failed': sum(v == 'failed' for v in observed.values()),
            'executed_errors': sum(v == 'error' for v in observed.values()),
            'skipped': sum(v == 'skipped' for v in observed.values()),
            'xfailed': sum(v == 'xfailed' for v in observed.values()), 'xpassed': sum(v == 'xpassed' for v in observed.values()),
            'executed_outcomes': observed, 'equivalent_obligations': equivalents, 'missing': missing,
            'extra': extra, 'failed': failed, 'unaccounted': unaccounted, 'errors': errors}
