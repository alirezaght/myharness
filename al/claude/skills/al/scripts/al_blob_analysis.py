"""Per-blob analysis facts: pure functions of one file's bytes (#25).

Everything here depends only on its arguments, never on repository or run state, so
the fact store can key a result by the blob's content hash and this module's code
digest (it imports no other AL module, so its digest is its own bytes). Repository-dependent interpretation (module
ownership, units, reach) replays these facts in the Python import analysis. Keep this module free of AL module names."""
import ast
import re
from collections import defaultdict


def _trie_pattern(tokens):
    """A regex matching, at one position, the longest token that starts there.

    Shared prefixes are factored so the engine does O(token length) work per
    position instead of trying every token; single-child chains stay literal so
    nesting depth is the number of branch points, not the token length."""
    trie = {}
    for token in tokens:
        node = trie
        for byte in token:
            node = node.setdefault(byte, {})
        node[None] = True

    def emit(node):
        branches = []
        for byte in sorted(k for k in node if k is not None):
            run = bytes([byte]);child = node[byte]
            while None not in child and len(child) == 1:
                (next_byte, child), = child.items();run += bytes([next_byte])
            branches.append(re.escape(run) + emit(child))
        if not branches:
            return b''
        body = branches[0] if len(branches) == 1 else b'(?:' + b'|'.join(branches) + b')'
        # Greedy optional continuation: the longest token at a position wins over its prefixes.
        return b'(?:' + body + b')?' if None in node else body
    return re.compile(emit(trie))


class TokenFinder:
    """Every token occurring (as a substring) in a byte string, in one regex scan.

    Equivalent to `{t for t in tokens if t in data}`: each search reports the
    longest token at the leftmost remaining start position, and every token that
    is a prefix of a reported match also occurs there."""
    def __init__(self, tokens):
        self.tokens = frozenset(t for t in tokens if t)
        self.pattern = _trie_pattern(sorted(self.tokens)) if self.tokens else None

    def present(self, data):
        if self.pattern is None:
            return set()
        found = set();position = 0;search = self.pattern.search
        while True:
            match = search(data, position)
            if match is None:
                return found
            longest = match.group(0);start = match.start()
            found.update(longest[:k] for k in range(1, len(longest) + 1) if longest[:k] in self.tokens)
            position = start + 1


# Bytes that never occur in a literal path token (whitespace, NUL and code punctuation).
SEPARATORS = b'\x00\t\n\x0b\x0c\r ()[]{},;:=<>!?*&|^`\\#'
_SPLIT = re.compile(b'[' + re.escape(SEPARATORS) + b']+')


def token_safe(token):
    """Whether `token in data` can be answered from `reference_runs(data)`."""
    return bool(token) and not any(b in SEPARATORS for b in token)


def reference_runs(data):
    """The distinct maximal separator-free runs of a blob, NUL-joined.

    For every token without separator bytes, `token in data` equals
    `token in reference_runs(data)`: an occurrence never spans a separator, and the
    joining NUL is itself a separator, so no occurrence spans two runs."""
    return b'\0'.join(sorted({run for run in _SPLIT.split(data) if run}))


def parse(data):
    """One Python syntax tree; never memoized, so no repository-sized AST pool is retained."""
    return ast.parse(data)


def _lineno(node):
    return getattr(node, 'lineno', None)


