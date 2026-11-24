"""Code facts (#34): import/endpoint edges of a repository and who consumes a path, module or endpoint.

One analyser serves AL and the Harness knowledge graph. Per-file edges are pure facts of one
blob (`al_blob_analysis.code_edges`), stored in the shared fact store (`al_analysis_index`)
keyed by the file's path and Git blob id, so a warm query reads them instead of parsing. This
module adds the repository-dependent part: the layout (`Layout`) that says which specifiers or
dotted modules reach a file (Python import roots for regular packages, `src` layouts and
uv/poetry workspace members; workspace packages from `package.json` `name`/`exports`/`main`
for pnpm, yarn and npm; `tsconfig`/`jsconfig` `paths`/`baseUrl` with `extends`), and the
consumer inventory resolved through it.

The repository view is the working copy as `al_snapshot.snapshot()` captures it (the resident
service (#27) answers that tree from memory and keeps fact-store pack headers in memory), or
the tree of `--commit`. Generated and vendored directories (`al_snapshot.EXCLUDED`) are left
out. Read-only: the commands record nothing in any run. Output is canonical JSON, so the
Harness knowledge graph, which queries `alctl run ... -- facts ...` and never imports AL code,
relays exactly these bytes' content.

    facts edges [PATH ...] [--commit REV]
    facts consumers TARGET [--commit REV] [--limit N]
"""
import json
import posixpath
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_blob_analysis as blob  # noqa: E402

SCHEMA = 1
NAMESPACE = 'code-edges'
UNIT_MANIFESTS = ('pyproject.toml', 'setup.py', 'setup.cfg', 'package.json')
MAX_CONFIGS = 400
FILE_BYTES = 2 * 1024 * 1024
BATCH_BYTES = 32 * 1024 * 1024
DEFAULT_LIMIT = 200
# Re-exporting modules (index.ts barrels, `export * from`) are followed this many levels.
REEXPORT_DEPTH = 5
_CONFIG = re.compile(r'(?:tsconfig|jsconfig)(?:\.[\w-]+)?\.json')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


# ---------------------------------------------------------------- layout

def parse_jsonc(text):
    """JSON with comments and trailing commas, as tsconfig/jsconfig allow."""
    text = re.sub(r'/\*.*?\*/|(?<![:"\w])//[^\n]*', '', text, flags=re.S)
    return json.loads(re.sub(r',(\s*[}\]])', r'\1', text))


def strip_ext(locator):
    for extension in ('.d.ts', *blob.CODE_SUFFIXES):
        if locator.endswith(extension):
            return locator[:-len(extension)]
    return locator


def ancestors(locator):
    parent = posixpath.dirname(locator)
    while True:
        yield parent
        if not parent:
            return
        parent = posixpath.dirname(parent)


