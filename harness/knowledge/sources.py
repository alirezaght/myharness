"""Confined, bounded, non-executing source discovery. Unknown reads never mean deletion."""

import os
import posixpath
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath

from harness.code_resolution import CODE_SUFFIXES, SCRIPT_SUFFIXES  # noqa: F401
from harness.db import git_env
from harness.knowledge.models import (
    EXTRACTOR_VERSION,
    MAX_REFRESH_BYTES,
    MAX_SOURCE_BYTES,
    KnowledgeScope,
    RefreshRequest,
    SourceInventory,
    SourceVersion,
    canonical,
    digest,
    identity,
)

_SUFFIXES = CODE_SUFFIXES | {".md", ".toml", ".json", ".yaml", ".yml", ".sql"}
# Dependency lockfiles, build output and minified bundles are generated: they carry no
# consumer edges, crowd out real sources and would be re-indexed on every install or build.
_LOCKFILES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "composer.lock",
    "pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "cargo.lock",
    "gemfile.lock",
}
_GENERATED_DIRS = {
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".output",
    ".astro",
    "coverage",
    "__generated__",
    "bower_components",
}
_MANIFESTS = {
    "package.json",
    "pyproject.toml",
    "setup.cfg",
    "pnpm-workspace.yaml",
    "jsconfig.json",
}
_PRIVATE = {
    ".git",
    ".harness",
    ".codex",
    ".claude",
    ".aws",
    ".ssh",
    "node_modules",
    ".venv",
    "__pycache__",
    "credentials",
    "transcripts",
    "logs",
}


class ExcludedSource(ValueError):
    pass


class SourceSizeCapped(ValueError):
    def __init__(self, limit: int, observed_bytes: int):
        self.limit = limit
        self.observed_bytes = observed_bytes
        super().__init__("source size capped")


_SECRET = re.compile(
    r"(-----BEGIN .*PRIVATE KEY-----|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
    r'(?i:password|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*["\x27][^"\x27\s]{8,})'
)


def _unprotected(locator: str) -> bool:
    parts = PurePosixPath(locator).parts
    return (
        bool(parts)
        and not PurePosixPath(locator).is_absolute()
        and all(
            part not in _PRIVATE
            and part not in (".", "..")
            and not part.lower().startswith(".env")
            and not any(
                word in part.lower() for word in ("credential", "transcript", ".pem", ".key")
            )
            for part in parts
        )
    )


def generated(locator: str) -> bool:
    path = PurePosixPath(locator)
    name = path.name.lower()
    return (
        name in _LOCKFILES
        or name.endswith((".min.js", ".min.mjs", ".bundle.js"))
        or any(part in _GENERATED_DIRS for part in path.parts[:-1])
    )


def eligible(locator: str) -> bool:
    return (
        posixpath.splitext(posixpath.basename(locator))[1].lower() in _SUFFIXES
        and not generated(locator)
        and _unprotected(locator)
    )


def stat_identity(root: Path, locator: str):
    """Cheap change detector for a source already read through the confined reader."""
    try:
        value = os.lstat(os.path.join(root, locator))
    except OSError:
        return None
    return (value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def priority(locator: str) -> int:
    """Code first, then unit manifests, then documentation, then data and configuration."""
    path = PurePosixPath(locator)
    name, suffix = path.name.lower(), path.suffix.lower()
    if suffix in CODE_SUFFIXES:
        return 0
    if name in _MANIFESTS or (name.startswith("tsconfig") and suffix == ".json"):
        return 1
    if suffix == ".md":
        return 2
    return 3


def source_kind(locator: str) -> str:
    return "python" if locator.endswith(".py") else "text"


def ordinary_text_path(locator: str) -> bool:
    """Union of ordinary execution-file and Knowledge protected-path restrictions."""
    from harness.execution_files import relative_path

    relative_path(locator)
    return _unprotected(locator)


def read_source(root: Path, locator: str, limit: int) -> bytes:
    if not eligible(locator):
        raise ValueError("excluded source")
    return _read_text(root, locator, limit)


def read_ordinary_text(root: Path, locator: str, limit: int) -> bytes:
    """Read authorized ordinary text independently of Knowledge indexing eligibility."""
    if not ordinary_text_path(locator):
        raise ValueError("excluded source")
    return _read_text(root, locator, limit)


def _read_text(root: Path, locator: str, limit: int) -> bytes:
    """Walk directory descriptors with O_NOFOLLOW; screen the entire stable file."""
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(root, flags | os.O_DIRECTORY)
    try:
        parts = PurePosixPath(locator).parts
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(child)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("source is not an unaliased regular file")
            if before.st_size > limit:
                raise SourceSizeCapped(limit, before.st_size)
            raw = b""
            while len(raw) <= limit:
                block = os.read(child, min(65536, limit + 1 - len(raw)))
                if not block:
                    break
                raw += block
            after = os.fstat(child)
            if len(raw) > limit:
                raise SourceSizeCapped(limit, len(raw))
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("unstable source")
            text = raw.decode("utf-8")
            if "\x00" in text or _SECRET.search(text):
                raise ExcludedSource("sensitive or binary source excluded")
            return raw
        finally:
            os.close(child)
    finally:
        os.close(fd)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, env=git_env(), capture_output=True, check=False)


