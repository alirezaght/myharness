"""Path-based probes: languages, components, interfaces, schemas, config, tests, conventions."""

import re
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from harness.investigation import FactCategory
from harness.repo_manifests import MANIFESTS
from harness.repo_scan import Findings, is_secret_path, read_text

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".sql": "sql",
    ".sh": "shell",
    ".swift": "swift",
    ".m": "objective-c",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".dart": "dart",
    ".fs": "fsharp",
    ".vue": "vue",
    ".svelte": "svelte",
}
SCHEMA_SUFFIXES = frozenset({".sql", ".prisma", ".proto", ".graphql"})
API_SPEC_PATTERNS = ("openapi*.yaml", "openapi*.yml", "openapi*.json", "swagger*.yaml")
API_SPEC_PATTERNS += ("swagger*.yml", "swagger*.json")
CONFIG_SUFFIXES = frozenset({".toml", ".yaml", ".yml", ".ini", ".cfg"})
CONFIG_NAMES = ("Dockerfile", "docker-compose*.yml", "docker-compose*.yaml", ".editorconfig")
CONFIG_NAMES += (".env.example", ".env.sample")
TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec"})
TEST_FILE_PATTERNS = ("test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*")
CONVENTION_FILES = (".editorconfig", "ruff.toml", ".ruff.toml", ".eslintrc*", ".prettierrc*")
CONVENTION_FILES += (".pre-commit-config.yaml", "CONTRIBUTING.md")
AGENT_FILES = frozenset({"AGENTS.md", "CLAUDE.md"})
INTERFACE_NAME_MARKERS = frozenset(
    {"api", "route", "controller", "handler", "cli", "interface", "contract", "schema"}
    | {"endpoint", "service"}
)
MAX_LISTED = 25


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)


def _listed(out: Findings, category: FactCategory, lines: list[tuple[str, str]]) -> None:
    for text, evidence in lines[:MAX_LISTED]:
        out.fact(category, text, evidence)
    if len(lines) > MAX_LISTED:
        out.fact(category, f"{len(lines) - MAX_LISTED} more not listed", "listing cap")


def is_source(rel: PurePosixPath) -> bool:
    return rel.suffix in LANGUAGE_BY_SUFFIX


def is_test(rel: PurePosixPath) -> bool:
    return bool(TEST_DIR_NAMES & set(rel.parts[:-1])) or _matches(rel.name, TEST_FILE_PATTERNS)