class Layout:
    """The repository shape a resolver needs: tracked paths and a manifest reader."""

    def __init__(self, locators, read_text=None):
        self.read_text = read_text
        self.locators = frozenset(locators)
        self.directories = {a for name in self.locators for a in ancestors(name)}
        self.units = {posixpath.dirname(n) for n in self.locators if posixpath.basename(n) in UNIT_MANIFESTS}
        self._json, self.unreadable = {}, set()

    def unit(self, locator):
        return next((a for a in ancestors(locator) if a in self.units), '')

    def read_json(self, locator):
        if locator not in self._json:
            try:
                if self.read_text is None:
                    raise ValueError('no manifest reader')
                self._json[locator] = parse_jsonc(self.read_text(locator))
            except (OSError, ValueError, KeyError):
                self.unreadable.add(locator)
                self._json[locator] = None
        return self._json[locator]

    # Python
    def python_roots(self, locator):
        """Import roots for a file, most specific first: the regular-package root, `src`
        layout directories, unit (pyproject/setup) roots and the repository root."""
        roots = []
        directory = posixpath.dirname(locator)
        top = None
        while directory and posixpath.join(directory, '__init__.py') in self.locators:
            top = directory
            directory = posixpath.dirname(directory)
        if top is not None:
            roots.append(posixpath.dirname(top))
        for ancestor in ancestors(locator):
            if ancestor in self.units:
                # An installed src-layout unit puts its own `src` on the path for its tests.
                source = posixpath.join(ancestor, 'src') if ancestor else 'src'
                if source in self.directories:
                    roots.append(source)
            if posixpath.basename(ancestor) == 'src' or ancestor in self.units or not ancestor:
                roots.append(ancestor)
        return list(dict.fromkeys(roots))

    def python_names(self, locator):
        """(root, dotted module) pairs under which `locator` is importable."""
        stem = locator[:-3]
        if stem.endswith('/__init__') or stem == '__init__':
            stem = posixpath.dirname(stem)
        names = []
        for root in self.python_roots(locator):
            relative = stem[len(root) + 1:] if root else stem
            if relative and all(part.isidentifier() for part in relative.split('/')):
                names.append((root, relative.replace('/', '.')))
        return names

    def python_owner(self, importer, module):
        """The root an importer's own layout resolves `module` against, or None if outside."""
        first = module.split('.', 1)[0]
        for root in self.python_roots(importer):
            base = posixpath.join(root, first) if root else first
            if base in self.directories or base + '.py' in self.locators:
                return root
        return None

    # Scripts
    def script_specifiers(self, locator):
        """Bare specifiers (with the importer scope they are valid in) that reach `locator`."""
        stem = strip_ext(locator)
        targets = {stem}
        if posixpath.basename(stem) == 'index':
            targets.add(posixpath.dirname(stem))
        found = set()
        for package in (a for a in ancestors(locator) if a in self.units):
            manifest = posixpath.join(package, 'package.json') if package else 'package.json'
            data = self.read_json(manifest) if manifest in self.locators else None
            if not isinstance(data, dict) or not isinstance(data.get('name'), str):
                continue
            found.update((spec, '') for spec in _package_specifiers(data, package, locator))
            break
        for config in self._configs():
            # An alias is valid for importers under its config; its target may lie elsewhere.
            scope = posixpath.dirname(config)
            options = self._compiler_options(config)
            base_url = options.get('baseUrl')
            paths = options.get('paths') or {}
            base = posixpath.normpath(posixpath.join(scope, base_url or '.'))
            base = '' if base == '.' else base
            for alias, replacements in paths.items() if isinstance(paths, dict) else ():
                for replacement in replacements if isinstance(replacements, list) else ():
                    if not isinstance(replacement, str):
                        continue
                    pattern = posixpath.normpath(posixpath.join(base, replacement))
                    for target in targets:
                        spec = _invert(alias, pattern, target)
                        if spec:
                            found.add((spec, scope))
            if base_url is not None:
                for target in targets:
                    if base and target.startswith(base + '/'):
                        found.add((target[len(base) + 1:], scope))
                    elif not base:
                        found.add((target, scope))
        return found

    def _configs(self):
        return sorted(n for n in self.locators if _CONFIG.fullmatch(posixpath.basename(n)))[:MAX_CONFIGS]

    def _compiler_options(self, config, depth=0):
        data = self.read_json(config)
        if not isinstance(data, dict):
            return {}
        options = {}
        parent = data.get('extends')
        if isinstance(parent, str) and parent.startswith('.') and depth < 4:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(config), parent))
            if not target.endswith('.json'):
                target += '.json'
            inherited = self._compiler_options(target, depth + 1) if target in self.locators else {}
            # Inherited baseUrl/paths are relative to the config that declared them.
            relative = posixpath.relpath(posixpath.dirname(target) or '.', posixpath.dirname(config) or '.')
            if 'baseUrl' in inherited:
                inherited['baseUrl'] = posixpath.normpath(posixpath.join(relative, inherited['baseUrl']))
            elif 'paths' in inherited:
                inherited['baseUrl'] = relative
            options.update(inherited)
        own = data.get('compilerOptions')
        if isinstance(own, dict):
            options.update({k: own[k] for k in ('baseUrl', 'paths') if k in own})
        return options


def _invert(alias, pattern, target):
    """The specifier `alias` produces for `target` through `pattern`, if it does."""
    pattern = strip_ext(pattern)
    if '*' in alias and '*' in pattern:
        prefix, suffix = pattern.split('*', 1)
        if target.startswith(prefix) and target.endswith(suffix):
            middle = target[len(prefix):len(target) - len(suffix) if suffix else None]
            if middle:
                return alias.replace('*', middle, 1)
        return None
    return alias if '*' not in alias and pattern == target else None


