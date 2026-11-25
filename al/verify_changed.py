#!/usr/bin/env python3
"""Verify a development change: run the AL tests it affects, in parallel, on both hosts.

    uv run python al/verify_changed.py                 # changes since merge-base with main (+ uncommitted + untracked)
    uv run python al/verify_changed.py --base HEAD~3   # changes since another ref
    uv run python al/verify_changed.py --path al/codex/skills/al/scripts/al_gates.py
    uv run python al/verify_changed.py --all           # every collected test (development run, no release claim)
    uv run python al/verify_changed.py --list          # print the selection and exit

The selection comes from al/verification_affected.py (imports, file-name references,
the test_<name>/al_<name> convention, structural guards, and complete-suite fallbacks).
Tests run through the release runner (al/verification_runner.py): one isolated pytest
process per group of at most --split tests of one file, with private HOME/TMP/runtime
state, at most --workers at once.
This is a development check. It never makes a release claim; release_verify.py --full
remains the gate before tagging. Elapsed time is reported, never judged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from al.verification_affected import SUITES, select  # noqa: E402

FAILURE_LINES = 80


def _git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError('git ' + ' '.join(args) + ' failed: ' + result.stderr.strip())
    return result.stdout


def changed_files(root, base=None):
    """Committed changes since merge-base(HEAD, base) plus staged, unstaged and untracked files."""
    candidates = [base] if base else ['main', 'origin/main', 'HEAD']
    for ref in candidates:
        try:
            start = _git(root, 'merge-base', 'HEAD', ref).strip()
            break
        except ValueError:
            if base:
                raise ValueError('cannot resolve --base ' + base + ' against HEAD')
    else:
        raise ValueError('no base found; pass --base REF or --path FILE')
    paths = set(_git(root, 'diff', '--name-only', '--no-renames', start).splitlines())
    paths.update(_git(root, 'ls-files', '--others', '--exclude-standard').splitlines())
    return start, sorted(paths)


def _plan(root, artifact_root, selection, *, strict_isolation, hosts, split=0):
    from al.verification_plan import build_plan, collect_suites
    from al.verification_profile import capture_source_binding, read_json
    binding = capture_source_binding(root)
    collections = collect_suites(root, {'artifact_root': str(artifact_root)})
    obligations = Path(root) / 'al/verification-obligations.json'
    declarations = read_json(obligations) if obligations.is_file() else {'schema_version': 1}
    plan = build_plan(collections, declarations, tier='full', changed_paths=[], binding=binding)
    wanted = {(suite, path) for suite, paths in selection.items() if suite in hosts for path in paths}
    candidates = list(plan['execution_assignments'])
    if declarations.get('acceptance'):
        # Long end-to-end proofs are a separate release tier; a change that selects them runs them here.
        candidates += build_plan(collections, declarations, tier='acceptance', changed_paths=[], binding=binding)['execution_assignments']
    assignments = [a for a in candidates if (a['suite'], a['node']['source']['path']) in wanted]
    if split:
        # Smaller node groups of one file run in separate fresh processes: session/module fixtures are
        # rebuilt per group (more isolation, never less), and one long file no longer bounds the wall time.
        offsets = {}
        for assignment in assignments:
            key = assignment['suite'] + ':' + assignment['node']['source']['path']
            offset = offsets.get(key, 0);offsets[key] = offset + 1
            assignment['group'] = key + ':dev-chunk-' + str(offset // split)
    if not strict_isolation:
        # Every shard already runs in its own process with private HOME, TMP, runtime and
        # harness state. The release gate additionally serializes files whose isolation
        # review predates the current source; a development check runs them concurrently.
        for assignment in assignments:
            assignment['serialization_group'] = None
    selected = {a['id'] for a in assignments}
    plan.update(execution_assignments=assignments, tier='development-changed', complete_required=False,
                shared_equivalents=[], intentionally_unselected=sorted(set(plan['collected_obligations']) - selected),
                serialization_groups=sorted({a['serialization_group'] for a in assignments if a['serialization_group']}))
    return plan, binding


def _failure_excerpt(log, limit=FAILURE_LINES):
    try:
        lines = Path(log).read_text(errors='replace').splitlines()
    except OSError:
        return ['(log unavailable: ' + str(log) + ')']
    start = next((i for i, line in enumerate(lines) if '= FAILURES =' in line or '= ERRORS =' in line), None)
    body = lines[start:] if start is not None else lines[-limit:]
    if len(body) > limit:
        summary = [line for line in body if line.startswith(('FAILED ', 'ERROR '))]
        body = body[:limit - len(summary) - 1] + ['... (truncated; see log)'] + summary
    return body


TIERS = 'al/verification-tiers.json'
# Structural guards: byte parity of the host copies, consistency and docs. Fast, so every change runs them.
GUARDS = ('test_parity.py', 'test_consistency.py', 'test_docs.py')
SLOW_MEAN_SECONDS = 1.0


def slow_files(root):
    """{suite: set(paths)} of the slow tier: measured heavy files and the acceptance tier."""
    import fnmatch
    tiers = Path(root) / TIERS
    slow = {suite: set() for suite in SUITES}
    try:
        value = json.loads(tiers.read_text())
    except (OSError, ValueError):
        value = {}
    for key in value.get('slow', {}):
        suite, path = key.split(':', 1)
        slow.setdefault(suite, set()).add(path)
    obligations = Path(root) / 'al/verification-obligations.json'
    try:
        acceptance = json.loads(obligations.read_text()).get('acceptance', [])
    except (OSError, ValueError):
        acceptance = []
    for pattern in acceptance:
        suite, rest = pattern.split(':', 1)
        path = rest.split('::', 1)[0]
        if '*' not in path:
            slow.setdefault(suite, set()).add(path)
    return slow


def fast_tier(root, selection, hosts):
    """Drop slow-tier files from the selection and add the guards; return (selection, deferred count)."""
    slow = slow_files(root)
    result, deferred = {}, 0
    for suite, paths in selection.items():
        kept = [path for path in paths if path not in slow.get(suite, set())]
        deferred += len(paths) - len(kept)
        result[suite] = kept
    for suite in hosts - {'installer'}:
        base = Path(root) / SUITES[suite]
        guards = ['tests/' + name for name in GUARDS if (base / name).is_file()]
        result[suite] = sorted(set(result.get(suite, [])) | set(guards))
    return result, deferred


def record_tiers(root, report_path):
    """Classify test files whose mean test time exceeds SLOW_MEAN_SECONDS as slow, from a measured run."""
    report = json.loads(report_path.read_text())
    files = {}
    for shard in report['verification']['shards']:
        child = json.loads(Path(shard['report']).read_text())
        for node, stages in child['reports'].items():
            key = child['suite'] + ':' + node.split('::')[0]
            seconds = sum(stages.get(stage, {}).get('duration', 0) for stage in ('setup', 'call', 'teardown'))
            row = files.setdefault(key, [0.0, 0])
            row[0] += seconds
            row[1] += 1
    slow = {key: {'tests': count, 'seconds': round(total, 1)} for key, (total, count) in sorted(files.items())
            if total / count > SLOW_MEAN_SECONDS}
    value = {'schema_version': 1,
             'policy': 'Fast tier (default of al/verify_changed.py): affected test files not listed here plus the parity, '
                       'consistency and docs guards; run after every change. Slow tier: the files below and the acceptance '
                       'tier; run by release_verify.py --full (release) or nightly, never by default.',
             'rule': 'a test file is slow when its mean measured test time exceeds ' + str(SLOW_MEAN_SECONDS) + ' s',
             'measured_files': len(files), 'slow': slow}
    (Path(root) / TIERS).write_text(json.dumps(value, indent=2) + '\n')
    print('recorded ' + str(len(slow)) + ' slow of ' + str(len(files)) + ' measured test files in ' + TIERS)
    return 0


def main(argv=None, *, root=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base', help='compare against merge-base(HEAD, BASE); default main')
    parser.add_argument('--path', action='append', default=[], help='treat PATH (repository-relative) as changed; repeatable')
    parser.add_argument('--all', action='store_true', help='select every collected test (with --tier all: the complete development run)')
    parser.add_argument('--tier', choices=['fast', 'all'], default='fast',
                        help='fast (default): affected tests outside the slow tier plus the parity/consistency/docs guards; '
                             'all: every affected test, slow ones included')
    parser.add_argument('--record-tiers', metavar='REPORT',
                        help='rewrite al/verification-tiers.json from the per-test durations of a --tier all report and exit')
    parser.add_argument('--host', choices=['codex', 'claude'], action='append',
                        help='only run this host suite (plus installer); default both hosts')
    parser.add_argument('--depth', type=int, default=1,
                        help='also select tests of modules that import a changed module, up to DEPTH hops (default 1)')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--strict-isolation', action='store_true',
                        help='serialize test files whose isolation review is not current, as release_verify --full does')
    parser.add_argument('--split', type=int, default=8, metavar='N',
                        help='run each test file in groups of at most N tests, each in a fresh process (default 8; 0 = whole files)')
    parser.add_argument('--report', help='report path outside the checkout (default: a new temporary directory)')
    parser.add_argument('--list', action='store_true', help='print the selected test files and exit')
    args = parser.parse_args(argv)
    if args.workers < 1 or args.depth < 0 or args.split < 0:
        parser.error('--workers must be positive; --depth and --split non-negative')
    root = Path(root or ROOT.parent).resolve()
    if args.report:
        report = Path(args.report).resolve()
        if report == root or root in report.parents:
            parser.error('--report must be outside the checkout')
    try:
        if args.record_tiers:
            return record_tiers(root, Path(args.record_tiers))
        return verify(root, args)
    except (OSError, ValueError) as exc:
        print('FAIL: ' + type(exc).__name__ + ': ' + str(exc))
        return 1


def verify(root, args):
    started = time.monotonic()
    base = None
    if args.all:
        changed, chosen = [], select(root, [], everything=True)
    else:
        if args.path:
            changed = sorted(set(args.path))
        else:
            base, changed = changed_files(root, args.base)
        chosen = select(root, changed, depth=args.depth)
    hosts = {'installer', *(args.host or ('codex', 'claude'))}
    selection = {suite: paths for suite, paths in chosen['tests'].items() if suite in hosts}
    deferred = 0
    if args.tier == 'fast':
        selection, deferred = fast_tier(root, selection, hosts)
    count = sum(len(paths) for paths in selection.values())
    print('changed: ' + (str(len(changed)) + ' file(s)' + (' since ' + base[:12] if base else '') if not args.all else 'all tests requested'))
    for reason in chosen['reasons']:
        print('  ' + reason)
    print('selected (' + args.tier + ' tier): ' + ', '.join(suite + '=' + str(len(selection.get(suite, ()))) for suite in SUITES)
          + ' test file(s)' + ('; complete suites: ' + ', '.join(chosen['full']) if chosen['full'] else ''))
    if deferred:
        print('  ' + str(deferred) + ' affected slow test file(s) deferred to the slow tier (release_verify --full or nightly);'
              ' --tier all runs them now')
    if args.list:
        for suite in SUITES:
            for path in selection.get(suite, ()):
                print(suite + ':' + path)
        return 0
    if not count:
        print('PASS (nothing to run: no AL test observes these changes)')
        return 0

    from al.verification_plan import finalize_verification_storage
    from al.verification_profile import read_json, write_json
    from al.verification_runner import run
    report_path = Path(args.report).resolve() if args.report else Path(tempfile.mkdtemp(prefix='al-verify-changed-')).resolve() / 'report.json'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='run-', dir=report_path.parent))
    plan, binding = _plan(root, directory, selection, strict_isolation=args.strict_isolation, hosts=hosts,
                          split=0 if args.strict_isolation else args.split)
    write_json(directory / 'plan.json', plan)
    # Earlier node durations only order shards (longest first); they never select or skip work.
    history = Path(tempfile.gettempdir()) / ('al-verify-changed-durations-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '.json')
    try:
        durations = read_json(history)
        if not isinstance(durations, dict):
            durations = {}
    except (OSError, ValueError):
        durations = {}
    result = run(plan, workers=args.workers, artifact_root=directory / 'execution', cache_mode='cold', durations=durations)
    observed = {node: seconds for shard in result.get('shards', []) for node, seconds in shard.get('durations', {}).items()
                if type(seconds) in (int, float)}
    if observed:
        try:
            write_json(history, {**durations, **observed})
        except OSError:
            pass
    from al.verification_profile import capture_source_binding
    # Source bytes and membership, not HEAD: committing during a development run changes nothing tested.
    stable = capture_source_binding(root)['files'] == binding['files']
    reconciliation = result['reconciliation']
    from al.verification_budget import violations
    over_budget = violations(observed)
    passed = bool(reconciliation['selected_success']) and stable and not over_budget
    elapsed = time.monotonic() - started
    result.update(plan_path=str(directory / 'plan.json'), plan_sha256=hashlib.sha256((directory / 'plan.json').read_bytes()).hexdigest())
    report = {'schema_version': 1, 'contract': 'al-development-changed-v1', 'status': 'PASS' if passed else 'FAIL',
              'release_claim': False, 'changed': changed, 'base': base, 'selection': selection, 'reasons': chosen['reasons'],
              'complete_suites': chosen['full'], 'source_stable': stable, 'strict_isolation': args.strict_isolation,
              'over_budget': over_budget,
              'verification': result, 'elapsed_seconds': elapsed}
    write_json(report_path, report)
    try:
        cleanup = finalize_verification_storage(report_path, result)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        cleanup = {'complete': False, 'error': str(exc)}

    failed = reconciliation['failed'] + reconciliation['missing']
    if failed or reconciliation['errors']:
        logs = {}
        for shard in result.get('shards', []):
            bad = [node for node, status in shard.get('outcomes', {}).items() if status != 'passed' and status != 'skipped']
            if bad or shard.get('errors'):
                logs[shard['log']] = bad or shard['errors']
        for log, nodes in logs.items():
            print('\n--- ' + ', '.join(map(str, nodes[:5])) + (' (+' + str(len(nodes) - 5) + ' more)' if len(nodes) > 5 else ''))
            print('\n'.join(_failure_excerpt(log)))
            print('log: ' + log)
        for error in reconciliation['errors'][:20]:
            print('error: ' + str(error))
    if not stable:
        print('source changed while the tests ran; the result does not describe one source state')
    if over_budget:
        # The time budget (#36, al/verification-budget.json) fails a run like a failing test.
        print(str(len(over_budget)) + ' over the time budget (start from run_state or test the decision directly):')
        for line in over_budget[:50]:
            print('BUDGET ' + line)
    print('\n' + str(reconciliation['executed_passed']) + ' passed, ' + str(reconciliation['executed_failed']) + ' failed, '
          + str(reconciliation['executed_errors']) + ' errors, ' + str(reconciliation['skipped']) + ' skipped'
          + (', ' + str(len(reconciliation['missing'])) + ' missing' if reconciliation['missing'] else ''))
    outcomes = reconciliation['executed_outcomes']
    for node in [n for n in reconciliation['failed'] if outcomes.get(n) != 'skipped'][:50]:
        print('FAILED ' + node)
    skipped = [n for n in reconciliation['failed'] if outcomes.get(n) == 'skipped']
    if skipped:
        # Same accounting as release_verify: a skipped test is not a passed test.
        print(str(len(skipped)) + ' skipped test(s) count as not passed, as in release verification:')
        for node in skipped[:20]:
            print('SKIPPED ' + node)
    print(('PASS' if passed else 'FAIL') + ' (development check, not a release result) in ' + str(round(elapsed, 1)) + ' s; '
          + str(count) + ' test file(s); report ' + str(report_path)
          + ('' if cleanup.get('complete') else '; verification storage retained'))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