def import_events(data, module, package):
    """The import observations of one Python source, in visit order.

    Events: ['i', line, target, reason] an uncertainty; ['r', name, line, dynamic] a
    resolved import name (None when not a literal module); ['m', name, line] a
    from-import member that is resolved only when some module owns it."""
    events = []

    def issue(node, target, reason):
        events.append(['i', _lineno(node), target, reason])

    def resolve(name, node, dynamic=False):
        events.append(['r', name if isinstance(name, str) else None, _lineno(node), dynamic])

    try:
        tree = parse(data)
    except (SyntaxError, UnicodeError, ValueError, RecursionError):
        issue(ast.Pass(), None, 'source AST unavailable');return events
    if sum(1 for _ in ast.walk(tree)) > 100000:
        issue(tree, None, 'source AST complexity limit');return events

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.aliases = {};self.tainted = set()

        def scope(self, node):
            saved = self.aliases, self.tainted;self.aliases = dict(self.aliases);self.tainted = set(self.tainted)
            body = node.body if hasattr(node, 'body') else []
            # Binding analysis is deliberately conservative across branches/order.
            imports = {};writes = defaultdict(int);alias_dependencies = {}

            def bindings(nodes, conditional=False):
                for n in nodes:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                        if hasattr(n, 'name'):
                            writes[n.name] += 1
                        continue
                    if isinstance(n, (ast.Assign, ast.AnnAssign)):
                        value = n.value
                        alias = None
                        if isinstance(value, ast.Name) and (value.id == '__import__' or self.aliases.get(value.id) in {'function', 'builtin'} or imports.get(value.id) in {'function', 'builtin'}):
                            alias = 'builtin' if value.id == '__import__' else imports.get(value.id, self.aliases.get(value.id))
                        elif isinstance(value, ast.Attribute) and value.attr == 'import_module':
                            alias = 'function' if isinstance(value.value, ast.Name) and imports.get(value.value.id, self.aliases.get(value.value.id)) == 'module' else 'unknown'
                        for target in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                            if alias and isinstance(target, ast.Name):
                                imports[target.id] = alias
                                alias_dependencies[target.id] = value.id if isinstance(value, ast.Name) else value.value.id if isinstance(value.value, ast.Name) else None
                            if isinstance(target, ast.Attribute) and target.attr == 'path':
                                issue(n, None, 'runtime search path assignment')
                    if isinstance(n, ast.Import):
                        for a in n.names:
                            name = a.asname or a.name.split('.')[0];writes[name] += 1
                            if a.name == 'importlib':
                                imports[name] = 'module'
                            if conditional:
                                writes[name] += 2
                    elif isinstance(n, ast.ImportFrom):
                        for a in n.names:
                            name = a.asname or a.name;writes[name] += 1
                            if n.module == 'importlib' and a.name == 'import_module':
                                imports[name] = 'function'
                            if n.module == 'builtins' and a.name == '__import__':
                                imports[name] = 'builtin'
                            if conditional:
                                writes[name] += 2
                    elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                        writes[n.id] += 1
                    elif isinstance(n, (ast.Global, ast.Nonlocal)):
                        for name in n.names:
                            writes[name] += 2
                    elif isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) and isinstance(n.value, ast.Name):
                        writes[n.value.id] += 2
                    if conditional and isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                        writes[n.id] += 2
                    bindings(list(ast.iter_child_nodes(n)), conditional or isinstance(n, (ast.If, ast.Try, ast.For, ast.While, ast.With, ast.Match)))
            bindings(body)
            if hasattr(node, 'args'):
                for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                    writes[arg.arg] += 2
                for arg in (node.args.vararg, node.args.kwarg):
                    if arg:
                        writes[arg.arg] += 2
            self.tainted.update(name for name, count in writes.items() if count > 1 or name not in imports)
            self.tainted.difference_update(name for name, count in writes.items() if count == 1 and name in imports)
            self.aliases.update(imports)
            while True:
                tainted = {name for name, dependency in alias_dependencies.items() if dependency in self.tainted or imports[name] == 'unknown'}
                if tainted <= self.tainted:
                    break
                self.tainted.update(tainted)
            for n in body:
                self.visit(n)
            self.aliases, self.tainted = saved
        visit_Module = scope
        visit_FunctionDef = scope
        visit_AsyncFunctionDef = scope
        visit_ClassDef = scope

        def visit_Lambda(self, node):
            if any(isinstance(n, ast.Call) for n in ast.walk(node)):
                issue(node, None, 'lambda dynamic binding context unsupported')

        def visit_Import(self, node):
            for a in node.names:
                resolve(a.name, node)

        def visit_ImportFrom(self, node):
            base = node.module or ''
            if node.level:
                parts = module.split('.') if package else module.split('.')[:-1]
                if node.level > len(parts):
                    issue(node, base, 'relative import escapes package');return
                base = '.'.join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            resolve(base, node)
            for a in node.names:
                if a.name != '*':
                    events.append(['m', base + '.' + a.name if base else a.name, _lineno(node)])

        def primitive(self, node):
            return isinstance(node, ast.Name) and (node.id == '__import__' or self.aliases.get(node.id) in {'function', 'builtin', 'module', 'unknown'}) or isinstance(node, ast.Attribute) and node.attr in {'import_module', '__import__'}

        def visit_Assign(self, node):
            if isinstance(node.value, (ast.List, ast.Tuple, ast.Set, ast.Dict)) and any(self.primitive(n) for n in ast.walk(node.value)):
                issue(node, None, 'import primitive escapes through a container')
            self.generic_visit(node)

        def visit_Return(self, node):
            if self.primitive(node.value):
                issue(node, None, 'import primitive escapes through a return value')
            self.generic_visit(node)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'getattr' and node.args and (self.primitive(node.args[0]) or len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value in {'import_module', '__import__'}):
                issue(node, None, 'reflective import primitive binding is unsupported')
            if any(self.primitive(n) for n in [*node.args, *(k.value for k in node.keywords)]):
                issue(node, None, 'import primitive escapes as a call argument')
            fn = node.func;recognized = False;suspected = False
            if isinstance(fn, ast.Name):
                kind = self.aliases.get(fn.id)
                recognized = (fn.id == '__import__' or kind in {'function', 'builtin'}) and fn.id not in self.tainted
                suspected = fn.id == '__import__' or kind in {'function', 'builtin', 'unknown'}
            elif isinstance(fn, ast.Attribute):
                recognized = isinstance(fn.value, ast.Name) and self.aliases.get(fn.value.id) == 'module' and fn.value.id not in self.tainted and fn.attr == 'import_module'
                suspected = fn.attr in {'import_module', '__import__', 'exec_module', 'load_module', 'spec_from_file_location'}
                if fn.attr in {'append', 'insert', 'extend'} and isinstance(fn.value, ast.Attribute) and fn.value.attr == 'path':
                    issue(node, None, 'runtime search path mutation')
            if recognized:
                target = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
                if isinstance(target, str) and target.startswith('.'):
                    package_name = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else next((k.value.value for k in node.keywords if k.arg == 'package' and isinstance(k.value, ast.Constant)), None)
                    if isinstance(package_name, str):
                        level = len(target) - len(target.lstrip('.'));parts = package_name.split('.')
                        target = '.'.join(parts[:len(parts) - level + 1] + [target.lstrip('.')]) if level <= len(parts) else None
                    else:
                        target = None
                if isinstance(fn, ast.Name) and (fn.id == '__import__' or self.aliases.get(fn.id) == 'builtin'):
                    level = next((k.value.value if isinstance(k.value, ast.Constant) else None for k in node.keywords if k.arg == 'level'), 0)
                    if len(node.args) > 4:
                        level = node.args[4].value if isinstance(node.args[4], ast.Constant) else None
                    if level != 0 or len(node.args) > 1 or any(k.arg in {'globals', 'locals'} for k in node.keywords):
                        issue(node, target, 'builtin import context/relative level is unsupported');target = None
                resolve(target, node, True)
            elif suspected:
                issue(node, None, 'dynamic import binding is shadowed or unsupported')
            if isinstance(fn, ast.Name) and fn.id in {'eval', 'exec'}:
                issue(node, None, 'runtime code execution can introduce imports')
            self.generic_visit(node)
    Visitor().visit(tree)
    return events