def _export_targets(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _export_targets(item)
    elif isinstance(value, list):
        for item in value:
            yield from _export_targets(item)


def _package_specifiers(data, package, locator):
    name, stem = data['name'], strip_ext(locator)
    relative = locator[len(package) + 1:] if package else locator
    specs = set()
    exports = data.get('exports')
    if isinstance(exports, (str, list)) or (isinstance(exports, dict) and not any(k.startswith('.') for k in exports)):
        exports = {'.': exports}
    if isinstance(exports, dict):
        for key, value in exports.items():
            if not key.startswith('.'):
                continue
            subpath = name + key[1:]
            for target in _export_targets(value):
                pattern = strip_ext(posixpath.normpath(posixpath.join(package, target)))
                spec = _invert(subpath, pattern, stem)
                if spec:
                    specs.add(spec)
        return specs
    for field in ('source', 'module', 'main', 'types', 'typings', 'browser'):
        value = data.get(field)
        if isinstance(value, str):
            entry = strip_ext(posixpath.normpath(posixpath.join(package, value)))
            if entry in {stem, posixpath.dirname(stem) if stem.endswith('/index') else None}:
                specs.add(name)
    if posixpath.basename(stem) == 'index' and posixpath.dirname(stem) == package:
        specs.add(name)
    # Without an exports map every file is importable by deep path.
    specs.add(f'{name}/{strip_ext(relative)}')
    specs.add(f'{name}/{relative}')
    if posixpath.basename(stem) == 'index':
        specs.add(f'{name}/{posixpath.dirname(strip_ext(relative))}'.rstrip('/'))
    return specs


def is_code(locator):
    return PurePosixPath(locator).suffix.lower() in blob.CODE_SUFFIXES


def is_script(locator):
    return locator.endswith(blob.SCRIPT_EXTENSIONS)


def resolve_target(layout, target):
    """Map a locator, dotted module, directory or endpoint to locators and endpoints."""
    target = target.strip()
    if target.startswith('endpoint:') or (target.startswith('/') and target not in layout.locators):
        path = blob.endpoint_path(target.removeprefix('endpoint:'))
        return [], [path] if path else []
    target = target.removeprefix('file:').removeprefix('module:').strip('/')
    if target in layout.locators:
        return [target], []
    if target in layout.directories:
        prefix = target + '/'
        return sorted(n for n in layout.locators if n.startswith(prefix) and is_code(n)), []
    stems = [n for n in layout.locators if n.endswith('.py') or is_script(n)]
    matches = sorted(n for n in stems if strip_ext(n) == target)
    if matches:
        return matches, []
    if re.fullmatch(r'[A-Za-z_][\w.]*', target):
        return sorted(n for n in stems if n.endswith('.py')
                      and any(name == target for _, name in layout.python_names(n))), []
    return [], []


# ---------------------------------------------------------------- repository view

def _generated(locator):
    import al_snapshot
    return any(part in al_snapshot.EXCLUDED for part in locator.split('/')[:-1])


def _layout_input(locator):
    name = posixpath.basename(locator)
    return is_code(locator) or name in UNIT_MANIFESTS or bool(_CONFIG.fullmatch(name))


class View:
    """The files of one repository tree: paths, blob ids, lazily read bytes and edges."""

    def __init__(self, root, commit=None):
        import al_snapshot
        self.root = Path(root)
        if commit:
            tree = al_snapshot._run(self.root, ['rev-parse', '--verify', '--end-of-options', commit + '^{tree}'])
            self.tree = tree.decode().strip()
        else:
            self.tree = al_snapshot.snapshot(self.root)
        self.files, self.gaps = {}, []
        for path, (mode, kind, oid, size) in al_snapshot.entries(self.root, self.tree).items():
            if kind != 'blob' or mode not in ('100644', '100755') or _generated(path) or not _layout_input(path):
                continue
            if size is None or size > FILE_BYTES:
                self.gaps.append({'locator': path, 'status': 'unresolved', 'reason': 'file-too-large'})
                continue
            self.files[path] = oid
        self._bytes = {}
        self._edges = None

    def read(self, paths):
        """{path: bytes} of the given view files, read from Git in bounded batches."""
        import al_snapshot
        wanted = [p for p in dict.fromkeys(paths) if p in self.files and p not in self._bytes]
        batch, size = [], 0
        for path in wanted + [None]:
            if path is not None:
                batch.append(path)
                size += FILE_BYTES
            if batch and (path is None or size >= BATCH_BYTES):
                data = b''.join(self.files[p].encode() + b'\n' for p in batch)
                raw = al_snapshot._run(self.root, ['cat-file', '--batch'], data=data, timeout=600)
                offset = 0
                for name in batch:
                    end = raw.index(b'\n', offset)
                    header = raw[offset:end].split()
                    if len(header) != 3 or header[1] != b'blob':
                        raise ValueError('invalid Git blob frame for ' + name)
                    length = int(header[2])
                    self._bytes[name] = raw[end + 1:end + 1 + length]
                    offset = end + 2 + length
                batch, size = [], 0
        return {p: self._bytes[p] for p in paths if p in self._bytes}

    def read_text(self, path):
        if path not in self.files:
            raise KeyError(path)
        return self.read([path])[path].decode()

    def edges(self):
        """{path: {'status', 'edges'}} of every code file, through the fact store."""
        if self._edges is None:
            import al_analysis_index as index
            paths = sorted(p for p in self.files if is_code(p))
            keys = [[SCHEMA, p, self.files[p]] for p in paths]

            def compute(missing):
                data = self.read([paths[i] for i in missing])
                return {i: blob.code_edges(paths[i], data[paths[i]]) for i in missing}

            # Per-file edges are a pure function of the path and bytes (al_blob_analysis alone).
            code = index.code_digest('al_blob_analysis')
            values = index.facts(NAMESPACE, code, keys, compute, root=self.root, compress=True)
            self._edges = dict(zip(paths, values))
        return self._edges

    def layout(self):
        return Layout(self.files, self.read_text)


def _gaps(view):
    gaps = list(view.gaps)
    for path, value in sorted(view.edges().items()):
        if value['status'] not in ('ok', 'unsupported'):
            gaps.append({'locator': path, 'status': 'unresolved', 'reason': value['status']})
    return gaps


def edge_rows(view, paths=()):
    """Edges of the code files under `paths` (all code files when empty), in file then byte order."""
    selected = []
    for path in sorted(view.edges()):
        if not paths or any(path == p.strip('/') or path.startswith(p.strip('/') + '/') for p in paths):
            selected.append(path)
    rows = []
    for path in selected:
        for relation, target, value, start, end, text in view.edges()[path]['edges']:
            rows.append({'locator': path, 'relation': relation, 'target': target, 'value': value,
                         'start': start, 'end': end, 'text': text})
    gaps = [g for g in _gaps(view) if not paths or g['locator'] in selected]
    # The Git blob id each file's edges were computed from, so a caller holding the bytes can
    # tell that the edges describe exactly them.
    return {'schema': SCHEMA, 'tree': view.tree, 'status': 'partial' if gaps else 'complete',
            'files': {path: view.files[path] for path in selected}, 'edges': rows, 'gaps': gaps}


def consumers(view, target, limit=DEFAULT_LIMIT):
    """Static importers and runtime (HTTP endpoint) callers of `target` in the view."""
    layout = view.layout()
    files, endpoints = resolve_target(layout, target)
    by_target = defaultdict(list)
    for path, value in view.edges().items():
        for edge in value['edges']:
            by_target[(edge[0], edge[1])].append((path, edge))
    found = {}

    def add(importer, edge, relation, via, owner, through=()):
        if importer in files or (importer, relation, via) in found:
            return
        row = {'locator': importer, 'unit': layout.unit(importer) or '.', 'relation': relation, 'via': via,
               'scope': ('runtime' if not owner else 'intra-unit' if layout.unit(importer) == layout.unit(owner)
                         else 'cross-unit'),
               'target': owner, 'start_byte': edge[3], 'end_byte': edge[4]}
        if through:
            row['through'] = list(through)
        found[(importer, relation, via)] = row

    def importers(relation, keys):
        return [(path, edge) for key in sorted(set(keys)) for path, edge in by_target.get((relation, key), ())]

    for locator in files:
        if locator.endswith('.py'):
            wanted = defaultdict(list)
            for root, name in layout.python_names(locator):
                wanted['module:' + name].append((root, name, None))
                if '.' in name:
                    parent, leaf = name.rsplit('.', 1)
                    wanted['module:' + parent].append((root, name, leaf))
            for importer, edge in importers('imports', wanted):
                imported = set((edge[2] or {}).get('names', [])) if isinstance(edge[2], dict) else set()
                for root, name, leaf in wanted[edge[1]]:
                    if leaf is not None and leaf not in imported:
                        continue
                    owner = layout.python_owner(importer, name)
                    if owner is not None and owner != root:
                        continue  # the importer's own layout resolves this name elsewhere
                    add(importer, edge, 'imports', name, locator)
                    break
        elif is_script(locator):
            pending, visited = [(locator, ())], {locator}
            while pending:
                current, through = pending.pop(0)
                relation = 'imports-via-reexport' if through else 'imports'
                for importer, edge, via in _script_importers(layout, importers, current):
                    add(importer, edge, relation, via, locator, through)
                    if (importer not in visited and importer not in files and len(through) < REEXPORT_DEPTH
                            and is_script(importer) and edge[5].lstrip().startswith('export')):
                        visited.add(importer)
                        pending.append((importer, (*through, importer)))
        for edge in view.edges().get(locator, {'edges': []})['edges']:
            if edge[0] == 'serves-endpoint':
                endpoints.append((edge[1].removeprefix('endpoint:'), locator))
    if endpoints:
        calls = sorted(((key[1], path, edge) for key, rows in by_target.items() if key[0] == 'calls-endpoint'
                        for path, edge in rows), key=lambda r: (r[1], r[2][3]))
        for item in endpoints:
            path, owner = item if isinstance(item, tuple) else (item, '')
            for called, importer, edge in calls:
                called = called.removeprefix('endpoint:')
                if called == path or blob.segments_match(called, path):
                    add(importer, edge, 'calls-endpoint', path, owner)
    gaps = _gaps(view)
    if layout.unreadable:
        gaps.append({'status': 'unresolved', 'reason': 'layout-config-unreadable',
                     'locators': sorted(layout.unreadable)[:20]})
    results = sorted(found.values(), key=lambda c: (c['scope'] != 'cross-unit', c['locator'], c['relation'], c['via']))
    return {'schema': SCHEMA, 'tree': view.tree, 'target': target, 'status': 'partial' if gaps else 'complete',
            'resolved': {'files': files[:50], 'endpoints': sorted({e if isinstance(e, str) else e[0] for e in endpoints})},
            'consumers': results[:limit], 'truncated': len(results) > limit, 'gaps': gaps}


def _script_importers(layout, importers, locator):
    """(importer, edge, specifier as written) of every static import of a script `locator`."""
    stem = strip_ext(locator)
    relative = {f'path:{stem}', f'path:{locator}'}
    relative.update(f'path:{stem}{extension}' for extension in blob.SCRIPT_EXTENSIONS)
    if posixpath.basename(stem) == 'index':
        relative.add(f'path:{posixpath.dirname(stem)}')
    rows = [(path, edge, edge[1].removeprefix('path:')) for path, edge in importers('imports', relative)]
    scopes = defaultdict(set)
    for spec, spec_scope in layout.script_specifiers(locator):
        scopes[f'import:{spec}'].add(spec_scope)
    for path, edge in importers('imports', scopes):
        allowed = scopes[edge[1]]
        if '' in allowed or any(path.startswith(s + '/') for s in allowed):
            rows.append((path, edge, edge[1].removeprefix('import:')))
    return rows


# ---------------------------------------------------------------- command

def register(sub):
    p = sub.add_parser('facts', help='read-only code facts: import/endpoint edges and consumers (#34)')
    action = p.add_subparsers(dest='action', required=True)
    edges = action.add_parser('edges', help='edges of code files under PATHs (all when none)')
    edges.add_argument('paths', nargs='*')
    edges.add_argument('--commit')
    found = action.add_parser('consumers', help='importers and endpoint callers of a path, module or endpoint')
    found.add_argument('target')
    found.add_argument('--commit')
    found.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    p.set_defaults(fn=cmd_facts)


def cmd_facts(a, sid=None):
    """Print one canonical JSON document; nothing is recorded."""
    import al_snapshot
    import al_store
    root = al_store.REPO_ROOT
    try:
        view = View(root, a.commit)
        if a.action == 'edges':
            result = edge_rows(view, a.paths)
        else:
            result = consumers(view, a.target, max(1, min(1000, a.limit)))
    except (al_snapshot.Unavailable, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit('al_code_facts: repository view unavailable: ' + str(error)[:500])
    finally:
        import al_analysis_index
        al_analysis_index.flush()
    sys.stdout.write(canonical(result) + '\n')


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='al_code_facts')
    register(parser.add_subparsers(dest='cmd', required=True))
    a = parser.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    main()