def inventory_names(root: Path, request: RefreshRequest) -> tuple[list[str], dict]:
    if request.commit:
        from harness.knowledge.commit_sources import commit_inventory

        return commit_inventory(root, request)
    result = _git(root, "ls-files", "-z", "--cached")
    if result.returncode:
        raise ValueError("tracked source enumeration unavailable")
    names = set(result.stdout.decode("utf-8").split("\x00")) - {""}
    if request.included_paths and not request.included_paths == tuple(
        sorted(set(request.included_paths))
    ):
        raise ValueError("explicit inclusions must be sorted and unique")
    private = set(request.included_paths) - names
    names.update(request.included_paths)
    revision = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "symbolic-ref", "--quiet", "HEAD")
    return sorted(name for name in names if eligible(name)), {
        "kind": "working-tree-observation",
        "task_private_paths": sorted(private),
        "head": revision.stdout.decode().strip() if not revision.returncode else None,
        "branch": branch.stdout.decode().strip() if not branch.returncode else None,
        "inventory_sha256": digest(canonical(sorted(names)).encode()),
    }


READ_WORKERS = 8
_READ_CHUNK = 512


def _read_all(root, names, request, observation):
    """Read sources in order, a bounded chunk at a time on a few threads (I/O bound)."""
    from concurrent.futures import ThreadPoolExecutor

    def read(name):
        before = after = None
        try:
            if request.commit:
                from harness.knowledge.commit_sources import read_blob

                raw = read_blob(root, observation["git_entries"][name], request.max_file_bytes)
            else:
                before = stat_identity(root, name)
                raw = read_source(root, name, request.max_file_bytes)
                after = stat_identity(root, name)
        except (OSError, UnicodeError, ValueError) as error:
            raw = error
        return name, before, raw, after

    if len(names) < 64:
        yield from map(read, names)
        return
    with ThreadPoolExecutor(READ_WORKERS) as pool:
        for start in range(0, len(names), _READ_CHUNK):
            yield from pool.map(read, names[start : start + _READ_CHUNK])


