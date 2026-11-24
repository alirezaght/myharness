"""Map changed source files to the AL test files that can observe them (development checks only).

The map is a static dependency graph over the AL tree:

* Python imports (``import x``, ``from x import y``, relative imports, imports
  inside functions), resolved against the importing file's own directory, its
  host's ``scripts``/``tests`` directories, ``al/`` and the repository root;
* string literals that name a known file (``"al_state.py"``, ``"SKILL.md"``)
  or a known module stem (``importlib.import_module("al_gates")``);
* the naming convention ``tests/test_<name>.py`` <-> ``scripts/al_<name>.py``.

A test file is selected when it changed, when it references a changed file or
a source module that reaches a changed file within ``depth`` reverse
import/reference hops, or when it imports a changed test helper (directly or
through other helpers). Full transitive closure is
not used: every host test runs through ``conftest.py`` -> ``al_state.py``, which
reaches nearly every script, so the closure would always be the whole suite.

Changes the graph cannot attribute fall back to complete suites: suite
infrastructure (conftest and the helpers it imports, the pytest plugin,
project/lock files), a changed ``al/`` file that no test references, and any
deleted Python module. This map is for development checks only. Release
verification never uses it; ``release_verify.py --full`` stays the gate.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
import re

HOSTS = ('codex', 'claude')
SUITES = {'installer': 'al/tests', 'codex': 'al/codex/skills/al/tests', 'claude': 'al/claude/skills/al/tests'}
TEXT_SUFFIXES = ('.py', '.md', '.json', '.yml', '.yaml', '.toml', '.txt', '.sh', '.js', '.cfg', '.fixture')
# Changing these can change how every test in a suite is collected or run.
SUITE_INFRASTRUCTURE = {
    'al/tests/conftest.py': ('installer',),
    'al/codex/skills/al/tests/conftest.py': ('codex',),
    'al/claude/skills/al/tests/conftest.py': ('claude',),
    'al/verification_pytest.py': ('installer', 'codex', 'claude'),
    'al/release-layout.json': ('installer',),
    'pyproject.toml': ('installer', 'codex', 'claude'),
    'uv.lock': ('installer', 'codex', 'claude'),
    'conftest.py': ('installer', 'codex', 'claude'),
}
# Structural guards scan whole trees (parity, layout, documentation); any host change runs them.
HOST_GUARDS = ('test_parity.py', 'test_consistency.py', 'test_resources.py', 'test_docs.py')
NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.\-]*\.(?:py|md|json|ya?ml|toml|txt|sh|js|cfg|fixture)\b')
EXCLUDED_PARTS = {'__pycache__', '.pytest_cache', '.venv', 'node_modules'}


def host_of(relative):
    parts = relative.split('/')
    if len(parts) > 3 and parts[0] == 'al' and parts[1] in HOSTS and parts[2:4] == ['skills', 'al']:
        return parts[1]
    return None


def suite_of(relative):
    for suite, prefix in SUITES.items():
        if relative.startswith(prefix + '/'):
            return suite
    return None


def is_test_file(relative):
    name = relative.rsplit('/', 1)[-1]
    return suite_of(relative) is not None and name.startswith('test_') and name.endswith('.py') and '/fixtures/' not in relative


class Graph:
    def __init__(self, root):
        self.root = Path(root).resolve()
        al = self.root / 'al'
        self.files = sorted(p.relative_to(self.root).as_posix() for p in al.rglob('*')
                            if p.is_file() and not EXCLUDED_PARTS & set(p.parts) and p.suffix in TEXT_SUFFIXES)
        self.files += [name for name in ('pyproject.toml', 'conftest.py') if (self.root / name).is_file()]
        self.known = set(self.files)
        self.by_name = {}
        for relative in self.files:
            self.by_name.setdefault(relative.rsplit('/', 1)[-1], []).append(relative)
        self.edges = {relative: self._edges(relative) for relative in self.files}

    # -- edge discovery -------------------------------------------------
    def _scope(self, relative):
        """Files a literal in ``relative`` may name: its own host tree, or all of al/ otherwise."""
        host = host_of(relative)
        prefix = 'al/' + host + '/skills/al/' if host else 'al/'
        return prefix

    def _roots(self, relative):
        directory = relative.rsplit('/', 1)[0]
        roots = [directory]
        host = host_of(relative)
        if host:
            roots += ['al/' + host + '/skills/al/tests', 'al/' + host + '/skills/al/scripts']
        roots += ['al', 'al/tests', '']
        return roots

    def _module(self, roots, dotted):
        path = dotted.replace('.', '/')
        for base in roots:
            for candidate in (path + '.py', path + '/__init__.py'):
                name = (base + '/' + candidate) if base else candidate
                if name in self.known:
                    return name
        return None

    def _edges(self, relative):
        try:
            text = (self.root / relative).read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            return set()
        found = set()
        if relative.endswith('.py'):
            found |= self._python_edges(relative, text)
        prefix = self._scope(relative)
        for match in set(NAME.findall(text)):
            name = match.rsplit('/', 1)[-1]
            found.update(f for f in self.by_name.get(name, ()) if f.startswith(prefix) and f != relative)
        return found

    def _python_edges(self, relative, text):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return set()
        roots = self._roots(relative)
        package = relative.rsplit('/', 1)[0]
        found = set()
        stems = self._stems(self._scope(relative))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split('/')
                    base = base[:len(base) - (node.level - 1)] if node.level > 1 else base
                    module_roots = ['/'.join(base)]
                    module = node.module or ''
                    for alias in node.names:
                        for dotted in filter(None, (module + '.' + alias.name if module else alias.name, module)):
                            hit = self._module(module_roots, dotted)
                            if hit:
                                found.add(hit)
                    continue
                module = node.module or ''
                names = [module] + [module + '.' + alias.name for alias in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in stems:
                found.update(stems[node.value])
                continue
            for dotted in names:
                # The longest importable prefix: `from a import b` names a module or an attribute.
                parts = dotted.split('.')
                for end in range(len(parts), 0, -1):
                    hit = self._module(roots, '.'.join(parts[:end]))
                    if hit:
                        found.add(hit)
                        break
        found.discard(relative)
        return found

    @lru_cache(maxsize=None)
    def _stems(self, prefix):
        stems = {}
        for relative in self.files:
            if relative.startswith(prefix) and relative.endswith('.py') and '/tests/' not in relative:
                stem = relative.rsplit('/', 1)[-1][:-3]
                if stem != '__init__' and len(stem) > 3:
                    stems.setdefault(stem, set()).add(relative)
        return stems

    # -- queries --------------------------------------------------------
    def test_files(self):
        return [relative for relative in self.files if is_test_file(relative)]


def select(root, changed, *, depth=1, everything=False):
    """Return {'tests': {suite: [suite-relative test paths]}, 'full': [suites], 'reasons': [...]}.

    A test file is selected when it is changed, when it references (imports or names) a
    changed source file or one of the source files that reach it within ``depth`` reverse
    hops, when it reaches a changed test helper, or by the test_<name>/al_<name>
    convention. Host changes also select the structural guards of both hosts.
    """
    graph = Graph(root)
    changed = sorted(set(changed))
    full, reasons = set(SUITES) if everything else set(), []
    selected = {suite: set() for suite in SUITES}
    tests = graph.test_files()
    reverse = {}
    for source, targets in graph.edges.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)

    def helper(path):
        return suite_of(path) is not None and not is_test_file(path)

    def test_side(test):
        seen, stack = {test}, [test]
        while stack:
            for child in graph.edges.get(stack.pop(), ()):
                if helper(child) and not child.endswith('/conftest.py') and child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    for path in changed:
        if path in SUITE_INFRASTRUCTURE:
            suites = SUITE_INFRASTRUCTURE[path]
            full.update(suites)
            reasons.append(path + ': suite infrastructure -> complete ' + '/'.join(suites))
            continue
        if not path.startswith('al/'):
            reasons.append(path + ': outside al/, not exercised by AL suites')
            continue
        host = host_of(path)
        if path not in graph.known:
            if path.endswith('.py') and not is_test_file(path):
                # A deleted module may still be imported somewhere; only a complete run can tell.
                suites = (host,) if host else tuple(SUITES)
                full.update(suites)
                reasons.append(path + ': deleted or unparsed module -> complete ' + '/'.join(suites))
            else:
                reasons.append(path + ': deleted test/resource, nothing to run')
            continue
        conftest = SUITES.get(suite_of(path) or '', '') + '/conftest.py'
        if helper(path) and conftest in graph.known and path in test_side(conftest):
            # Shared fixtures import it, so every test in the suite runs through it.
            full.add(suite_of(path))
            reasons.append(path + ': imported by ' + conftest + ' -> complete ' + suite_of(path))
            continue
        hits = set()
        if is_test_file(path):
            hits.add(path)
        elif helper(path):
            hits.update(test for test in tests if path in test_side(test))
        else:
            reached, frontier = {path}, {path}
            for _ in range(depth):
                frontier = {source for target in frontier for source in reverse.get(target, ())
                            if suite_of(source) is None and source not in reached}
                reached |= frontier
            hits.update(test for test in tests if graph.edges[test] & reached)
            name = path.rsplit('/', 1)[-1]
            if host and '/scripts/' in path and name.startswith('al_') and name.endswith('.py'):
                conventional = SUITES[host] + '/test_' + name[3:]
                if conventional in graph.known:
                    hits.add(conventional)
        if not hits:
            suites = (host,) if host else tuple(SUITES)
            full.update(suites)
            reasons.append(path + ': no test references it -> complete ' + '/'.join(suites))
            continue
        reasons.append(path + ': ' + str(len(hits)) + ' test file(s)')
        if host:
            hits.update(SUITES[h] + '/' + guard for h in HOSTS for guard in HOST_GUARDS
                        if SUITES[h] + '/' + guard in graph.known)
        for test in hits:
            selected[suite_of(test)].add(test)
    for suite in full:
        selected[suite] = {test for test in tests if suite_of(test) == suite}
    return {'tests': {suite: sorted(_suite_relative(suite, t) for t in paths) for suite, paths in selected.items()},
            'full': sorted(full), 'reasons': reasons, 'changed': changed}


def _suite_relative(suite, test):
    # The installer suite runs from the repository root; host suites run from their skill directory.
    if suite == 'installer':
        return test
    return test.split('/skills/al/', 1)[1]
