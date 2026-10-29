"""Private bounded transport for two manifest-producing guarded AL operations."""

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from harness.execution_contract import canonical
from harness.execution_logs import redact

REPORT_LIMIT = 32 * 1024 * 1024
STDERR_LIMIT = 1024 * 1024
ERROR = "guarded AL report publication refused: result or operation provenance differs"


def report_request(arguments):
    if arguments[:2] not in (["gates", "discover"], ["gates", "plan"]):
        return False
    flag = "--paths-file" if arguments[1] == "discover" else "--file"
    if (
        len(arguments) not in ({4, 6} if arguments[1] == "discover" else {4})
        or arguments[2] != flag
        or not arguments[3].startswith("artifact:")
    ):
        raise ValueError("gate reports require their exact immutable input and supported flags")
    if len(arguments) == 6 and (arguments[4] != "--base" or not arguments[5]):
        raise ValueError("invalid gate discovery base")
    return True


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(ERROR)
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError(ERROR)),
        )
    except (ValueError, RecursionError, TypeError):
        raise ValueError(ERROR) from None


def _read(path, maximum=REPORT_LIMIT):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(ERROR)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise ValueError(ERROR)
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (
        len(raw) > maximum
        or any(
            getattr(before, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")
        )
        or path.stat().st_ino != before.st_ino
    ):
        raise ValueError(ERROR)
    return raw


@contextmanager
def _publication_lock(path):
    with Path(path).open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("guarded AL report source has another active operation") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class GateReport:
    def __init__(self, controller, principal, arguments, normalized, state):
        from harness.execution_store import get_execution

        self.controller, self.principal = controller, principal
        from contextlib import closing

        from harness.db import connect

        with closing(connect(controller.database)) as conn:
            execution = get_execution(conn, controller.execution_id)
        self.identity = self._identity(execution, state)
        references = state.get("gate_manifests", [])
        if not isinstance(references, list) or any(not isinstance(r, dict) for r in references):
            raise ValueError(ERROR)
        self.before = json.loads(canonical(references))
        self.arguments, self.normalized = list(arguments), list(normalized)
        _, identifier, sha = arguments[3].split(":", 2)
        self.input = {"artifact_id": identifier, "sha256": sha}
        self.input_path = str(controller.artifacts.resolve(identifier, sha))
        self.input_value = _json(_read(self.input_path))
        if (arguments[1] == "discover" and not isinstance(self.input_value, list)) or (
            arguments[1] == "plan" and not isinstance(self.input_value, dict)
        ):
            raise ValueError("gate report input has the wrong JSON type")
        self.operation_id = "al-report-" + secrets.token_hex(16)
        self.result = None
        self.classification = None
        self.directory = controller.directory / "private-report-spools"
        if any(
            p.is_symlink() for p in (self.directory, *self.directory.parents)
        ) or self.directory.is_relative_to(Path(controller.envelope["repository"])):
            raise ValueError("private report spool location is invalid")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if (
            self.directory.stat().st_uid != os.getuid()
            or stat.S_IMODE(self.directory.stat().st_mode) != 0o700
        ):
            raise ValueError("private report spool ownership is invalid")
        self.streams, self.paths, self.sizes = {}, {}, {"stdout": 0, "stderr": 0}
        try:
            for name in self.sizes:
                fd, filename = tempfile.mkstemp(prefix=self.operation_id + "-", dir=self.directory)
                self.paths[name] = Path(filename)
                self.streams[name] = os.fdopen(fd, "w+b")
        except OSError:
            self.close()
            raise

    def _identity(self, execution, state):
        return {
            "execution": {
                k: execution[k]
                for k in ("id", "task_id", "binding", "host", "al_session_id", "al_run_id")
            },
            "state": {
                k: state.get(k) for k in ("session_id", "run_id", "identity", "runtime_origin")
            },
            "pin": Path(self.controller.initialization["pin_root"]).name,
        }

    def consume(self, channel, raw):
        self.sizes[channel] += len(raw)
        if self.sizes[channel] > (REPORT_LIMIT if channel == "stdout" else STDERR_LIMIT):
            raise ValueError("guarded AL report " + channel + " limit exceeded")
        self.streams[channel].write(raw)

    def publish(self, returncode):
        from contextlib import closing

        from harness.controller import HarnessController
        from harness.db import connect
        from harness.template_classification import validate_reference

        if self.result is not None:
            return self.result
        if returncode != 0:
            raise ValueError("guarded AL report command failed; no artifact published")
        with (
            self.controller.lock,
            _publication_lock(self.controller.initialization["coordination_lock"]),
            closing(connect(self.controller.database)) as conn,
        ):
            execution, state = self.controller.authority(HarnessController(conn), self.principal)
            if self._identity(execution, state) != self.identity or self.input_path != str(
                self.controller.artifacts.resolve(
                    **{
                        "artifact_id": self.input["artifact_id"],
                        "expected_sha256": self.input["sha256"],
                    }
                )
            ):
                raise ValueError(ERROR)
            validate_reference(self.classification)
            refs = state.get("gate_manifests", [])
            if (
                not isinstance(refs, list)
                or len(refs) != len(self.before) + 1
                or refs[:-1] != self.before
            ):
                raise ValueError(ERROR)
            ref = refs[-1]
            if not isinstance(ref, dict):
                raise ValueError(ERROR)
            sha = ref.get("sha256", "")
            expected = (
                Path(self.controller.initialization["source"]).parent
                / execution["al_session_id"]
                / "artifacts"
                / sha
            )
            if (
                not re.fullmatch(r"[0-9a-f]{64}", sha)
                or ref.get("id") != sha
                or ref.get("kind") != "gate-manifest"
                or ref.get("stored_path") != str(expected)
                or ref.get("source") != self.input_path
            ):
                raise ValueError(ERROR)
            raw = _read(expected)
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError(ERROR)
            manifest = _json(raw)
            if not isinstance(manifest, dict):
                raise ValueError(ERROR)
            stream = self.streams["stdout"]
            stream.flush()
            stream.seek(0)
            if _json(stream.read(REPORT_LIMIT + 1)) != manifest:
                raise ValueError(ERROR)
            if (
                manifest.get("schema_version") != 1
                or type(manifest.get("revision")) is not int
                or manifest.get("revision") != len(refs)
                or manifest.get("base_oid") != state["identity"]["merge_base"]
                or not isinstance(manifest.get("id"), str)
                or not manifest["id"]
                or len(manifest["id"]) > 128
            ):
                raise ValueError(ERROR)
            paths = manifest.get("paths")
            wanted = (
                self.input_value
                if self.arguments[1] == "discover"
                else self.input_value.get("paths")
            )
            if (
                not isinstance(paths, list)
                or not isinstance(wanted, list)
                or any(not isinstance(p, str) for p in paths + wanted)
                or not {
                    PurePosixPath(p).as_posix() if self.arguments[1] == "discover" else p
                    for p in wanted
                }
                <= set(paths)
                or any(
                    PurePosixPath(p).is_absolute() or ".." in PurePosixPath(p).parts for p in paths
                )
            ):
                raise ValueError(ERROR)
            if self.arguments[1] == "plan" and {
                k: v for k, v in manifest.items() if k not in {"id", "revision"}
            } != {k: v for k, v in self.input_value.items() if k not in {"id", "revision"}}:
                raise ValueError(ERROR)
            if not isinstance(manifest.get("units"), list) or not isinstance(
                manifest.get("gates"), list
            ):
                raise ValueError(ERROR)
            provenance = {
                "operation_id": self.operation_id,
                "binding": self.identity,
                "arguments": self.arguments,
                "normalized_arguments": self.normalized,
                "input": self.input,
                "prior_manifests": self.before,
                "canonical_reference": ref,
            }
            if any(not isinstance(u, str) for u in manifest["units"]) or any(
                not isinstance(g, dict) for g in manifest["gates"]
            ):
                raise ValueError(ERROR)
            coverage = manifest.get("service_coverage", {})
            if not isinstance(coverage, dict) or not isinstance(
                coverage.get("assessment", {}), dict
            ):
                raise ValueError(ERROR)
            assessment = coverage.get("assessment", {})
            if not isinstance(assessment.get("gaps", []), list) or any(
                not isinstance(assessment.get(k, {}), dict)
                for k in ("enumeration", "check_discovery")
            ):
                raise ValueError(ERROR)
            outcome = assessment.get("outcome", "unverified")
            if not isinstance(outcome, str) or len(outcome) > 32:
                raise ValueError(ERROR)
            enumeration = assessment.get("enumeration", {}).get("status", "unverified")
            checks = assessment.get("check_discovery", {}).get("status", "unverified")
            if any(
                not isinstance(value, str) or len(value) > 32 for value in (enumeration, checks)
            ):
                raise ValueError(ERROR)
            unresolved = sum(g.get("applicability") == "UNRESOLVED" for g in manifest["gates"])
            self.streams["stderr"].flush()
            self.streams["stderr"].seek(0)
            current = self.controller.state(execution)
            if (
                self._identity(execution, current) != self.identity
                or current.get("gate_manifests", []) != refs
            ):
                raise ValueError(ERROR)
            validate_reference(self.classification)
            artifact = self.controller.artifacts.capture_gate_manifest(raw, provenance)
            self.result = {
                "returncode": 0,
                "status": "Exited",
                "stdout": "",
                "stderr": redact(
                    self.streams["stderr"].read(STDERR_LIMIT).decode(errors="replace")
                ),
                "artifact": artifact,
                "manifest": {
                    "id": manifest["id"],
                    "revision": manifest["revision"],
                    "units": len(manifest["units"]),
                    "gates": len(manifest["gates"]),
                },
                "coverage": {
                    "outcome": outcome,
                    "enumeration": enumeration,
                    "check_discovery": checks,
                    "gap_count": len(assessment.get("gaps", [])),
                    "unresolved_gates": unresolved,
                    "incomplete": outcome not in {"identified", "exhaustive"}
                    or enumeration != "complete"
                    or checks != "complete"
                    or bool(unresolved),
                    "admission": "unverified",
                },
                "summary": (
                    "Report generated; check coverage and planning admission are separate. "
                    "Read the artifact for the full assessment."
                ),
            }
            return self.result

    def failure(self, returncode, error):
        self.streams["stderr"].flush()
        self.streams["stderr"].seek(0)
        return {
            "returncode": returncode,
            "status": "Failed",
            "stdout": "",
            "stderr": redact(self.streams["stderr"].read(STDERR_LIMIT).decode(errors="replace")),
            "artifact": None,
            "error": str(error)[:2048],
        }

    def close(self):
        for stream in self.streams.values():
            stream.close()
        for path in self.paths.values():
            path.unlink(missing_ok=True)
        with self.controller.lock:
            if self.controller.active_report is self:
                self.controller.active_report = None
