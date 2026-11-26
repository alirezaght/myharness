"""Prepare immutable sealed-control bindings outside policy admission transactions."""

import ast
import json
import secrets
import subprocess
import sys
from pathlib import Path

from harness.policy.contracts import (
    PolicyError,
    qualification_diagnostic,
    qualification_require,
    require,
)
from harness.policy.control_git import configuration, environment, file_state
from harness.policy.control_launcher import expected_launcher

PROFILE = "sealed-al-retry-v1"
FINISH = "sealed-al-writer-finish-v1"
WRITER_FIELDS = ("id", "agent", "task", "role", "state", "files", "contribution_before")


def _closure(root, records, qualification_ref):
    states = []
    for name, value in sorted(records.items()):
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts)
        state = file_state(root / relative, 16 * 1024 * 1024)
        qualification_require(
            state["sha256"] == value["sha256"], "source_bytes_mismatch", qualification_ref
        )
        states.append(state)
    return states


def _prepare(operation, args, state, qualification_ref):
    def check(condition, cause):
        qualification_require(condition, cause, qualification_ref)

    writer_binding = None
    if len(args) == 4 and args[0] == "retry" and args[2] == "--reason" and args[3].strip():
        method = PROFILE
    else:
        require(
            len(args) == 6
            and args[:3] == ["writer", "finish", "--id"]
            and args[4] == "--outcome"
            and args[5] in {"success", "failed", "abandoned"},
            "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
        )
        writer = state.get("writers", {}).get(args[3])
        require(writer is not None and writer["state"] == "ACTIVE", "AUTHORITY_STALE")
        writer_binding = {key: writer.get(key) for key in WRITER_FIELDS}
        method = FINISH
    pin = Path(operation.initialization["pin_root"])
    manifest_file = pin / "manifest.json"
    manifest_state = file_state(manifest_file)
    manifest = json.loads(manifest_file.read_bytes())
    records = manifest["complete_file_manifest"]
    platform = operation.envelope["host"]
    required = {
        "installer/runtime_retry_control.py",
        f"{platform}/skills/al/scripts/al_retry_control.py",
    }
    check(required <= records.keys(), "source_inventory_mismatch")
    from harness.policy.control_sources_claude import EXPECTED as CLAUDE
    from harness.policy.control_sources_codex import EXPECTED as CODEX
    from harness.policy.control_sources_installer import EXPECTED as INSTALLER

    expected = INSTALLER | (CODEX if platform == "codex" else CLAUDE)
    check(
        all(records.get(name, {}).get("sha256") == value for name, value in expected.items()),
        "source_inventory_mismatch",
    )
    executable_names = {
        name
        for name in records
        if name == "alctl.py"
        or name.startswith("installer/")
        and name.endswith(".py")
        or name.startswith(platform + "/skills/al/scripts/")
        and name.endswith(".py")
    }
    check(executable_names == expected.keys(), "source_inventory_mismatch")
    states = [manifest_state, *_closure(pin / "payload", records, qualification_ref)]
    launcher = Path(operation.package["launcher"]).resolve(strict=True)
    states.append(file_state(launcher))
    tree = ast.parse(launcher.read_text())
    requests = [
        ast.literal_eval(n.value.args[0])
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "_boot"
    ]
    check(len(requests) == 1, "launcher_request_mismatch")
    request = requests[0]
    snapshot = Path(request["root"])
    origin = json.loads((snapshot / "origin.json").read_bytes())
    for name, key in (("origin.json", "origin_sha256"), ("receipt.json", "receipt_sha256")):
        state = file_state(snapshot / name)
        check(
            state["sha256"] == request["expected"][key],
            "origin_binding_mismatch" if name == "origin.json" else "receipt_binding_mismatch",
        )
        states.append(state)
    check(
        "installer/runtime_retry_control.py" in origin["closure"],
        "origin_inventory_mismatch",
    )
    check(
        all(
            origin["closure"].get(name, {}).get("sha256") == value
            for name, value in (INSTALLER | CODEX | CLAUDE).items()
        ),
        "origin_inventory_mismatch",
    )
    check(
        request["interpreter"] == manifest["interpreter"]["executable"],
        "interpreter_binding_mismatch",
    )
    states.extend(_closure(snapshot, origin["closure"], qualification_ref))
    check(
        launcher.read_bytes() == expected_launcher(request, snapshot, INSTALLER),
        "launcher_bytes_mismatch",
    )
    interpreter = manifest["interpreter"]["canonical_executable"]
    executable = file_state(Path(interpreter))
    check(
        executable["sha256"] == manifest["interpreter"]["sha256"],
        "interpreter_bytes_mismatch",
    )
    states.append(executable)
    # Fixed known concrete implementation, never the /usr/bin/git launcher or PATH.
    git = (
        Path("/Library/Developer/CommandLineTools/usr/bin/git")
        if sys.platform == "darwin"
        else Path("/usr/bin/git")
    )
    git = git.resolve(strict=True)
    git_state = file_state(git)
    result = subprocess.run(
        [str(git), "--version"], env=environment(), capture_output=True, timeout=5, check=True
    )
    allowed = {
        b"git version 2.50.1 (Apple Git-155)\n",
        b"git version 2.54.0 (Apple Git-157)\n",
        b"git version 2.50.1\n",
        b"git version 2.54.0\n",
    }
    check(result.stdout in allowed, "git_version_unsupported")
    states.append(git_state)
    repository = str(Path(operation.envelope["repository"]).resolve())
    config = configuration(repository)
    from harness.policy.control_boundary import prepare as boundary

    isolation, isolation_state = boundary(
        operation, manifest["interpreter"]["executable"], qualification_ref=qualification_ref
    )
    main_isolation, main_state = boundary(
        operation,
        manifest["interpreter"]["executable"],
        al=True,
        qualification_ref=qualification_ref,
    )
    return Qualification(
        operation,
        tuple(states),
        repository,
        config,
        str(git),
        git_state,
        manifest["interpreter"]["executable"],
        manifest["pin_identity"],
        platform,
        tuple(args),
        method,
        writer_binding,
        isolation,
        isolation_state,
        main_isolation,
        main_state,
        qualification_ref,
    )


