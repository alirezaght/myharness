"""Bounded descriptor-relative discovery; observations grant no execution authority."""

import hashlib
import os
import re
import stat
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from harness.authoring_contract import digest, exact
from harness.execution_files import PROTECTED, read_file, relative_path
from harness.investigation_policy import AcquisitionError

DEFAULT_BUDGET = {
    "entries": 2000,
    "files": 120,
    "bytes": 262144,
    "hash_bytes": 16777216,
    "results": 40,
    "seconds": 10,
    "rounds": 2,
}
SKIP = PROTECTED | {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}


def source_record(kind, locator, content, metadata=None):
    return {
        "id": kind
        + ":"
        + digest({"locator": locator, "content": content, "metadata": metadata or {}})[:24],
        "kind": kind,
        "locator": locator,
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "metadata": metadata or {},
    }


def repository_operation(method):
    @wraps(method)
    def bounded(self, *args, **kwargs):
        with self.operation():
            return method(self, *args, **kwargs)

    return bounded


class Reader:
    def __init__(self, root, budget=None, *, cancelled=lambda: False, managed_policy=None):
        self.root = Path(root).resolve(strict=True)
        self.managed_policy = managed_policy
        self.budget = dict(DEFAULT_BUDGET, **(budget or {}))
        if set(self.budget) != set(DEFAULT_BUDGET) or any(
            type(v) is not int or v < 1 or v > DEFAULT_BUDGET[k] for k, v in self.budget.items()
        ):
            raise ValueError("bounded repository policy required")
        self.cancelled = cancelled
        self.elapsed = 0.0
        self._operation_start = None
        self._operation_depth = 0
        self.used = {
            "entries": 0,
            "files": 0,
            "bytes": 0,
            "hash_bytes": 0,
            "results": 0,
            "rounds": 0,
        }
        self.sources, self.limitations, self.inventory = [], [], []

    def check(self):
        if self.cancelled():
            raise AcquisitionError("cancelled")
        active = (
            time.monotonic() - self._operation_start if self._operation_start is not None else 0.0
        )
        if self.elapsed + active >= self.budget["seconds"]:
            raise AcquisitionError("timeout")

    @contextmanager
    def operation(self):
        self.check()
        if self._operation_depth == 0:
            self._operation_start = time.monotonic()
        self._operation_depth += 1
        try:
            yield
            self.check()
        finally:
            self._operation_depth -= 1
            if self._operation_depth == 0:
                self.elapsed += time.monotonic() - self._operation_start
                self._operation_start = None

    def charge(self, key, amount):
        self.check()
        if self.used[key] + amount > self.budget[key]:
            raise AcquisitionError("output_limit")
        self.used[key] += amount

    @repository_operation
    def read(self, path, offset=0, limit=8192):
        relative_path(path)
        from harness.investigation_artifacts import reject_source

        reject_source(self.managed_policy, path)
        self.charge("files", 1)
        # Reserve the maximum possible hashing cost before read_file performs work.
        from harness.execution_files import parent_descriptor

        with parent_descriptor(self.root, path) as (directory, name):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("ordinary source required")
        self.charge("hash_bytes", 8388608)
        remaining = self.budget["bytes"] - self.used["bytes"]
        if remaining < 1:
            raise AcquisitionError("output_limit")
        value = read_file(self.root, path, offset=offset, limit=min(limit, remaining))
        self.used["hash_bytes"] -= 8388608 - value["hash_bytes"]
        self.charge("bytes", value["page_bytes"])
        self.charge("results", 1)
        if value["sha256"] is None or value["truncated"]:
            self.limitations.append("Partial repository source: " + path)
        row = source_record("repository", path, value.pop("text"), value)
        self.sources.append(row)
        return row

    @repository_operation
    def walk(self):
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

        def descend(fd, prefix):
            self.check()
            with os.scandir(fd) as entries:
                for entry in entries:
                    self.charge("entries", 1)
                    if entry.name in SKIP or entry.name.startswith(".env"):
                        continue
                    path = prefix + entry.name
                    if self.managed_policy and path in self.managed_policy["excluded_paths"]:
                        continue
                    relative_path(path)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(
                            entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        try:
                            descend(child, path + "/")
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        self.inventory.append(path)
                    else:
                        self.limitations.append("Excluded nonordinary source: " + path)

        try:
            descend(root_fd, "")
        finally:
            os.close(root_fd)

    def observe(self, ticket):
        try:
            with self.operation():
                self.walk()
                from harness.repo_relevance import search_terms

                terms = set(re.findall(r"[a-z][a-z0-9_]{3,30}", search_terms(ticket).lower()))
                terms -= {
                    "description",
                    "summary",
                    "status",
                    "updated",
                    "with",
                    "this",
                    "that",
                    "from",
                }

                def rank(path):
                    base = Path(path).name
                    return (
                        base
                        not in {
                            "AGENTS.md",
                            "CLAUDE.md",
                            "README.md",
                            "pyproject.toml",
                            "package.json",
                        },
                        -sum(t in path.lower() for t in terms),
                        path,
                    )

                # Each searched file is an actual confined read, not a broad analyzer claim.
                for path in sorted(self.inventory, key=rank):
                    if Path(path).suffix.lower() not in {
                        ".py",
                        ".js",
                        ".ts",
                        ".tsx",
                        ".json",
                        ".toml",
                        ".md",
                        ".sql",
                        ".yaml",
                        ".yml",
                    }:
                        continue
                    self.read(path)
        except AcquisitionError as error:
            if error.reason == "cancelled":
                raise
            self.limitations.append(error.reason)
        except (OSError, ValueError):
            self.limitations.append("Repository source unavailable or changed")
        return self.result()

    @repository_operation
    def additional(self, requests):
        self.charge("rounds", 1)
        if not isinstance(requests, list) or not 1 <= len(requests) <= 8:
            raise ValueError("bounded additional reads required")
        for request in requests:
            if request.get("kind") == "read":
                exact(request, {"kind", "path", "offset", "limit"})
                self.read(request["path"], request["offset"], request["limit"])
            elif request.get("kind") == "search":
                exact(request, {"kind", "path", "text"})
                prefix = str(relative_path(request["path"]))
                needle = request["text"]
                if not isinstance(needle, str) or not 1 <= len(needle.encode()) <= 200:
                    raise ValueError("bounded literal search required")
                for path in sorted(self.inventory):
                    if path == prefix or path.startswith(prefix + "/"):
                        self.read(path)
            else:
                raise ValueError("unknown repository read variant")
        return self.result()

    def result(self):
        # Repeated reads retain their original immutable identity.
        rows = {s["id"]: s for s in self.sources}
        return {
            **({"managed_policy": self.managed_policy} if self.managed_policy else {}),
            "sources": list(rows.values()),
            "complete": not self.limitations,
            "limitations": list(dict.fromkeys(self.limitations)),
            "counts": dict(self.used),
            "searched_paths": sorted(self.inventory),
            "atomic_snapshot": False,
        }


def investigate_repository(
    root, ticket, policy=None, *, cancelled=lambda: False, progress=lambda stage: None
):
    progress("inspecting_repository")
    reader = Reader(root, policy, cancelled=cancelled)
    return reader.observe(ticket)


def read_requested(root, requests, remaining_budget):
    reader = (
        remaining_budget if isinstance(remaining_budget, Reader) else Reader(root, remaining_budget)
    )
    return reader.additional(requests)


def recheck_observation(root, observation):
    charged = 0
    for source in observation["sources"]:
        if source["kind"] != "repository":
            continue
        from harness.investigation_artifacts import reject_source

        reject_source(observation.get("managed_policy"), source["locator"])
        previous = source["metadata"]
        charged += 8388608
        if charged > DEFAULT_BUDGET["hash_bytes"]:
            raise AcquisitionError("output_limit")
        value = read_file(
            root, source["locator"], offset=previous["offset"], limit=max(1, previous["page_bytes"])
        )
        charged -= 8388608 - value["hash_bytes"]
        if any(value[key] != previous[key] for key in ("version", "sha256", "hash_status")):
            raise AcquisitionError("source_changed")
        if hashlib.sha256(value["text"].encode()).hexdigest() != source["sha256"]:
            raise AcquisitionError("source_changed")
    return True


def observation_current(root, observation):
    from harness.execution_files import _source_version, parent_descriptor

    try:
        for source in observation["sources"]:
            if source["kind"] != "repository":
                continue
            with parent_descriptor(root, source["locator"]) as (directory, name):
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if _source_version(info) != source["metadata"]["version"]:
                return False
        return True
    except (OSError, ValueError, KeyError):
        return False
