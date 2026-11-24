"""Pure source analysis: per-file import/endpoint edges and repository-layout resolution.

Self-contained (standard library only) and free of graph state, so any analyser can reuse it:

- ``script_edges(locator, text)`` / ``python_endpoint_edges(source)``: file path and content ->
  ``Edge`` tuples (relation, target, value, byte span, text). Script specifiers are recorded as
  written; relative ones are normalized against the importing file.
- ``Layout(locators, read_text)``: the repository shape (the set of tracked paths plus a reader
  for manifests) -> which specifiers or dotted modules reach a file: Python import roots (regular
  packages, ``src`` layouts, uv/poetry workspace members), workspace packages (``package.json``
  ``name``/``exports``/``main`` for pnpm, yarn and npm) and ``tsconfig``/``jsconfig``
  ``paths``/``baseUrl`` with ``extends``.
"""

import ast
import json
import posixpath
import re
from collections.abc import Callable
from typing import Any, NamedTuple

CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".vue",
        ".svelte",
        ".astro",
    }
)
SCRIPT_SUFFIXES = CODE_SUFFIXES - {".py"}
SCRIPT_EXTENSIONS = (*sorted(SCRIPT_SUFFIXES), ".d.ts")
UNIT_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "package.json")
MAX_CONFIGS = 400


class Edge(NamedTuple):
    """One observed edge: ``relation`` is imports, serves-endpoint or calls-endpoint; ``target``
    is ``path:<repo path>``, ``import:<specifier>`` or ``endpoint:<normalized path>``."""

    relation: str
    target: str
    value: Any
    start: int
    end: int
    text: str


# ESM static imports/re-exports, side-effect imports, dynamic import(), CommonJS require() and
# TypeScript `import x = require()`. Comments are blanked first so commented-out code is inert.
_SCRIPT_IMPORT = re.compile(
    r"""(?:\bimport\s+(?:type\s+)?(?:[\w$*{}\s,]+?\s+from\s+)?"""
    r"""|\bexport\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s+from\s+"""
    r"""|\bimport\s*\(\s*|\brequire\s*\(\s*)(["'`])([^"'`\n]+)\1"""
)
_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\\\w\"'])//[^\n]*", re.S)
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_SERVER_RECEIVER = re.compile(
    r"^(?:app|api|router|server|routes?|fastify|hono|\w*router|\w*app)$", re.I
)
# fetch("/x"), axios.get("/x"), client.post(`/x/${id}`), $fetch("/x"), useFetch("/x")
_SCRIPT_CALL = re.compile(
    r"""(?:\b([\w$]+)\s*\.\s*(get|post|put|patch|delete|head|options|request)"""
    r"""|\b(fetch|\$fetch|useFetch|ofetch|ky|axios|request))\s*(?:<[^>()]*>)?\s*\(\s*"""
    r"""(["'`])([^"'`\n]*)\4(\s*,\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[\w$]+\s*=>))?"""
)
_PY_ROUTE_ATTRS = {*_HTTP_METHODS, "route", "api_route", "websocket", "api_view"}
_PY_CALL_ATTRS = {*_HTTP_METHODS, "request", "stream", "fetch", "urlopen"}


def endpoint_path(value: str) -> str | None:
    """Normalize a literal URL or route to a comparable path, or None when it is not one."""
    value = value.strip()
    match = re.match(r"^(?:[a-z][a-z0-9+.-]*:)?//[^/]*(/.*)?$", value, re.I)
    if match:
        value = match.group(1) or "/"
    elif value.startswith("{}") or value.startswith("${"):
        # A base URL from configuration followed by a path: keep the path.
        value = re.sub(r"^(?:\$\{[^}]*\}|\{\})", "", value)
    if not value.startswith("/"):
        return None
    value = re.split(r"[?#]", value, maxsplit=1)[0]
    value = re.sub(r"\$\{[^}]*\}|\{[^}/]*\}|<[^>/]*>|(?<=/):[A-Za-z_]\w*", "{}", value)
    value = re.sub(r"/+", "/", value).rstrip("/") or "/"
    if not re.search(r"[A-Za-z0-9]", value) or len(value) > 512:
        return None
    return value


def _byte_offsets(text: str):
    if text.isascii():
        return lambda index: index
    return lambda index: len(text[:index].encode())