def prepare(operation, args, state):
    qualification_ref = secrets.token_hex(16)
    try:
        return _prepare(operation, args, state, qualification_ref)
    except PolicyError as error:
        if error.diagnostic is not None:
            diagnostic = qualification_diagnostic(error.diagnostic)
            diagnostic["qualification_ref"] = qualification_ref
            diagnostic.pop("observation_id", None)
            raise PolicyError(error.code, diagnostic=diagnostic) from None
        qualification_require(False, "preparation_failed", qualification_ref)
    except Exception:
        qualification_require(False, "preparation_failed", qualification_ref)


class Qualification:
    def __init__(
        self,
        operation,
        states,
        repository,
        config,
        git,
        git_state,
        interpreter,
        pin,
        platform,
        args,
        method,
        writer_binding,
        isolation,
        isolation_state,
        main_isolation,
        main_state,
        qualification_ref,
    ):
        self.operation, self.states, self.repository, self.config = (
            operation,
            states,
            repository,
            config,
        )
        self.git, self.git_state, self.interpreter, self.pin, self.platform, self.args = (
            git,
            git_state,
            interpreter,
            pin,
            platform,
            args,
        )
        self.method, self.writer_binding = method, writer_binding
        self.isolation, self.isolation_state = isolation, isolation_state
        self.main_isolation, self.main_state = main_isolation, main_state
        self.probe_ref = qualification_ref
        self.channel = None
        self.verified = False
        self.receipt = None

    def validate(self):
        try:
            self._validate()
        except PolicyError as error:
            if error.diagnostic is not None:
                raise
            qualification_require(False, "preparation_failed", self.probe_ref)
        except Exception:
            qualification_require(False, "preparation_failed", self.probe_ref)

    def _validate(self):
        qualification_require(
            all(file_state(Path(state["path"])) == state for state in self.states),
            "source_state_changed",
            self.probe_ref,
        )
        qualification_require(
            configuration(self.repository) == self.config,
            "git_configuration_changed",
            self.probe_ref,
        )
        qualification_require(
            file_state(self.isolation.policy) == self.isolation_state
            and file_state(self.main_isolation.policy) == self.main_state,
            "boundary_profile_changed",
            self.probe_ref,
        )
        self.verified = True

    @property
    def binding(self):
        return {
            "profile": self.method,
            "writer_id": self.args[3] if self.method == FINISH else None,
            "outcome": self.args[5] if self.method == FINISH else None,
            "pin_identity": self.pin,
            "interpreter": self.interpreter,
            "git_identity": self.git_state["sha256"],
            "helper_boundary": "darwin-deny-process-fork-v1",
            "qualification_probe_ref": self.probe_ref,
            "main_and_helper_profiles_tested": True,
            "main_profile_identity": self.main_state["sha256"],
            "helper_profile_identity": self.isolation_state["sha256"],
        }

    def qualifies(self, process):
        try:
            self.validate()
        except (OSError, ValueError):
            return False
        return (
            self.channel is not None
            and self.channel.completed
            and process.returncode == self.channel.exit_code
        )