def enumerate_sources(
    scope: KnowledgeScope, request: RefreshRequest, cached: frozenset[str] = frozenset()
) -> SourceInventory:
    """Hash every eligible source; retain bytes only for versions whose extraction is not cached.

    There is no fixed file-count ceiling. Sources are visited code first, so whatever a bounded
    refresh defers is the least useful material, and the retained-byte budget applies only to
    sources that still need extraction: an unchanged large repository streams through.
    """
    if request.included_paths and not scope.task_id:
        raise ValueError("untracked inclusions require task scope")
    if not (
        (request.max_files is None or request.max_files >= 1)
        and 1 <= request.max_file_bytes <= MAX_SOURCE_BYTES
    ):
        raise ValueError("source limits exceed supported bounds")
    root = scope.repository.resolve()
    names, observation = inventory_names(root, request)
    ordered = sorted(names, key=lambda name: (priority(name), name))
    limit = len(ordered) if request.max_files is None else request.max_files
    coverage, sources, retained_bytes = [], [], 0
    if len(ordered) > limit:
        coverage.append(
            {
                "status": "capped",
                "kind": "inventory",
                "reason": "file-count-capped",
                "limit": limit,
            }
        )
    for name in ordered[limit:]:
        coverage.append({"locator": name, "status": "capped", "reason": "file-count-capped"})
    for name, before, raw, after in _read_all(root, ordered[:limit], request, observation):
        remaining_bytes = MAX_REFRESH_BYTES - retained_bytes
        try:
            if isinstance(raw, Exception):
                raise raw
        except ExcludedSource:
            coverage.append({"locator": name, "status": "excluded-sensitive"})
            continue
        except SourceSizeCapped as error:
            coverage.append(
                {
                    "locator": name,
                    "status": "capped",
                    "reason": "source-size-capped",
                    "limit": request.max_file_bytes,
                    "read_limit": request.max_file_bytes,
                    "observed_bytes": error.observed_bytes,
                }
            )
            continue
        except (OSError, UnicodeError, ValueError):
            coverage.append(
                {
                    "locator": name,
                    "status": "unreadable",
                    "reason": "source-read-failed",
                    "limit": request.max_file_bytes,
                }
            )
            continue
        source_id = identity(scope.project_id, name)
        sha = digest(raw)
        visibility = (
            "task:" + scope.task_id
            if name in observation.get("task_private_paths", [])
            else "project"
        )
        kind = source_kind(name)
        version_parts = (source_id, sha, EXTRACTOR_VERSION, kind)
        version = (
            identity(*version_parts, visibility)
            if visibility != "project"
            else identity(*version_parts)
        )
        retain = version not in cached or name.startswith("docs/knowledge/imports/")
        if retain and len(raw) > remaining_bytes:
            # Deferred, not lost: once this refresh's sources are cached they stop
            # consuming the budget and a later incremental refresh retains these.
            coverage.append(
                {
                    "locator": name,
                    "status": "capped",
                    "reason": "refresh-bytes-capped",
                    "limit": MAX_REFRESH_BYTES,
                    "read_limit": remaining_bytes,
                    "observed_bytes": len(raw),
                }
            )
            continue
        if retain:
            retained_bytes += len(raw)
        sources.append(
            SourceVersion(
                source_id,
                version,
                name,
                kind,
                sha,
                raw if retain else b"",
                (
                    dict(
                        mode="commit",
                        commit=observation["head"],
                        file_mode=observation["git_entries"][name]["mode"],
                        blob=observation["git_entries"][name]["blob"],
                        type=observation["git_entries"][name]["type"],
                        visibility=visibility,
                    )
                    if request.commit
                    else dict(observation, visibility=visibility)
                ),
                visibility=visibility,
                raw_retained=retain,
                # Unchanged lstat across the read lets revalidation skip a second full read.
                stat=before if before is not None and before == after else None,
            )
        )
    coverage = [
        dict(c, visibility="task:" + scope.task_id)
        if c.get("locator") in observation.get("task_private_paths", [])
        else dict(c, visibility="project")
        for c in coverage
    ]
    return SourceInventory(
        tuple(sources),
        all(c["status"] == "excluded-sensitive" for c in coverage),
        tuple(coverage),
        observation,
        frozenset(names),
        True,
    )


def read_artifact(root: Path, reference: dict, *, maximum=16 * MAX_SOURCE_BYTES) -> bytes:
    """Read a hash-bound ordinary artifact directly under its server-selected issuing root."""
    root = Path(root)
    if any(parent.is_symlink() for parent in (root, *root.parents)):
        raise ValueError("artifact issuing root alias refused")
    root = root.resolve(strict=True)
    path = Path(reference.get("stored_path", reference.get("path", "")))
    if not path.is_absolute() or path.parent != root or path.name in {"", ".", ".."}:
        raise ValueError("artifact outside issuing root")
    expected = reference.get("sha256", "").removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("artifact hash binding required")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
                raise ValueError("bounded ordinary artifact required")
            raw = b""
            while len(raw) <= maximum:
                block = os.read(fd, min(65536, maximum + 1 - len(raw)))
                if not block:
                    break
                raw += block
            after = os.fstat(fd)
            if (
                len(raw) > maximum
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or digest(raw) != expected
            ):
                raise ValueError("artifact content binding differs")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory)
