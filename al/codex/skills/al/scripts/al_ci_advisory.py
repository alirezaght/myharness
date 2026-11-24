"""Advisory (allow-failure) semantics of sourced CI recipes.

A CI step whose failure never fails the pipeline is informational. AL still runs the check it
maps to, but the result is a note, never a required gate. Detection is generic and source-bound;
it never looks for a particular repository's helper names:

- GitLab: `allow_failure: true` or `allow_failure: {exit_codes: ...}` on a job, or on every rule
  that can select it; `after_script` lines, whose failures GitLab ignores.
- GitHub: a literal `continue-on-error: true` on a job or on a step.
- Shell: a command whose failure is swallowed by a benign `||` tail (`|| true`, `|| :`,
  `|| exit 0`, `|| return 0`, `|| echo ...`), and calls of a shell function that runs its
  arguments (`"$@"`) with the failure swallowed (`"$@" || true`, `if ! "$@"; then echo ...; fi`,
  or after `set +e` with a benign final statement) and that never records the failure for later
  (no assignment, counter, file write or non-zero `return`/`exit` after the call). A helper
  function that runs the tool is advisory when every call of it is.

Anything outside this grammar is treated as blocking, so detection can only relax a check that
the sourced recipe demonstrably tolerates.
"""
import re
import shlex
from pathlib import PurePosixPath

BENIGN = {'true', ':', 'echo', 'printf'}
SHELLS = {'sh', 'bash', 'zsh', 'dash', 'ksh', 'source', '.'}
CONDITION = {'if', 'elif', 'while', 'until', '!'}
PREFIX = {'then', 'do', 'else', '{', '(', 'time', 'command', 'exec', 'env'}
ARGUMENTS = re.compile(r'^\$\{?[@*](?::\d+(?::\d+)?)?\}?$')
ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(?:\[[^]]*\])?\+?=')
DEFINITION = re.compile(r'^[A-Za-z_][\w:.-]*$')
REDIRECT = {'>', '>>', '>|'}
QUIET = {'/dev/stderr', '/dev/null'}
# Tools whose package name differs from the command they install.
ALIASES = {'lint-imports': ('import-linter', 'importlinter')}
INTERPRETERS = {'sh', 'bash', 'zsh', 'uv', 'uvx', 'npx', 'npm', 'pnpm', 'yarn', 'bun', 'deno', 'make', 'env', 'go'}
MAX_FILES = 64
MAX_BYTES = 1024 * 1024


def _allowed(value):
    return value is True or isinstance(value, dict) and 'exit_codes' in value


def gitlab_job(job):
    """Why a merged GitLab job never fails its pipeline, or None."""
    if not isinstance(job, dict):return None
    value = job.get('allow_failure')
    rules = job.get('rules') if isinstance(job.get('rules'), list) else []
    selecting = [r for r in rules if isinstance(r, dict) and r.get('when') != 'never']
    # A rule's own allow_failure overrides the job's; a rule without one falls back to it.
    effective = [r['allow_failure'] for r in selecting if 'allow_failure' in r]
    if not selecting or len(effective) < len(selecting):effective.append(value)
    if not all(_allowed(v) for v in effective):return None
    exit_codes = any(isinstance(v, dict) for v in effective)
    return 'GitLab allow_failure' + (' (exit_codes)' if exit_codes else '') + ': a failure never fails the pipeline'


def github_job(job):
    if isinstance(job, dict) and job.get('continue-on-error') is True:
        return 'GitHub continue-on-error: a failure never fails the workflow'
    return None


def github_steps(job):
    """The job without literal continue-on-error steps, and the run text of the removed steps.

    A literal `continue-on-error: false` is the default and is dropped; an expression stays, so
    the job keeps its unsupported shape."""
    if not isinstance(job, dict):return job, []
    out = {k: v for k, v in job.items() if not (k == 'continue-on-error' and isinstance(v, bool))}
    steps = job.get('steps')
    if not isinstance(steps, list):return out, []
    kept, removed = [], []
    for step in steps:
        if isinstance(step, dict) and step.get('continue-on-error') is True and 'run' in step:
            removed.append(step['run'] if isinstance(step['run'], str) else '');continue
        if isinstance(step, dict) and step.get('continue-on-error') is False:
            step = {k: v for k, v in step.items() if k != 'continue-on-error'}
        kept.append(step)
    out['steps'] = kept
    return out, removed


