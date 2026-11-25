"""Time budget of the AL suites (#36): no test over TEST_SECONDS and no file over FILE_SECONDS.

Measured from the per-test durations (setup + call + teardown) the verification runner reports.
The acceptance journeys in al/verification-budget.json are exempt: they are the small set of real
end-to-end runs kept on purpose (al/TESTS.md). So are the few tests named under `exempt`, each with
the reason it must spawn real processes. Everything else asserts one decision and must start from
a prebuilt run or call the decision function directly.

A violation fails verify_changed and release_verify like a failing test, so a slow test cannot
creep back in.
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
