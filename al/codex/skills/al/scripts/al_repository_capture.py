"""Typed, bounded whole-discovery capture; never follows opaque boundaries."""

import hashlib
import os
import stat
import time
from pathlib import Path


RACY_NS = 2_000_000_000


class FrozenObservation(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("immutable capture observation")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __deepcopy__(self, memo):
        return self


def immutable_observation(value):
    if isinstance(value, dict):
        return FrozenObservation({k: immutable_observation(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(immutable_observation(v) for v in value)
    return value


class CaptureDrift(ValueError):
    def __init__(self, reason, paths=()):
        self.reason = reason
        self.paths = tuple(sorted(set(paths)))
        compatibility = {
            "source-content-or-identity": "captured coverage input changed",
            "source-content": "captured coverage content changed",
        }.get(reason)
        detail = reason + (": " + compatibility if compatibility else "")
        super().__init__("coverage capture drift: " + detail + ": " + ",".join(self.paths))


class CaptureUnavailable(ValueError):
    def __init__(self, reason, paths=()):
        self.reason = reason
        self.paths = tuple(sorted(set(paths)))
        super().__init__("coverage capture unavailable: " + reason + ": " + ",".join(self.paths))


def opaque_owner(opaque_paths, path):
    if "." in opaque_paths:
        return "."
    parts = path.split("/")
    return next(
        (
            "/".join(parts[:i])
            for i in range(1, len(parts) + 1)
            if "/".join(parts[:i]) in opaque_paths
        ),
        None,
    )


def literal_opaque_exclusions(opaque_paths):
    def escaped(path):
        return "".join("\\" + char if char in "\\*?[]" else char for char in path)

    return ["--exclude=/" + escaped(path) + "/" for path in sorted(opaque_paths) if path != "."]


class CaptureMixin:
    def initialize_capture(self):
        self.path_observations = {}
        self.match_observations = {}
        self.source_purposes = {}
        self.listing_observation = None
        self.match_work = 0
        # Files unchanged for RACY_NS before capture started can be revalidated by
        # their full stat identity alone (Git's racy-clean rule); newer ones are re-read.
        self.capture_started_ns = time.time_ns()

    def begin_observation_epoch(self):
        """Start a fresh window for shared ancestor observations (see path_observation)."""
        self._ancestors = {}

    def _ancestor(self, name):
        """lstat + nested-repository probe of one ancestor directory, once per epoch.

        Within one pass over many task paths their shared ancestors are observed once;
        every pass that must detect drift (listing, revalidation) starts a new epoch.
        The leaf of each observed path is always observed afresh."""
        from al_repository_facts import file_identity

        cache = self.__dict__.setdefault("_ancestors", {})
        hit = cache.get(name)
        if hit is not None:
            return hit
        value = os.lstat(os.path.join(str(self.root), name))
        kind = (
            "symlink"
            if stat.S_ISLNK(value.st_mode)
            else (
                "supplied-opaque"
                if name in self.opaque
                else "nested-repository"
                if stat.S_ISDIR(value.st_mode) and os.path.lexists(os.path.join(str(self.root), name, ".git"))
                else None
            )
        )
        row = (kind, list(file_identity(value)), stat.S_ISDIR(value.st_mode))
        cache[name] = row
        return row

    def path_observation(self, path):
        from al_repository_facts import file_identity, safe

        safe(path)
        parts = [] if path == "." else path.split("/")
        root = str(self.root)
        for index in range(1, len(parts) + 1):
            name = "/".join(parts[:index])
            leaf = index == len(parts)
            try:
                if leaf:
                    value = os.lstat(os.path.join(root, name))
                    kind = (
                        "symlink"
                        if stat.S_ISLNK(value.st_mode)
                        else (
                            "supplied-opaque"
                            if name in self.opaque
                            else "nested-repository"
                            if stat.S_ISDIR(value.st_mode) and os.path.lexists(os.path.join(root, name, ".git"))
                            else None
                        )
                    )
                    identity, is_dir = list(file_identity(value)), stat.S_ISDIR(value.st_mode)
                    leaf_value = value
                else:
                    # Explicit: callers also apply this to plain observation contexts.
                    kind, identity, is_dir = CaptureMixin._ancestor(self, name)
            except FileNotFoundError:
                return {"path": path, "state": "absent", "frontier": name}
            if kind:
                return {
                    "path": path,
                    "state": "opaque",
                    "frontier": name,
                    "boundary_kind": kind,
                    "identity": list(identity),
                }
            if not leaf and not is_dir:
                return {
                    "path": path,
                    "state": "absent",
                    "frontier": name,
                    "identity": list(identity),
                }
        value = leaf_value if parts else os.lstat(root)
        return {
            "path": path,
            "state": "directory"
            if stat.S_ISDIR(value.st_mode)
            else "regular"
            if stat.S_ISREG(value.st_mode)
            else "opaque",
            "identity": list(file_identity(value)),
        }

    def observe_path(self, path):
        value = self.path_observation(path)
        from al_repository_facts import LIMITS

        if path not in self.path_observations and len(self.path_observations) >= LIMITS["files"]:
            raise CaptureUnavailable("path-observation-limit", [path])
        if value["state"] == "absent":
            self.absent.add(path)
        previous = self.path_observations.setdefault(path, value)
        if previous != value:
            raise CaptureDrift("path-identity", [path])
        return value

    def capture_bytes(self, path, *, required=False, rule=False):
        from al_repository_facts import gap

        observed = self.observe_path(path)
        if rule:
            self.rules.add(path)
        if observed["state"] == "absent":
            self.absent.add(path)
            if required:
                self.gaps.append(gap("missing-input", path, "required repository input is absent"))
            return b""
        if observed["state"] != "regular":
            raise CaptureUnavailable("opaque-or-nonregular-input", [path])
        if path not in self.files.records:
            data, identity = self.read_observed(path)
            self.files.records[path] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "identity": identity,
                "size": len(data),
            }
            self.modes[path] = identity[2] & 0o7777
            self.retain(("current", path), data)
        return self.files[path]

    @staticmethod
    def matched(pattern, members):
        from al_ci_rules import pattern_matches

        return tuple(
            sorted(
                p.rstrip("/") for p in members if pattern_matches(pattern, p.rstrip("/")) is True
            )
        )

    def observe_matches(self, pattern, *, purpose):
        if not isinstance(pattern, str) or pattern.startswith("/") or ".." in Path(pattern).parts:
            raise CaptureUnavailable("unsafe-membership-pattern")
        # Bind the directories consulted by pattern expansion, including an
        # absent frontier. A changed-and-restored match set is still drift.
        literal = []
        for part in Path(pattern).parts:
            if any(c in part for c in "*?["):
                break
            literal.append(part)
        frontier = Path(*literal) if literal else Path(".")
        if literal and not any(c in pattern for c in "*?["):
            frontier = frontier.parent
        current = frontier
        while True:
            self.observe_path(current.as_posix())
            if current == Path("."):
                break
            current = current.parent
        key = (pattern, purpose)
        if key in self.match_observations:
            return self.match_observations[key]
        from al_repository_facts import LIMITS

        self.match_work += len(self.members)
        if (
            len(self.match_observations) >= LIMITS["files"]
            or self.match_work > LIMITS["files"] * 128
        ):
            raise CaptureUnavailable("match-observation-limit")
        values = self.matched(pattern, self.members)
        if key in self.match_observations and self.match_observations[key] != values:
            raise CaptureDrift("match-membership", values)
        self.match_observations[key] = values
        for path in values:
            self.observe_path(path)
        return values

    def adopt_source(self, ref, *, purpose):
        path = ref["path"]
        if path not in self.members and path not in self.path_observations:
            raise CaptureUnavailable("unobserved-delegated-source", [path])
        data = self.capture_bytes(path, required=True, rule=True)
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise CaptureDrift("delegated-source-conflict", [path])
        self.source_purposes.setdefault(path, set()).add(purpose)

    def revalidate(self, listing=None):
        self.begin_observation_epoch()
        fresh = listing if listing is not None else self.observe_listing()
        if fresh != self.listing_observation:
            raise CaptureDrift("repository-membership")
        for path, old in self.path_observations.items():
            if self.path_observation(path) != old:
                raise CaptureDrift("path-identity", [path])
        self.__dict__.pop("_safe_ancestors", None)
        settled = self.__dict__.get("capture_started_ns", 0) - RACY_NS
        for path, record in self.files.records.items():
            identity = tuple(record["identity"])
            if max(identity[4], identity[5]) < settled:
                from al_repository_facts import file_identity

                try:
                    self.safe_file(path)
                    current = os.lstat(os.path.join(str(self.root), path))
                except (FileNotFoundError, NotADirectoryError) as exc:
                    raise CaptureDrift("source-existence", [path]) from exc
                except OSError as exc:
                    raise CaptureUnavailable("source-permission-or-io", [path]) from exc
                if not stat.S_ISREG(current.st_mode) or file_identity(current) != identity:
                    raise CaptureDrift("source-content-or-identity", [path])
                continue
            try:
                data, identity = self.read_observed(path)
            except (FileNotFoundError, NotADirectoryError) as exc:
                raise CaptureDrift("source-existence", [path]) from exc
            except OSError as exc:
                raise CaptureUnavailable("source-permission-or-io", [path]) from exc
            if (
                identity != record["identity"]
                or hashlib.sha256(data).hexdigest() != record["sha256"]
            ):
                raise CaptureDrift("source-content-or-identity", [path])
        for path in self.absent:
            if self.path_observation(path)["state"] != "absent":
                raise CaptureDrift("source-absence", [path])
        for (pattern, _), old in self.match_observations.items():
            if self.matched(pattern, fresh["members"]) != old:
                raise CaptureDrift("match-membership", old)

    def capture_metadata(self, attempts=1, discarded=()):
        from al_repository_facts import digest
        from al_runtime_resources import implementation_digest

        observations = {
            "listing": self.listing_observation,
            "paths": self.path_observations,
            "matches": [
                {"pattern": p, "purpose": purpose, "matches": list(values)}
                for (p, purpose), values in sorted(self.match_observations.items())
            ],
            "sources": [
                {"path": p, "sha256": v["sha256"], "identity": list(v["identity"])}
                for p, v in sorted(self.files.records.items())
            ],
        }
        return {
            "schema_version": 2,
            "attempts": attempts,
            "stable": True,
            "discarded_attempts": list(discarded),
            "observations_digest": digest(observations),
            "implementation_binding": implementation_digest(),
            "source_count": len(self.files.records),
            "match_count": len(self.match_observations),
            "absence_count": len(self.absent),
        }


def capture_discovery(factory, make_attempt):
    discarded = []
    for number in (1, 2):
        try:
            scan = factory()
            result = make_attempt(scan)
            scan.revalidate()
            metadata = scan.capture_metadata(number, discarded)
            declaration = result.get("service_coverage", {})
            if "semantic_requirements" in declaration:
                from al_repository_facts import digest

                declaration["capture"] = metadata
                declaration["provenance"]["capture_sha256"] = digest(metadata)
                declaration["provenance"]["digest"] = digest(
                    {k: v for k, v in declaration["provenance"].items() if k != "digest"}
                )
            else:
                result["capture"] = metadata
            if "repository_facts" in result:
                result["repository_facts"]["capture"] = metadata
            return result
        except CaptureDrift as exc:
            discarded.append({"reason": exc.reason, "paths": list(exc.paths)})
            if number == 2:
                raise CaptureDrift("repeated-drift", exc.paths) from exc
    raise AssertionError("bounded capture exhausted")