def _strip_comment(line):
    quote = None;escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False;continue
        if char == '\\' and quote != "'":
            escaped = True;continue
        if quote:
            if char == quote:quote = None
            continue
        if char in '"\'':quote = char
        elif char == '#' and (index == 0 or line[index - 1] in ' \t;&|('):return line[:index]
    return line


def logical_lines(text):
    """Shell lines with continuations joined, comments and here-document bodies removed."""
    out = [];pending = '';heredoc = None
    for raw in text.splitlines():
        if heredoc is not None:
            if raw.strip() == heredoc:heredoc = None
            continue
        line = pending + raw
        if line.endswith('\\') and not line.endswith('\\\\'):
            pending = line[:-1] + ' ';continue
        pending = ''
        line = _strip_comment(line)
        match = re.search(r'<<-?\s*[\'"]?([A-Za-z_][A-Za-z0-9_]*)', line)
        if match:heredoc = match.group(1)
        if line.strip():out.append(line)
    if pending.strip():out.append(_strip_comment(pending))
    return out


def _tokens(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True;lexer.commenters = ''
    return list(lexer)


def statements(text):
    """Statements as token lists, or None when the text is outside the shell grammar AL reads."""
    out = []
    try:
        for line in logical_lines(text):
            current = []
            for token in _tokens(line):
                if token in {';', '&', ';;'}:
                    if current:out.append(current)
                    current = []
                else:current.append(token)
            if current:out.append(current)
    except ValueError:
        return None
    return out


def _command(tokens):
    """The statement without leading keywords, grouping and environment assignments."""
    index = 0;condition = False
    while index < len(tokens):
        token = tokens[index]
        if token in CONDITION:
            condition = True;index += 1
        elif token in PREFIX or ASSIGNMENT.match(token):index += 1
        else:break
    return tokens[index:], condition


def _loud(tokens):
    """The statement writes to a file other than stderr or /dev/null."""
    return any(t in REDIRECT and (i + 1 >= len(tokens) or tokens[i + 1] not in QUIET) for i, t in enumerate(tokens))


def _benign(tail):
    parts = [[]]
    for token in tail:
        if token in {'&&', ';'}:parts.append([])
        elif token not in {'{', '}', '(', ')'}:parts[-1].append(token)
    parts = [p for p in parts if p]
    if not parts:return False
    for part in parts:
        if _loud(part):return False
        if part[0] in {'exit', 'return'}:
            if part[1:2] != ['0']:return False
        elif part[0] not in BENIGN:return False
    return True


def swallowed(tokens):
    """The statement's failure is swallowed by a benign `||` tail."""
    if '||' not in tokens:return False
    last = len(tokens) - 1 - tokens[::-1].index('||')
    return _benign(tokens[last + 1:])


def _failing(tokens):
    """A statement that can fail the function or record a failure for a later check."""
    keywords = [t for t in tokens if t not in CONDITION and t not in PREFIX]
    if keywords and ASSIGNMENT.match(keywords[0]):return True
    command, _ = _command(tokens)
    if not command:return False
    if command[0] in {'exit', 'return'}:return command[1:2] != ['0']
    if command[0] in {'false', 'let', 'export', 'declare', 'typeset', 'readonly'} or command[0].startswith('(('):return True
    return _loud(tokens)


def _definition(tokens):
    """(name, tokens after the name and parentheses) when a statement opens a function."""
    if tokens[:1] == ['function'] and len(tokens) >= 2 and DEFINITION.match(tokens[1]):
        name, rest = tokens[1], tokens[2:]
    elif len(tokens) >= 2 and DEFINITION.match(tokens[0]) and (tokens[1] == '()' or tokens[1:3] == ['(', ')']):
        name, rest = tokens[0], tokens[1:]
    else:return None, None
    if rest[:1] == ['()']:rest = rest[1:]
    elif rest[:2] == ['(', ')']:rest = rest[2:]
    return name, rest


def split_functions(parsed):
    """Function bodies keyed by name, and the statements outside any function."""
    functions = {};toplevel = [];index = 0
    while index < len(parsed):
        name, rest = _definition(parsed[index])
        index += 1
        if name is None:
            toplevel.append(parsed[index - 1]);continue
        body = [];depth = 0;opened = False;pending = [rest] if rest else []
        while True:
            if not pending:
                if index >= len(parsed):break
                pending = [parsed[index]];index += 1
            kept = [];closed = False
            for token in pending.pop(0):
                if token == '{':
                    depth += 1
                    if depth == 1 and not opened:
                        opened = True;continue
                elif token == '}':
                    depth -= 1
                    if opened and depth == 0:
                        closed = True;break
                kept.append(token)
            if kept:body.append(kept)
            if closed:break
        functions[name] = body
    return functions, toplevel


def _wrapper(body):
    """A function that runs its arguments and never lets their failure escape."""
    invoked = False;relaxed = False
    for tokens in body:
        if tokens[:2] == ['set', '+e']:relaxed = True
        if any(ARGUMENTS.match(t) for t in tokens):
            _, condition = _command(tokens)
            if not (condition or swallowed(tokens) or relaxed):return False
            invoked = True;continue
        if invoked and _failing(tokens):return False
    if not invoked:return False
    if relaxed:
        final, _ = _command(body[-1])
        if not final or not (final[0] in BENIGN or final[:2] in (['return', '0'], ['exit', '0'])):return False
    return True


def wrappers(parsed_texts):
    out = set()
    for parsed in parsed_texts:
        for name, body in split_functions(parsed or [])[0].items():
            if _wrapper(body):out.add(name)
    return out


def tool_names(argv):
    """The executable a check runs, with its known package aliases, in normalised form."""
    args = list(argv)
    if args[:2] == ['uv', 'run']:
        args = args[2:]
        valued = {'--extra', '--group', '--package', '--with', '--project', '--directory', '--python', '--env-file', '--only-group'}
        while args and args[0].startswith('-'):
            args = args[2:] if args[0] in valued else args[1:]
    if args[:1] and PurePosixPath(args[0]).name.startswith('python') and args[1:2] == ['-m']:args = args[2:]
    if not args:return set()
    name = PurePosixPath(args[0]).name
    # An interpreter or package runner names no particular check.
    if name.startswith(('python', 'node')) or name in INTERPRETERS:return set()
    return {n.lower().replace('_', '-') for n in (name, *ALIASES.get(name, ()))}


def _mentions(tokens, names):
    return any(PurePosixPath(t).name.lower().replace('_', '-') in names for t in tokens)


def _reference(tokens):
    """A repository script a statement runs or sources."""
    command, _ = _command(tokens)
    if not command:return None
    if command[0] in SHELLS:
        rest = [t for t in command[1:] if not t.startswith('-')]
        candidate = rest[0] if rest else None
    elif '/' in command[0] or command[0].endswith('.sh'):candidate = command[0]
    else:return None
    if not candidate or '$' in candidate or candidate.startswith('/'):return None
    path = PurePosixPath(candidate)
    if '..' in path.parts:return None
    return path.as_posix().removeprefix('./') or None


def _advisory(tokens, known):
    if swallowed(tokens):return 'shell `|| <benign>` swallows the failure'
    command, _ = _command(tokens)
    if command and command[0] in known:return 'shell function ' + command[0] + ' swallows the failure of the command it runs'
    return None


def _wrapped_reference(tokens, known):
    command, _ = _command(tokens)
    if command and command[0] in known:
        for index in range(1, len(command)):
            ref = _reference(command[index:])
            if ref:return ref
    return None


def classify(entries, names, read):
    """How sourced CI recipes run a tool: advisory only, blocking, or not at all.

    `entries` are (label, shell text, advisory reason or None) from CI definitions. `read(path)`
    returns a referenced repository script's text (binding its bytes) or None. Returns
    {'advisory': bool, 'mentions': [...], 'sources': [...]}; `advisory` is True only when the tool
    is invoked at least once and every invocation is advisory."""
    names = {n.lower().replace('_', '-') for n in names}
    cache = {}
    def load(path):
        if path not in cache:
            text = read(path) if len(cache) < MAX_FILES else None
            cache[path] = text if text is None or len(text.encode('utf-8', 'replace')) <= MAX_BYTES else None
        return cache[path]
    def collect(known):
        texts = {};queue = list(entries)
        while queue:
            label, text, reason = queue.pop(0)
            if (label, reason) in texts:continue
            parsed = statements(text)
            texts[(label, reason)] = (text, parsed)
            for tokens in parsed or []:
                ref = _reference(tokens) or _wrapped_reference(tokens, known)
                content = load(ref) if ref else None
                if content is not None:queue.append((ref, content, reason or _advisory(tokens, known)))
        return texts
    # Wrappers can live in sourced libraries: collect, learn the wrappers, then collect again with them.
    known = wrappers(parsed for _, parsed in collect(set()).values())
    texts = collect(known)
    known |= wrappers(parsed for _, parsed in texts.values())
    split = {key: split_functions(parsed) if parsed is not None else None for key, (_, parsed) in texts.items()}
    def call_sites(name):
        sites = []
        for (_, reason), parts in split.items():
            if parts is None:continue
            functions, toplevel = parts
            for tokens in [*toplevel, *(t for body in functions.values() for t in body)]:
                command, _ = _command(tokens)
                if command and (command[0] == name or command[0] in known and name in command[1:]):sites.append((tokens, reason))
        return sites
    mentions = []
    for (label, reason), (text, parsed) in texts.items():
        if parsed is None:
            if any(n in text.lower().replace('_', '-') for n in names):
                mentions.append({'source': label, 'advisory': bool(reason), 'reason': reason or 'unparsed shell text'})
            continue
        functions, toplevel = split[(label, reason)]
        rows = [(tokens, None) for tokens in toplevel]+[(tokens, name) for name, body in functions.items() for tokens in body]
        for tokens, owner in rows:
            if not _mentions(tokens, names):continue
            why = reason or _advisory(tokens, known)
            if not why and owner is not None and owner not in known:
                # A helper function that runs the tool is advisory when every call of it is.
                sites = call_sites(owner)
                if sites and all(r or _advisory(t, known) for t, r in sites):
                    why = 'every call of shell function ' + owner + ' swallows its failure'
            mentions.append({'source': label, 'advisory': bool(why), 'reason': why or 'blocking invocation',
                             'command': ' '.join(tokens)[:200]})
    sources = sorted(p for p, v in cache.items() if v is not None)
    return {'advisory': bool(mentions) and all(m['advisory'] for m in mentions), 'mentions': mentions, 'sources': sources}


def partition_lines(lines):
    """GitLab script lines split into blocking lines and lines whose failure is swallowed.

    A swallowed line is recorded with its command without the benign tail."""
    blocking, advisory = [], []
    for line in lines:
        try:tokens = _tokens(line)
        except ValueError:
            blocking.append(line);continue
        if swallowed(tokens) and tokens.count('||') == 1 and not any(t in {';', '&', '&&', '|'} for t in tokens[:tokens.index('||')]):
            advisory.append({'line': line, 'command': line[:line.rfind('||')].rstrip()})
        else:blocking.append(line)
    return blocking, advisory


def ci_entries(ci):
    """(label, shell text, advisory reason) for every inspectable CI definition in an inventory."""
    out = []
    for item in [*ci.get('obligations', []), *ci.get('deferred', [])]:
        source = item.get('source_path', '?') + ':' + str(item.get('job'))
        for variant in item.get('definition_variants', []):
            job = variant.get('job')
            if not isinstance(job, dict):continue
            reason = gitlab_job(job)
            for key in ('before_script', 'script', 'after_script'):
                value = job.get(key, [])
                value = [value] if isinstance(value, str) else value
                if not isinstance(value, list):continue
                text = '\n'.join(v for v in value if isinstance(v, str))
                if text:out.append((source + ':' + key, text, reason or ('GitLab after_script: its failure never fails the job' if key == 'after_script' else None)))
        workflow = item.get('workflow_definition')
        jobs = workflow.get('jobs') if isinstance(workflow, dict) else None
        job = jobs.get(item.get('job')) if isinstance(jobs, dict) else None
        if isinstance(job, dict) and isinstance(job.get('steps'), list):
            reason = github_job(job)
            for index, step in enumerate(job['steps']):
                if isinstance(step, dict) and isinstance(step.get('run'), str):
                    step_reason = reason or ('GitHub step continue-on-error: its failure never fails the job' if step.get('continue-on-error') is True else None)
                    out.append((source + ':steps[' + str(index) + ']', step['run'], step_reason))
    return out


def complete(ci):
    """Every CI definition was read: no unknown inventory hides another invocation."""
    for item in ci.get('obligations', []):
        if str(item.get('job', '')).startswith('<unresolved-') or item.get('impact', {}).get('inventory_complete') is False:return False
        if any(v.get('unsupported') for v in item.get('definition_variants', [])):return False
    return True