def script_edges(locator: str, text: str) -> list[Edge]:
    """Imports and endpoint use of a JavaScript/TypeScript (or component) source."""
    blank = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m[0]), text)
    offset = _byte_offsets(text)
    assertions, seen = [], set()
    directory = posixpath.dirname(locator)
    for match in _SCRIPT_IMPORT.finditer(blank):
        specifier = match.group(2)
        if "${" in specifier:
            continue
        if specifier.startswith("."):
            target = posixpath.normpath(posixpath.join(directory, specifier))
            obj = f"import:{specifier}" if target.startswith("..") else f"path:{target}"
        else:
            obj = f"import:{specifier}"
        key = ("imports", obj, match.start())
        if key in seen:
            continue
        seen.add(key)
        start, end = offset(match.start()), offset(match.end())
        assertions.append(
            Edge(
                "imports",
                obj,
                {"specifier": specifier},
                start,
                end,
                text[match.start() : match.end()],
            )
        )
    for match in _SCRIPT_CALL.finditer(blank):
        receiver, method, function = match.group(1), match.group(2), match.group(3)
        path = endpoint_path(match.group(5))
        if not path:
            continue
        serves = bool(
            method
            and method != "request"
            and (_SERVER_RECEIVER.match(receiver or "") or match.group(6))
        )
        start, end = offset(match.start()), offset(match.end())
        assertions.append(
            Edge(
                "serves-endpoint" if serves else "calls-endpoint",
                f"endpoint:{path}",
                {"method": (method or function or "").lower()},
                start,
                end,
                text[match.start() : match.end()],
            )
        )
    return assertions


def _literal(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None or right is not None:
            return (left if left is not None else "{}") + (right if right is not None else "{}")
    return None


def python_endpoint_edges(raw: bytes, tree=None) -> list[Edge]:
    """Routes a Python module serves (decorators) and endpoints it calls (HTTP clients)."""
    if tree is None:
        tree = ast.parse(raw)
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    assertions, decorators = [], set()

    def add(predicate, node, path, method):
        # ast column offsets are UTF-8 byte offsets within the line.
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        start, end = min(start, len(raw)), min(max(end, start), len(raw))
        assertions.append(
            Edge(
                predicate,
                f"endpoint:{path}",
                {"method": method},
                start,
                end,
                raw[start:end].decode("utf-8", "ignore")[:1024],
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in _PY_ROUTE_ATTRS
                    and decorator.args
                ):
                    decorators.add(id(decorator))
                    path = endpoint_path(_literal(decorator.args[0]) or "")
                    if path:
                        add("serves-endpoint", decorator, path, decorator.func.attr)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in decorators or not node.args:
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name not in _PY_CALL_ATTRS:
            continue
        argument = node.args[1] if name == "request" and len(node.args) > 1 else node.args[0]
        path = endpoint_path(_literal(argument) or "")
        if path:
            add("calls-endpoint", node, path, name)
    return assertions


def segments_match(left, right):
    """Same route, allowing a gateway/base prefix on either side (two or more segments)."""
    a, b = left.strip("/").split("/"), right.strip("/").split("/")
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 2 and len(a) != len(b):
        return False
    tail = longer[len(longer) - len(shorter) :]
    return all(x == y or "{}" in (x, y) for x, y in zip(shorter, tail, strict=True))


def parse_jsonc(text: str):
    """JSON with comments and trailing commas, as tsconfig/jsconfig allow."""
    text = re.sub(r"/\*.*?\*/|(?<![:\"\w])//[^\n]*", "", text, flags=re.S)
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def strip_ext(locator: str) -> str:
    for extension in (".d.ts", *CODE_SUFFIXES):
        if locator.endswith(extension):
            return locator[: -len(extension)]
    return locator


def ancestors(locator: str):
    parent = posixpath.dirname(locator)
    while True:
        yield parent
        if not parent:
            return
        parent = posixpath.dirname(parent)


class Layout:
    """The repository shape a resolver needs: tracked paths and a manifest reader."""

    def __init__(self, locators, read_text: Callable[[str], str] | None = None):
        self.read_text = read_text
        self.locators = frozenset(locators)
        self.directories = {a for name in self.locators for a in ancestors(name)}
        self.units = {
            posixpath.dirname(name)
            for name in self.locators
            if posixpath.basename(name) in UNIT_MANIFESTS
        }
        self._json, self.unreadable = {}, set()

    def unit(self, locator):
        return next((a for a in ancestors(locator) if a in self.units), "")

    def read_json(self, locator):
        if locator not in self._json:
            try:
                if self.read_text is None:
                    raise ValueError("no manifest reader")
                self._json[locator] = parse_jsonc(self.read_text(locator))
            except (OSError, ValueError):
                self.unreadable.add(locator)
                self._json[locator] = None
        return self._json[locator]

    # Python ---------------------------------------------------------------------------------
    def python_roots(self, locator):
        """Import roots for a file, most specific first: the regular-package root, `src`
        layout directories, unit (pyproject/setup) roots and the repository root."""
        roots = []
        directory = posixpath.dirname(locator)
        top = None
        while directory and posixpath.join(directory, "__init__.py") in self.locators:
            top = directory
            directory = posixpath.dirname(directory)
        if top is not None:
            roots.append(posixpath.dirname(top))
        for ancestor in ancestors(locator):
            if ancestor in self.units:
                # An installed src-layout unit puts its own `src` on the path for its tests.
                source = posixpath.join(ancestor, "src") if ancestor else "src"
                if source in self.directories:
                    roots.append(source)
            if posixpath.basename(ancestor) == "src" or ancestor in self.units or not ancestor:
                roots.append(ancestor)
        return list(dict.fromkeys(roots))

    def python_names(self, locator):
        """(root, dotted module) pairs under which `locator` is importable."""
        stem = locator[:-3]
        if stem.endswith("/__init__") or stem == "__init__":
            stem = posixpath.dirname(stem)
        names = []
        for root in self.python_roots(locator):
            relative = stem[len(root) + 1 :] if root else stem
            if relative and all(part.isidentifier() for part in relative.split("/")):
                names.append((root, relative.replace("/", ".")))
        return names

    def python_owner(self, importer, module):
        """The root an importer's own layout resolves `module` against, or None if outside."""
        first = module.split(".", 1)[0]
        for root in self.python_roots(importer):
            base = posixpath.join(root, first) if root else first
            if base in self.directories or base + ".py" in self.locators:
                return root
        return None

    # Scripts --------------------------------------------------------------------------------
    def script_specifiers(self, locator):
        """Bare specifiers (with the importer scope they are valid in) that reach `locator`."""
        stem = strip_ext(locator)
        targets = {stem}
        if posixpath.basename(stem) == "index":
            targets.add(posixpath.dirname(stem))
        found = set()
        for package in (a for a in ancestors(locator) if a in self.units):
            manifest = posixpath.join(package, "package.json") if package else "package.json"
            data = self.read_json(manifest) if manifest in self.locators else None
            if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                continue
            found.update((spec, "") for spec in _package_specifiers(data, package, locator))
            break
        for config in self._configs():
            # An alias is valid for importers under its config; its target may lie elsewhere.
            scope = posixpath.dirname(config)
            options = self._compiler_options(config)
            base_url = options.get("baseUrl")
            paths = options.get("paths") or {}
            base = posixpath.normpath(posixpath.join(scope, base_url or "."))
            base = "" if base == "." else base
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
                    if base and target.startswith(base + "/"):
                        found.add((target[len(base) + 1 :], scope))
                    elif not base:
                        found.add((target, scope))
        return found

    def _configs(self):
        configs = sorted(
            name
            for name in self.locators
            if re.fullmatch(r"(?:tsconfig|jsconfig)(?:\.[\w-]+)?\.json", posixpath.basename(name))
        )
        return configs[:MAX_CONFIGS]

    def _compiler_options(self, config, depth=0):
        data = self.read_json(config)
        if not isinstance(data, dict):
            return {}
        options = {}
        parent = data.get("extends")
        if isinstance(parent, str) and parent.startswith(".") and depth < 4:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(config), parent))
            if not target.endswith(".json"):
                target += ".json"
            inherited = self._compiler_options(target, depth + 1) if target in self.locators else {}
            # Inherited baseUrl/paths are relative to the config that declared them.
            relative = posixpath.relpath(
                posixpath.dirname(target) or ".", posixpath.dirname(config) or "."
            )
            if "baseUrl" in inherited:
                inherited["baseUrl"] = posixpath.normpath(
                    posixpath.join(relative, inherited["baseUrl"])
                )
            elif "paths" in inherited:
                inherited["baseUrl"] = relative
            options.update(inherited)
        own = data.get("compilerOptions")
        if isinstance(own, dict):
            options.update({k: own[k] for k in ("baseUrl", "paths") if k in own})
        return options


