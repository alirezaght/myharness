"""Bounded read-only ownership and current-main audit, shared with standalone AL.

Only explicit paths/package names select ownership. Keyword matches are leads,
never ownership or proof that behavior is implemented. No fetch or secret reads.
"""

import hashlib
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

EXCLUDED = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    "coverage",
    ".turbo",
    ".cache",
}
MANIFESTS = {"package.json", "pyproject.toml", "Cargo.toml", "go.mod"}
SOURCE = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".vue", ".svelte"}
MAX_FILES = 20000
MAX_BYTES = 262144


def _git(root, *args):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.decode(errors="replace").strip() if result.returncode == 0 else None


def _secret(path):
    return (
        path.name == ".env"
        or path.name.startswith(".env.")
        or path.suffix in {".pem", ".key", ".p12", ".pfx"}
        or path.name.startswith(("id_rsa", "id_ed25519", "id_ecdsa"))
    )


def _text(root, relative):
    path = root / relative
    if _secret(path) or any(p.is_symlink() for p in [path, *path.parents]):
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or b"\0" in raw:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def inventory(root):
    files = []
    complete = True
    for current, directories, names in os.walk(root, followlinks=False):
        base = Path(current)
        directories[:] = sorted(
            d
            for d in directories
            if d not in EXCLUDED
            and not (base / d).is_symlink()
            and not d.endswith((".egg-info", ".dist-info"))
        )
        for name in sorted(names):
            path = base / name
            if not path.is_symlink() and path.is_file():
                files.append(path.relative_to(root).as_posix())
                if len(files) == MAX_FILES:
                    complete = False
                    return sorted(files), complete
    return sorted(files), complete


def _named(text, name):
    return bool(name and re.search(r"(?<![\w/@.-])" + re.escape(name) + r"(?![\w/@.-])", text))


def audit(root, request, *, files=None):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not isinstance(request, str) or not request.strip():
        raise ValueError("repository directory and material request required")
    if files is None:
        files, complete = inventory(root)
    else:
        files = sorted(str(p) for p in files if not EXCLUDED.intersection(PurePosixPath(p).parts))
        complete = len(files) <= MAX_FILES
        files = files[:MAX_FILES]
    texts = {}
    units = []
    diagnostics = []
    for name in files:
        path = PurePosixPath(name)
        if path.name not in MANIFESTS:
            continue
        text = _text(root, name)
        if text is None:
            diagnostics.append("manifest unreadable: " + name)
            continue
        texts[name] = text
        try:
            if path.name == "package.json":
                package = json.loads(text).get("name")
            elif path.suffix == ".toml":
                data = tomllib.loads(text)
                package = data.get("project", data.get("package", {})).get("name")
            else:
                match = re.search(r"^module\s+(\S+)", text, re.M)
                package = match[1] if match else None
            package = package if isinstance(package, str) else None
        except (ValueError, AttributeError):
            diagnostics.append("manifest malformed: " + name)
            package = None
        units.append({"root": path.parent.as_posix(), "manifest": name, "name": package})
    seeds = [
        name
        for name in files
        if name != "AGENTS.md" and not _secret(PurePosixPath(name)) and _named(request, name)
    ]
    selected = []
    for unit in units:
        base = unit["root"]
        if (
            _named(request, unit["name"])
            or base != "."
            and _named(request, base)
            or any(base == "." or name.startswith(base + "/") for name in seeds)
        ):
            selected.append(unit)
    # With a single root package, repository ownership is explicit in its manifest.
    if not selected and len(units) == 1 and units[0]["root"] == ".":
        selected = units[:]
    relevant = set(seeds)
    for name in files:
        owners = [u for u in units if u["root"] == "." or name.startswith(u["root"] + "/")]
        owner = max(owners, key=lambda u: len(u["root"]), default=None)
        if owner in selected:
            relevant.add(name)
    # Follow literal relative imports from scoped production sources. Dynamic
    # imports/aliases remain an explicit limitation, never an inferred dependency.
    edges = []
    todo = sorted(relevant)
    visited = set()
    while todo:
        name = todo.pop()
        if name in visited or PurePosixPath(name).suffix not in SOURCE:
            continue
        visited.add(name)
        text = _text(root, name)
        if text is None:
            continue
        texts[name] = text
        for target in re.findall(
            r"(?:from\s*|import\s*\(|require\s*\()?[\"'](\.[^\"'\n]+)[\"']", text
        ):
            destination = (root / name).parent / target
            resolved = destination.resolve()
            if not resolved.is_relative_to(root):
                continue
            base = resolved.relative_to(root).as_posix()
            candidates = [
                base,
                *[base + s for s in sorted(SOURCE)],
                base + "/index.ts",
                base + "/index.js",
            ]
            found = next((c for c in candidates if c in files), None)
            if found:
                edges.append(
                    {"from": name, "to": found, "evidence": "literal relative import " + target}
                )
                relevant.add(found)
                todo.append(found)
    instructions = []
    for name in files:
        if PurePosixPath(name).name not in {"AGENTS.md", "CLAUDE.md"}:
            continue
        parent = PurePosixPath(name).parent
        if parent == PurePosixPath(".") or any(
            PurePosixPath(p).is_relative_to(parent) for p in relevant
        ):
            text = _text(root, name)
            if text is not None:
                texts[name] = text
                instructions.append(
                    {"path": name, "sha256": hashlib.sha256(text.encode()).hexdigest()}
                )
    head = _git(root, "rev-parse", "--verify", "HEAD")
    comparisons = []
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        revision = _git(root, "rev-parse", "--verify", ref)
        counts = (
            _git(root, "rev-list", "--left-right", "--count", "HEAD..." + ref) if revision else None
        )
        if head and counts and re.fullmatch(r"\d+\s+\d+", counts):
            ahead, behind = map(int, counts.split())
            comparisons.append({"ref": ref, "revision": revision, "ahead": ahead, "behind": behind})
    words = {w.lower() for w in re.findall(r"[A-Za-z_][\w-]{3,}", request)}
    leads = []
    for name in files:
        if PurePosixPath(name).suffix not in SOURCE:
            continue
        text = texts.get(name)
        if text is None:
            text = _text(root, name)
        if text is not None and any(w in name.lower() or w in text.lower() for w in words):
            leads.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "status": "INSPECTION_LEAD_NOT_BEHAVIOR_PROOF",
                }
            )
            if len(leads) == 20:
                break
    return {
        "schema_version": 1,
        "repository": str(root),
        "head": head,
        "main_comparisons": comparisons,
        "remote_freshness": "UNVERIFIED_NO_FETCH",
        "inventory_complete": complete,
        "units": units,
        "selected_units": selected,
        "ownership": "SCOPED" if selected or seeds else "UNRESOLVED",
        "relevant_paths": sorted(relevant),
        "call_sites": sorted(edges, key=lambda e: (e["from"], e["to"])),
        "instructions": instructions,
        "existing_implementation_leads": leads,
        "diagnostics": diagnostics,
        "limitations": [
            "Static literal relative imports only; dynamic imports and aliases require inspection",
            "Implementation leads require source and behavior review before "
            "classifying remaining work",
        ],
        "inspected_sources": {
            n: hashlib.sha256(t.encode()).hexdigest() for n, t in sorted(texts.items())
        },
    }


def register(sub):
    def command(args, session):
        print(json.dumps(audit(Path.cwd(), args.request), sort_keys=True))

    parser = sub.add_parser(
        "routing-audit", help="read-only ownership and known-main investigation"
    )
    parser.add_argument("--request", required=True)
    parser.set_defaults(fn=command)
