"""Failure signatures that tell a pre-existing gate failure from a new one.

A candidate gate failure is pre-existing when the same command failed on the unchanged base and
the candidate adds nothing new. Logs are compared after normalisation: AL's own header lines,
disposable checkout paths, line/column positions, durations, colours, hashes and timestamps are
removed and whitespace is collapsed, so the same finding on a shifted line or in another checkout
compares equal.

1. Violation multiset (when the candidate log has finding lines: path:line diagnostics, test
   ids, import chains, error/fail/broken/violation wording). The candidate is pre-existing when
   it exits with the same code as base and every finding line, counted with multiplicity, also
   appears at base. A new finding, or one more occurrence of a known finding, blocks.
2. Fallback (no recognisable finding lines): the same exit code and the same SHA-256 digest of all
   normalised output lines.
"""
import hashlib
import re
from collections import Counter
from pathlib import Path

HEADER = ('argv:', 'cwd:', 'execution_sha256:', 'invocation:', 'executed at base:', 'AL:', 'environment scrubbed:')
FINDING_RE = re.compile(r"error|fail|✗|✖|panic|exception|traceback|warning|violation|exceeds|broken|forbidden|not allowed"
                        r"|^E\s|\s->\s|\.[A-Za-z0-9]+:#|::", re.I)
VOLATILE_RE = re.compile(r"(?:/[^\s:'\"]*)?/checkout/|\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b|\x1b\[[0-9;]*m"
                         r"|\b[0-9a-f]{12,}\b|\d{4}-\d\d-\d\dT[\d:.]+Z?")
POSITION_RE = (
    (re.compile(r"(\.[A-Za-z0-9]+):\d+(?::\d+)?"), r"\1:#"),        # path.py:12:5
    (re.compile(r"(\.[A-Za-z0-9]+)\(\d+,\d+\)"), r"\1(#)"),          # file.ts(12,5)
    (re.compile(r"\(l\.\d+(?:,\s*l\.\d+)*\)"), "(l.#)"),              # import-linter (l.12)
    (re.compile(r"\bline \d+\b", re.I), "line #"),
)
MAX_LOG = 8 * 1024 * 1024


def normalise(line):
    line = VOLATILE_RE.sub('', line)
    for pattern, replacement in POSITION_RE:line = pattern.sub(replacement, line)
    return ' '.join(line.split())


def lines(entry):
    """Normalised output lines of a gate run, or None when its log is unavailable or too large."""
    try:
        path = Path(entry['log'])
        if path.stat().st_size > MAX_LOG:return None
        text = path.read_text(errors='replace')
    except (OSError, KeyError, TypeError):
        return None
    out = []
    for raw in text.splitlines():
        if raw.startswith(HEADER):continue
        line = normalise(raw)
        if line:out.append(line)
    return out


def findings(normalised):
    return Counter(line for line in normalised if FINDING_RE.search(line))


def signature(entry):
    """Exit code, finding multiset and digest of one run's normalised output."""
    normalised = lines(entry)
    if normalised is None:return None
    return {'exit': entry.get('exit'), 'findings': findings(normalised),
            'digest': hashlib.sha256('\n'.join(normalised).encode()).hexdigest()}


def pre_existing(base, candidate):
    """How the candidate failure matches the base failure ('violations' or 'output'), or None."""
    if not base or not candidate:return None
    if base.get('argv') != candidate.get('argv') or base.get('cwd') != candidate.get('cwd'):return None
    b, c = signature(base), signature(candidate)
    if not b or not c or b['exit'] != c['exit']:return None
    if c['findings']:
        return 'violations' if not c['findings'] - b['findings'] else None
    return 'output' if c['digest'] == b['digest'] else None
