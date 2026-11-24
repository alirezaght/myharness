"""Failure signatures that tell a pre-existing gate failure from a new one.

A candidate gate failure is pre-existing when the same command failed on the unchanged base and
the candidate adds nothing new. Logs are compared after normalisation: AL's own header lines,
disposable checkout paths, line/column positions, durations, colours, hashes, timestamps and input
size counters ("Analyzed 120 files, 300 dependencies") are removed and whitespace is collapsed, so
the same finding on a shifted line, in another checkout or over a larger tree compares equal.

Findings are compared order-insensitively; tools such as import-linter print sections and witness
chains in a non-deterministic order (issue #33).

1. Findings (when the candidate log has finding lines: path:line diagnostics, test ids, import
   chains, error/fail/broken/violation wording). The candidate is pre-existing when it exits with
   the same code as base and adds no finding:
   - every finding line, without its list marker, counted with multiplicity, also appears at base;
     an edge line ("a -> b") is one witness of a graph edge and is compared as a set member;
   - a result counter on a summary line (no location; "2 broken", "3 errors", "1 failed") is not a
     finding line: the candidate blocks only when such a failure count exceeds the base count;
   - recognised tool reports are parsed into structured findings first (import-linter: contract,
     violation heading and violating import edge, the endpoints of its witness chain); the lines a
     parser consumed are not compared again as text.
   A new finding, one more occurrence of a known finding, or a larger failure count blocks.
2. Fallback (no recognisable finding lines or counters): the same exit code and the same SHA-256
   digest of all normalised output lines.
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
# How much input a tool looked at, not what it found: "Analyzed 120 files, 300 dependencies".
SIZE_RE = re.compile(r"\b\d+(\s+(?:source\s+)?(?:files?|modules?|packages?|dependenc(?:y|ies)|items?|"
                     r"lines? of code|targets?|sources?)\b)", re.I)
# A result counter on a summary line: "Contracts: 1 kept, 2 broken.", "Found 3 errors", "2 failed, 9 passed".
COUNTER_RE = re.compile(r"\b(\d+)\s+(broken|failed|failures?|errors?|warnings?|violations?|problems?|issues?|"
                        r"kept|passed|skipped|ok|succeeded|deselected|xfailed|xpassed)\b", re.I)
FAILURE_COUNTERS = {'broken', 'failed', 'failure', 'error', 'warning', 'violation', 'problem', 'issue'}
LOCATED_RE = re.compile(r"\.[A-Za-z0-9]+:#|\(#\)|::|\s->\s")
BULLET_RE = re.compile(r"^(?:[-*•+]\s*)+")
EDGE_RE = re.compile(r"^(?:-\s*)?(\S+) -> (\S+)")
UNDERLINE_RE = re.compile(r"^[-=]{3,}$")
MAX_LOG = 8 * 1024 * 1024


def normalise(line):
    line = VOLATILE_RE.sub('', line)
    for pattern, replacement in POSITION_RE:line = pattern.sub(replacement, line)
    line = SIZE_RE.sub(r"#\1", line)
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


def import_linter(normalised):
    """Structured import-linter findings and the indexes of the lines they consumed.

    Each finding is 'import-linter | <contract> | <violation heading> | <importer> -> <imported>',
    where importer and imported are the endpoints of a witness chain: the chain's intermediate
    modules, its line numbers and the order of sections and chains are not part of the finding."""
    if not any(line == 'Broken contracts' or re.match(r"Contracts: \d+ kept, \d+ broken", line) for line in normalised):
        return Counter(), set()
    found, consumed = Counter(), set()
    contract = heading = ''
    chain = []

    def flush():
        if chain:found['import-linter | '+contract+' | '+heading+' | '+chain[0][0]+' -> '+chain[-1][1]] = 1
        chain.clear()

    for i, line in enumerate(normalised):
        following = normalised[i + 1] if i + 1 < len(normalised) else ''
        edge = EDGE_RE.match(line)
        if edge:
            if line.startswith('-') or not chain:flush()
            chain.append(edge.groups());consumed.add(i)
            continue
        flush()
        if UNDERLINE_RE.match(line):continue
        if UNDERLINE_RE.match(following):
            contract, heading = line, ''
        elif line.endswith(':') and FINDING_RE.search(line):
            heading = line[:-1];consumed.add(i)
            found['import-linter | '+contract+' | '+heading] = 1
    flush()
    return found, consumed


PARSERS = (import_linter,)


def findings(normalised):
    """Order-insensitive findings and the maximum failure counter values of one run's output."""
    found, consumed = Counter(), set()
    for parser in PARSERS:
        parsed, used = parser(normalised)
        found.update(parsed);consumed |= used
    counters = {}
    for i, line in enumerate(normalised):
        if i in consumed:continue
        counts = COUNTER_RE.findall(line) if not LOCATED_RE.search(line) else []
        if counts:
            for number, word in counts:
                word = word.lower().rstrip('s')
                if word in FAILURE_COUNTERS:counters[word] = max(counters.get(word, 0), int(number))
            continue
        if not FINDING_RE.search(line):continue
        key = BULLET_RE.sub('', line) or line
        if ' -> ' in key:found[key] = 1
        else:found[key] += 1
    return found, counters


def signature(entry):
    """Exit code, findings, failure counters and digest of one run's normalised output."""
    normalised = lines(entry)
    if normalised is None:return None
    found, counters = findings(normalised)
    return {'exit': entry.get('exit'), 'findings': found, 'counters': counters,
            'digest': hashlib.sha256('\n'.join(normalised).encode()).hexdigest()}


def pre_existing(base, candidate):
    """How the candidate failure matches the base failure ('violations' or 'output'), or None."""
    if not base or not candidate:return None
    if base.get('argv') != candidate.get('argv') or base.get('cwd') != candidate.get('cwd'):return None
    b, c = signature(base), signature(candidate)
    if not b or not c or b['exit'] != c['exit']:return None
    if c['findings'] or c['counters']:
        if c['findings'] - b['findings']:return None
        if any(n > b['counters'].get(word, 0) for word, n in c['counters'].items()):return None
        return 'violations'
    return 'output' if c['digest'] == b['digest'] else None