CI_FILES = frozenset(
    {".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".travis.yml"}
    | {".drone.yml", ".woodpecker.yml", "buildspec.yml", "cloudbuild.yaml"}
)
CI_DIRS = frozenset({".github/workflows", ".circleci", ".buildkite", ".woodpecker", ".gitlab/ci"})


def is_workflow(rel: PurePosixPath) -> bool:
    """A CI definition of any provider."""
    if rel.as_posix() in CI_FILES:
        return True
    return rel.suffix in {".yml", ".yaml"} and any(
        rel.parent.as_posix() == d or rel.parent.as_posix().startswith(d + "/") for d in CI_DIRS
    )


def language_summary(files: list[PurePosixPath]) -> Findings:
    out = Findings()
    counts = Counter(LANGUAGE_BY_SUFFIX[rel.suffix] for rel in files if is_source(rel))
    for language, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        out.fact(FactCategory.REPOSITORY, f"{count} {language} files", "file extensions")
    total = sum(counts.values())
    if counts:
        language, count = max(counts.items(), key=lambda item: (item[1], item[0]))
        if count * 2 > total:
            out.assume(f"primary language is {language}", "majority of source files")
    return out


def component_summary(files: list[PurePosixPath]) -> Findings:
    out = Findings()
    present = {rel.as_posix() for rel in files}
    counts = Counter(
        rel.parts[0]
        for rel in files
        if len(rel.parts) > 1
        and is_source(rel)
        and not is_test(rel)
        and not rel.parts[0].startswith(".")
    )
    for top, count in sorted(counts.items())[:MAX_LISTED]:
        marker = f"{top}/__init__.py"
        if marker in present:
            text = f"`{top}/` is a Python package ({count} source files)"
            out.fact(FactCategory.COMPONENTS, text, marker)
        else:
            out.assume(f"`{top}/` is a component ({count} source files)", "directory layout")
    return out


def interface_facts(files: list[PurePosixPath]) -> Findings:
    out = Findings()
    hits = []
    for rel in files:
        if not is_source(rel) or is_test(rel):
            continue
        parts = [*rel.parts[:-1], rel.stem]
        tokens = {token for part in parts for token in re.split(r"[_\-.]", part.lower())}
        found = sorted({t.removesuffix("s") for t in tokens} & INTERFACE_NAME_MARKERS)
        if found:
            hits.append(
                (f"`{rel.as_posix()}` looks like an interface", f"name: {', '.join(found)}")
            )
    _listed(out, FactCategory.INTERFACES, hits)
    return out


def schema_facts(files: list[PurePosixPath]) -> Findings:
    out = Findings()
    schemas = [
        (f"schema file `{rel.as_posix()}`", rel.as_posix())
        for rel in files
        if rel.suffix in SCHEMA_SUFFIXES or _matches(rel.name, API_SPEC_PATTERNS)
    ]
    _listed(out, FactCategory.SCHEMAS, schemas)
    migrations = Counter(
        PurePosixPath(*rel.parts[: rel.parts.index("migrations") + 1]).as_posix()
        for rel in files
        if "migrations" in rel.parts[:-1]
    )
    for directory, count in sorted(migrations.items()):
        out.fact(
            FactCategory.SCHEMAS, f"migrations directory `{directory}/` ({count} files)", directory
        )
    return out


def config_facts(files: list[PurePosixPath]) -> Findings:
    out = Findings()
    configs = []
    for rel in files:
        where = rel.as_posix()
        if is_secret_path(rel):
            text = f"secret-bearing file `{where}` present (contents not read)"
            out.fact(FactCategory.CONFIG, text, "path only")
            text = f"Which values from `{where}` matter for this change? (not read by the harness)"
            out.ask("config", text)
        elif rel.name in MANIFESTS or is_workflow(rel) or _matches(rel.name, API_SPEC_PATTERNS):
            continue
        elif rel.suffix in CONFIG_SUFFIXES or _matches(rel.name, CONFIG_NAMES):
            configs.append((f"config file `{where}`", where))
    _listed(out, FactCategory.CONFIG, configs)
    return out


def test_facts(files: list[PurePosixPath]) -> Findings:
    out = Findings()
    tests = [rel for rel in files if is_test(rel)]
    if not tests:
        out.ask("tests", "No tests found; which framework and layout should new tests use?")
        return out
    out.fact(FactCategory.TESTS, f"{len(tests)} test files", "test directory and file naming")
    dirs = Counter(_test_dir(rel) for rel in tests)
    listed = [(f"test location `{d}/` ({n} files)", d) for d, n in sorted(dirs.items())]
    _listed(out, FactCategory.TESTS, listed)
    directory = max(dirs.items(), key=lambda item: (item[1], item[0]))[0]
    out.assume(f"new tests belong under `{directory}/`", "where most existing tests live")
    return out


def _test_dir(rel: PurePosixPath) -> str:
    for index, part in enumerate(rel.parts[:-1]):
        if part in TEST_DIR_NAMES:
            return PurePosixPath(*rel.parts[: index + 1]).as_posix()
    return rel.parent.as_posix()


def convention_facts(root: Path, files: list[PurePosixPath]) -> Findings:
    out = Findings()
    for rel in files:
        where = rel.as_posix()
        if is_workflow(rel):
            out.fact(FactCategory.CONSTRAINTS, f"CI workflow `{where}`", where)
        elif rel.name in AGENT_FILES:
            out.fact(FactCategory.CONSTRAINTS, f"agent instructions `{where}`", where)
        elif _matches(rel.name, CONVENTION_FILES):
            out.fact(FactCategory.CONVENTIONS, f"convention file `{where}`", where)
        elif where == ".python-version":
            pinned = (read_text(root, rel) or "").strip().splitlines()
            if pinned:
                out.fact(FactCategory.CONVENTIONS, f"Python {pinned[0]} pinned", where)
    return out
