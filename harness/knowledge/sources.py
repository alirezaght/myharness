"""Confined, bounded, non-executing source discovery. Unknown reads never mean deletion."""

import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath

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

_SUFFIXES = {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".js", ".ts", ".tsx", ".sql"}
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


def eligible(locator: str) -> bool:
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
        and Path(locator).suffix.lower() in _SUFFIXES
    )


def read_source(root: Path, locator: str, limit: int) -> bytes:
    """Walk directory descriptors with O_NOFOLLOW; reject aliases and special files."""
    if not eligible(locator):
        raise ValueError("excluded source")
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


def enumerate_sources(scope: KnowledgeScope, request: RefreshRequest) -> SourceInventory:
    if request.included_paths and not scope.task_id:
        raise ValueError("untracked inclusions require task scope")
    if not (1 <= request.max_files <= 10000 and 1 <= request.max_file_bytes <= MAX_SOURCE_BYTES):
        raise ValueError("source limits exceed supported bounds")
    root = scope.repository.resolve()
    names, observation = inventory_names(root, request)
    coverage, sources, retained_bytes = [], [], 0
    if len(names) > request.max_files:
        coverage.append(
            {
                "status": "capped",
                "kind": "inventory",
                "reason": "file-count-capped",
                "limit": request.max_files,
            }
        )
    for name in names[request.max_files :]:
        coverage.append({"locator": name, "status": "capped", "reason": "file-count-capped"})
    for name in names[: request.max_files]:
        remaining_bytes = MAX_REFRESH_BYTES - retained_bytes
        read_limit = min(request.max_file_bytes, remaining_bytes)
        try:
            if request.commit:
                from harness.knowledge.commit_sources import read_blob

                raw = read_blob(root, observation["git_entries"][name], read_limit)
            else:
                raw = read_source(root, name, read_limit)
        except ExcludedSource:
            coverage.append({"locator": name, "status": "excluded-sensitive"})
            continue
        except SourceSizeCapped as error:
            reason = (
                "refresh-bytes-capped"
                if remaining_bytes < request.max_file_bytes
                else "source-size-capped"
            )
            coverage.append(
                {
                    "locator": name,
                    "status": "capped",
                    "reason": reason,
                    "limit": (
                        MAX_REFRESH_BYTES
                        if reason == "refresh-bytes-capped"
                        else request.max_file_bytes
                    ),
                    "read_limit": read_limit,
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
                    "limit": read_limit,
                }
            )
            continue
        retained_bytes += len(raw)
        source_id = identity(scope.project_id, name)
        sha = digest(raw)
        visibility = (
            "task:" + scope.task_id
            if name in observation.get("task_private_paths", [])
            else "project"
        )
        kind = "python" if name.endswith(".py") else "text"
        version_parts = (source_id, sha, EXTRACTOR_VERSION, kind)
        version = (
            identity(*version_parts, visibility)
            if visibility != "project"
            else identity(*version_parts)
        )
        sources.append(
            SourceVersion(
                source_id,
                version,
                name,
                kind,
                sha,
                raw,
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
