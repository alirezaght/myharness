"""Dependency manifests: which exist, and what pyproject.toml / package.json declare."""

import json
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from harness.investigation import FactCategory
from harness.repo_scan import Findings, read_text

MANIFESTS = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Gemfile",
        "composer.json",
    }
)
DEPS = FactCategory.DEPENDENCIES


def _names(value: Any) -> list[str] | None:
    if isinstance(value, dict):
        value = list(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _table(data: Any, *keys: str) -> Any:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _listing(names: list[str]) -> str:
    return ", ".join(names) if names else "(none)"


def _pyproject(out: Findings, where: str, data: dict[str, Any]) -> None:
    deps = _names(_table(data, "project", "dependencies"))
    if deps is not None:
        out.fact(DEPS, f"runtime dependencies: {_listing(deps)}", f"{where} [project].dependencies")
    for table in ("dependency-groups", "project.optional-dependencies"):
        groups = _table(data, *table.split("."))
        for group, value in groups.items() if isinstance(groups, dict) else []:
            names = _names(value)
            if names is not None:
                text = f"{table} `{group}`: {_listing(names)}"
                out.fact(DEPS, text, f"{where} [{table}]")
    python = _table(data, "project", "requires-python")
    if isinstance(python, str):
        out.fact(FactCategory.CONSTRAINTS, f"requires-python {python}", f"{where} [project]")
    scripts = _table(data, "project", "scripts")
    for name, target in scripts.items() if isinstance(scripts, dict) else []:
        text = f"console script `{name}` → {target}"
        out.fact(FactCategory.INTERFACES, text, f"{where} [project.scripts]")
    ruff = _table(data, "tool", "ruff")
    if isinstance(_table(ruff, "line-length"), int):
        text = f"ruff line-length {ruff['line-length']}"
        out.fact(FactCategory.CONVENTIONS, text, f"{where} [tool.ruff]")
    select = _names(_table(ruff, "lint", "select")) or _names(_table(ruff, "select"))
    if select:
        text = f"ruff lint rules: {_listing(select)}"
        out.fact(FactCategory.CONVENTIONS, text, f"{where} [tool.ruff]")
    testpaths = _names(_table(data, "tool", "pytest", "ini_options", "testpaths"))
    if testpaths:
        text = f"pytest testpaths: {_listing(testpaths)}"
        out.fact(FactCategory.TESTS, text, f"{where} [tool.pytest.ini_options]")


def _package_json(out: Findings, where: str, data: dict[str, Any]) -> None:
    for key in ("dependencies", "devDependencies"):
        names = _names(data.get(key))
        if names is not None:
            out.fact(DEPS, f"{key}: {_listing(sorted(names))}", f"{where} {key}")
    scripts = _names(data.get("scripts"))
    if scripts:
        out.fact(FactCategory.INTERFACES, f"npm scripts: {_listing(scripts)}", f"{where} scripts")


def _parse(root: Path, rel: PurePosixPath) -> dict[str, Any] | None:
    text = read_text(root, rel)
    if text is None:
        return None
    try:
        data = tomllib.loads(text) if rel.suffix == ".toml" else json.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def dependency_facts(root: Path, files: list[PurePosixPath]) -> Findings:
    out = Findings()
    manifests = [rel for rel in files if rel.name in MANIFESTS]
    if not manifests:
        out.ask("dependencies", "No dependency manifest found; how are dependencies managed?")
        return out
    for rel in manifests:
        where = rel.as_posix()
        out.fact(DEPS, f"dependency manifest `{where}`", where)
        if rel.name not in ("pyproject.toml", "package.json"):
            continue
        data = _parse(root, rel)
        if data is None:
            out.ask("dependencies", f"{where} could not be parsed; which dependencies matter?")
        elif rel.name == "pyproject.toml":
            _pyproject(out, where, data)
        else:
            _package_json(out, where, data)
    packages = sorted({rel.parent.as_posix() for rel in manifests})
    if len(packages) > 1:
        listed = ", ".join(f"`{p}/`" if p != "." else "the root" for p in packages)
        out.ask("scope", f"Several packages found ({listed}); which one does the request target?")
    return out