def _invert(alias, pattern, target):
    """The specifier `alias` produces for `target` through `pattern`, if it does."""
    pattern = strip_ext(pattern)
    if "*" in alias and "*" in pattern:
        prefix, suffix = pattern.split("*", 1)
        if target.startswith(prefix) and target.endswith(suffix):
            middle = target[len(prefix) : len(target) - len(suffix) if suffix else None]
            if middle:
                return alias.replace("*", middle, 1)
        return None
    return alias if "*" not in alias and pattern == target else None


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
    name, stem = data["name"], strip_ext(locator)
    relative = locator[len(package) + 1 :] if package else locator
    specs = set()
    exports = data.get("exports")
    if isinstance(exports, (str, list)) or (
        isinstance(exports, dict) and not any(k.startswith(".") for k in exports)
    ):
        exports = {".": exports}
    if isinstance(exports, dict):
        for key, value in exports.items():
            if not key.startswith("."):
                continue
            subpath = name + key[1:]
            for target in _export_targets(value):
                pattern = strip_ext(posixpath.normpath(posixpath.join(package, target)))
                spec = _invert(subpath, pattern, stem)
                if spec:
                    specs.add(spec)
        return specs
    for field in ("source", "module", "main", "types", "typings", "browser"):
        value = data.get(field)
        if isinstance(value, str):
            entry = strip_ext(posixpath.normpath(posixpath.join(package, value)))
            if entry in {stem, posixpath.dirname(stem) if stem.endswith("/index") else None}:
                specs.add(name)
    if posixpath.basename(stem) == "index" and posixpath.dirname(stem) == package:
        specs.add(name)
    # Without an exports map every file is importable by deep path.
    specs.add(f"{name}/{strip_ext(relative)}")
    specs.add(f"{name}/{relative}")
    if posixpath.basename(stem) == "index":
        specs.add(f"{name}/{posixpath.dirname(strip_ext(relative))}".rstrip("/"))
    return specs
