#!/usr/bin/env python3
"""Run complete reviewed templates in every suite that collects them; never a full-suite claim."""
import argparse
import hashlib
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from al.verification_isolation import SPLIT_FILES
from al.verification_plan import build_plan, collect_suites
from al.verification_profile import capture_source_binding, read_json, write_json
from al.verification_runner import run


def execute(root, files, report, workers=8):
    started = time.monotonic();root = Path(root).resolve();report = Path(report).resolve()
    if not files or not set(files) <= SPLIT_FILES or len(set(files)) != len(files):
        raise ValueError('explicit distinct reviewed templates required')
    if report.exists() or report == root or root in report.parents:
        raise ValueError('new external cohort report required')
    binding = capture_source_binding(root)
    directory = Path(tempfile.mkdtemp(prefix='al-template-cohort-', dir=report.parent))
    collections = collect_suites(root, {'artifact_root': str(directory)})
    plan = build_plan(collections, read_json(root / 'al/verification-obligations.json'),
                      tier='full', changed_paths=[], binding=binding)
    assignments = [row for row in plan['execution_assignments'] if row['suite'] in {'codex', 'claude'} and row['node']['source']['path'] in files]
    # Shared-flow templates may live only in the codex suite (byte-identical shared scripts).
    if any(not any(a['node']['source']['path'] == file for a in assignments) for file in files):
        raise ValueError('reviewed template absent from complete collection')
    if not plan['isolation_closure_current'] or any(a['serialization_group'] for a in assignments):
        raise ValueError('reviewed template isolation proof is not current')
    selected = {a['id'] for a in assignments}
    plan.update(execution_assignments=assignments, shared_equivalents=[], complete_required=False,
                tier='template-cohort', intentionally_unselected=sorted(set(plan['collected_obligations']) - selected))
    write_json(directory / 'plan.json', plan)
    result = run(plan, workers=workers, artifact_root=directory / 'execution', cache_mode='cold', profile=True)
    stable = capture_source_binding(root) == binding
    value = {'schema_version': 1, 'contract': 'al-focused-template-verification-v1',
             'status': 'PASS' if stable and result['reconciliation']['selected_success'] and result['cleanup']['complete'] else 'FAIL',
             'full_suite': False, 'templates': sorted(files), 'source_stable': stable,
             'source_binding': binding, 'elapsed_seconds': time.monotonic() - started,
             'collected_obligations': len(plan['collected_obligations']), 'selected_obligations': len(assignments),
             'plan': str(directory / 'plan.json'), 'plan_sha256': hashlib.sha256((directory / 'plan.json').read_bytes()).hexdigest(),
             'verification': result}
    write_json(report, value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', action='append', required=True, choices=sorted(SPLIT_FILES))
    parser.add_argument('--report', required=True);parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    value = execute(Path(__file__).resolve().parents[1], args.file, args.report, args.workers)
    counts = value['verification']['reconciliation']
    print(str(counts['executed_passed']) + ' passed, ' + str(counts['executed_failed']) + ' failed, ' + str(counts['executed_errors']) + ' errors')
    print('focused template cohort ' + value['status'] + '; report=' + args.report + '; seconds=' + str(value['elapsed_seconds']))
    return 0 if value['status'] == 'PASS' else 1


if __name__ == '__main__':raise SystemExit(main())
