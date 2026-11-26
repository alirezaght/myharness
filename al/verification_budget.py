"""Timing diagnostics for the AL suites (#36), independent of functional verdicts.

Per-test durations (setup + call + teardown) retain the thresholds and named
acceptance/exemption rules from al/verification-budget.json. Violations remain
reported and retained in over_budget; they do not change verification status or
completeness. Elapsed time is reported, never judged by ordinary verification.
Performance acceptance remains a separate measured obligation.
"""
import fnmatch
import json
from pathlib import Path

BUDGET = Path(__file__).resolve().parent / 'verification-budget.json'


def load(path=BUDGET):
    value = json.loads(Path(path).read_text())
    if (value.get('schema_version') != 1 or not isinstance(value.get('acceptance'), list)
            or not all(isinstance(p, str) and p for p in value['acceptance'])
            or type(value.get('test_seconds')) not in (int, float) or type(value.get('file_seconds')) not in (int, float)
            or not isinstance(value.get('exempt', {}), dict)
            or not all(isinstance(r, str) and r.strip() for r in value.get('exempt', {}).values())):
        raise ValueError('invalid verification budget')
    return value


def _exempt(node, patterns):
    return any(fnmatch.fnmatchcase(node, pattern) for pattern in patterns)


def violations(durations, budget=None):
    """Sorted violation lines for {'suite:path::test': seconds}; empty when within budget."""
    budget = budget or load()
    patterns = budget['acceptance'] + sorted(budget.get('exempt', {}))
    out, files = [], {}
    for node, seconds in durations.items():
        if type(seconds) not in (int, float):
            continue
        exempt = _exempt(node, patterns)
        if not exempt and seconds > budget['test_seconds']:
            out.append('test %.1f s > %s s: %s' % (seconds, budget['test_seconds'], node))
        if not exempt:
            files[node.split('::', 1)[0]] = files.get(node.split('::', 1)[0], 0.0) + seconds
    for file, seconds in files.items():
        if seconds > budget['file_seconds']:
            out.append('file %.1f s > %s s: %s' % (seconds, budget['file_seconds'], file))
    return sorted(out)


def observed(result):
    """Per-test durations of a verification runner result."""
    return {node: seconds for shard in result.get('shards', []) for node, seconds in shard.get('durations', {}).items()
            if type(seconds) in (int, float)}
