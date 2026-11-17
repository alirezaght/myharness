"""Genuine managed issuer: prepare one contained successor, without activation."""

import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

from . import runtime_successor_store as Store
from .runtime_pin import _directory, prepare_pin, verify_pin
from .runtime_pin_initialization import read_initialization
from .runtime_pin_transaction import (
    publication_lock,
    read,
    read_state,
    reserve_locked,
    runtime_directory,
    state_path,
    storage,
    subject,
    validate,
)
from .runtime_successor_census import require_quiescent
from .runtime_successor_channel import fingerprint, receive
from .runtime_successor_lineage import (
    revisions,
    source_anchors,
)
from .validator_origin import (
    capture,
    decode,
    digest,
    executable_state,
    safe,
    verify_snapshot,
)

REQUEST_FIELDS = {
    "schema_version",
    "request_id",
    "execution_id",
    "expected_epoch_id",
    "expected_authority_generation",
    "predecessor_subject",
    "approved_requirement_hash",
    "expected_candidate_identity",
    "distribution_identity",
    "authority_snapshot_ref",
    "reason",
}


def distribution(platform):
    context = sys.modules.get("_al_validator_context")
    if (
        context is None
        or context.request["mode"] != "snapshot"
        or not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.dont_write_bytecode
    ):
        raise ValueError("successor preparation requires the genuine managed issuer")
    root = Path(__file__).resolve().parents[1]
    trusted = verify_snapshot(root, context.request["expected"])
    pin = prepare_pin(Path(trusted["snapshot"]), platform, _directory(storage() / "pins"))
    verified = verify_pin(storage() / "pins", pin)
    return pin, verified


def inspect_successor(identifier):
    from .runtime_successor_inspect import inspect_successor as inspect

    return inspect(identifier)


def _frame(value, sequence, label, field):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "sequence", label, "payload"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or type(value["sequence"]) is not int
        or value["sequence"] != sequence
        or value[label] != field
        or not isinstance(value["payload"], dict)
    ):
        raise ValueError("successor coordinator message refused")
    return value["payload"]


def _authority(
    record_ref, handoff, context_path, resources_path=None, activation_path=None, inputs_path=None
):
    raw, metadata = capture(record_ref["stored_path"])
    record = decode(raw)
    if metadata["mode"] != 0o600 or digest(raw) != record_ref["sha256"]:
        raise ValueError("prepared Harness authority record changed")
    expected = Path(record_ref["stored_path"])
    base = Path(handoff).parent / "prepared-authority"
    legacy = base / "authority.json"
    import re

    versioned = (
        expected.name == "authority.json"
        and expected.parent.parent == base / "bridge-revisions"
        and re.fullmatch(r"[0-9a-f]{64}", expected.parent.name)
    )
    if expected != legacy and not versioned or record["kind"] != "prepared-harness-authority-v1":
        raise ValueError("prepared Harness authority location differs")
    root = safe(record["snapshot"])
    if root != expected.parent / "trusted":
        raise ValueError("prepared Harness snapshot location differs")
    raw, _ = capture(root / "manifest.json")
    if digest(raw) != record["manifest_sha256"]:
        raise ValueError("prepared Harness manifest changed")
    manifest = decode(raw)
    files = manifest["files"]
    if set(
        p.relative_to(root / "harness").as_posix()
        for p in (root / "harness").rglob("*")
        if p.is_file()
    ) != set(files):
        raise ValueError("prepared Harness snapshot membership differs")
    for name, sha in files.items():
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or digest(capture(root / "harness" / name)[0]) != sha
        ):
            raise ValueError("prepared Harness snapshot resource differs")
    interpreter = record["interpreter"]
    if executable_state(interpreter["executable"]) != {
        k: interpreter[k] for k in ("sha256", "mode")
    }:
        raise ValueError("prepared Harness interpreter changed")
    boot = (
        "import runpy,sys;sys.path.insert(0,"
        + repr(str(root))
        + ");runpy.run_module('harness.execution_prepared_authority',run_name='__main__')"
    )
    argv = [
        interpreter["executable"],
        "-I",
        "-B",
        "-c",
        boot,
        "--handoff",
        str(handoff),
        "--context-file",
        str(context_path),
    ]
    if resources_path:
        argv += ["--resources-file", str(resources_path)]
    if activation_path:
        argv += ["--activation-file", str(activation_path)]
    if inputs_path:
        argv += ["--inputs-file", str(inputs_path)]
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
    )
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError("prepared Harness authority validation refused")
    return decode(result.stdout)


@contextlib.contextmanager
def _session_lock(transaction):
    runtime = runtime_directory(transaction)
    descriptor = os.open(
        runtime / ("." + transaction["session_id"] + ".lock"), os.O_RDWR | os.O_NOFOLLOW
    )
    try:
        info = os.fstat(descriptor)
        if (
            info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or not __import__("stat").S_ISREG(info.st_mode)
            or info.st_nlink != 1
        ):
            raise ValueError("unsafe successor session lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _policy(verified, platform):
    namespace = {"__name__": "_prepared_model_policy"}
    name = platform + "/skills/al/scripts/al_models.py"
    exec(
        compile(verified["bytes"][name], str(verified["root"] / "payload" / name), "exec"),
        namespace,
    )
    resources = verified["manifest"]["resource_map"]
    modules = {
        logical[len("scripts/") :]: verified["bytes"][path]
        for logical, path in resources.items()
        if logical.startswith("scripts/al_") and logical.endswith(".py")
    }
    implementation = hashlib.sha256(b"".join(modules[k] for k in sorted(modules))).hexdigest()
    value = {
        "id": "al-proof-policy",
        "version": 1,
        "runtime_implementation": implementation,
        "models": namespace["POLICY"],
    }
    return {
        "id": value["id"],
        "version": 1,
        "digest": digest(json.dumps(value, sort_keys=True, allow_nan=False).encode()),
    }


def _request(value, identifier):
    from .runtime_successor_recovery import MODE, RECOVERY_FIELDS

    recovery = isinstance(value, dict) and value.get("schema_version") == 2
    if (
        not isinstance(value, dict)
        or set(value) != (REQUEST_FIELDS | RECOVERY_FIELDS if recovery else REQUEST_FIELDS)
        or value["schema_version"] not in {1, 2}
        or value["request_id"] != identifier
        or type(value["expected_authority_generation"]) is not int
        or value["expected_authority_generation"] < 0
        or not isinstance(value["reason"], str)
        or len(value["reason"].strip()) < 12
        or set(value["predecessor_subject"]) != {"session_id", "run_id"}
    ):
        raise ValueError("invalid successor preparation request")
    if recovery:
        import re

        if value["transition_mode"] not in {MODE, "fresh-planning-intact"} or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", value["preview_sha256"]
        ):
            raise ValueError("invalid missing-state recovery request")
        for name in ("recovery_lineage_ref", "settlement_ref"):
            if (
                name == "recovery_lineage_ref"
                and value["transition_mode"] == "fresh-planning-intact"
            ):
                if value[name] is not None or not re.fullmatch(
                    r"[0-9a-f]{64}", str(value["expected_predecessor_state_sha256"])
                ):
                    raise ValueError("invalid intact fresh-planning source binding")
                continue
            ref = value[name]
            if (
                not isinstance(ref, dict)
                or set(ref) != {"stored_path", "sha256"}
                or not re.fullmatch(r"[0-9a-f]{64}", ref.get("sha256", ""))
            ):
                raise ValueError("invalid missing-state recovery reference")
        if not isinstance(value["authority_handoff_ref"], dict) or set(
            value["authority_handoff_ref"]
        ) != {"path", "sha256"}:
            raise ValueError("invalid missing-state recovery handoff")
    Store.request_id(identifier)
    return value


def prepare_successor(repository, platform, identifier, coordinator_fd):
    from .runtime_successor_channel import (
        PreparationTransport,
        classify_preparation_error,
        preparation_error,
        send,
    )

    coordinator = PreparationTransport(socket.socket(fileno=coordinator_fd))
    os.set_inheritable(coordinator_fd, False)
    coordinator.settimeout(300)
    try:
        return _prepare_successor(repository, platform, identifier, coordinator)
    except BaseException as error:
        directory = Store.directory(identifier)
        sequence = coordinator.sequence
        code = classify_preparation_error(error)
        diagnostic = preparation_error(
            identifier, sequence, "start" if sequence == 2 else "preparation", code
        )
        try:
            Store.immutable(
                directory / ("diagnostic-" + secrets.token_hex(16) + ".json"), diagnostic
            )
        except (OSError, ValueError):
            pass
        try:
            send(coordinator, diagnostic)
        except (OSError, ValueError):
            pass
        raise
    finally:
        coordinator.close()


def _prepare_successor(repository, platform, identifier, coordinator):
    current = distribution(platform)
    request = _request(_frame(receive(coordinator), 1, "operation", "prepare"), identifier)
    if (
        request["schema_version"] == 2
        and request["transition_mode"] == "fresh-planning-missing-state"
    ):
        from .runtime_successor_recovery import prepare_recovery

        return prepare_recovery(repository, platform, identifier, coordinator, request, *current)
    prior_subject = subject(repository, platform, request["predecessor_subject"]["session_id"])
    directory = Store.directory(identifier)
    request_ref = Store.immutable(directory / "request.json", request)
    journal_path = directory / "journal.json"
    if journal_path.exists():
        journal = read(journal_path)
        if journal["request_ref"] != request_ref:
            raise ValueError("successor request replay differs")
        successor_subject = journal["successor_subject"]
    else:
        successor_subject = subject(repository, platform, "successor-" + secrets.token_hex(16))
        journal = {
            "schema_version": 1,
            "kind": "successor-preparation-journal-v1",
            "state": "PREPARING",
            "request_ref": request_ref,
            "successor_subject": successor_subject,
        }
        from .registry import write_json

        write_json(journal_path, journal)
    with contextlib.ExitStack() as locks:
        capabilities = {
            str(v): locks.enter_context(publication_lock(v))
            for v in sorted(
                [prior_subject, successor_subject],
                key=lambda v: str(storage() / "runs" / fingerprint(v)),
            )
        }
        prior_lock, successor_lock = (
            capabilities[str(prior_subject)],
            capabilities[str(successor_subject)],
        )
        prior = read(prior_lock.path)
        validate(prior, prior_subject)
        if (
            prior["status"] != "ACTIVE"
            or prior["run_id"] != request["predecessor_subject"]["run_id"]
        ):
            raise ValueError("predecessor protected identity differs")
        locks.enter_context(_session_lock(prior))
        state = read_state(prior)
        require_quiescent(repository, transaction=prior, state=state)
        from .runtime_successor_payload import requested_distribution

        reservation = read(successor_lock.path) if successor_lock.path.exists() else None
        if reservation is not None:
            validate(reservation, successor_subject)
        pin, verified = requested_distribution(
            platform, request, current, predecessor=prior, reservation=reservation
        )
        anchors = source_anchors(prior, state)
        if (
            revisions(state["task_package"]["sha256"], state["amendments"])[-1]
            != request["approved_requirement_hash"]
        ):
            raise ValueError("accepted requirement lineage changed")
        input_ref = Store.immutable(
            directory / "inputs.json",
            {
                "predecessor": prior,
                "execution_id": request["execution_id"],
            },
        )
        authorization = _authority(
            request["authority_snapshot_ref"],
            state["integration"]["handoff"],
            input_ref["stored_path"],
            inputs_path=input_ref["stored_path"],
        )
        successor = reserve_locked(
            successor_lock,
            pin,
            private_session=True,
            harness_binding=authorization["harness_binding"],
        )
        if successor["pin"] != pin:
            raise ValueError("reserved successor pin differs")
        ignored = runtime_directory(successor) / ".gitignore"
        if ignored.exists():
            if capture(ignored)[0] != b"*\n":
                raise ValueError("successor runtime ignore policy differs")
        else:
            fd = os.open(ignored, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"*\n")
                stream.flush()
                os.fsync(stream.fileno())
        predecessor_ref = Store.archive_bytes(
            identifier, "predecessor-state.json", capture(state_path(prior))[0]
        )
        binding_ref = Store.archive_bytes(
            identifier, "predecessor-binding.json", capture(prior_lock.path)[0]
        )
        initial = read_initialization(prior)
        authority_directory = str(Path(state["integration"]["handoff"]).parent)
        plan = {
            "schema_version": 1,
            "kind": "successor-start-plan-v1",
            "request": request,
            "storage": str(storage()),
            "authority_directory": authority_directory,
            "predecessor_transaction": prior,
            "successor_transaction": {**successor, "initialization": None, "status": "RESERVED"},
            "predecessor_state_ref": predecessor_ref,
            "predecessor_binding_ref": binding_ref,
            "source_anchors": anchors,
            "predecessor_initialization": initial,
        }
        plan_ref = Store.immutable(directory / "plan.json", plan)
        Store.fence(successor_subject, identifier, plan_ref["sha256"])
        try:
            from .runtime_successor_start import start_locked

            return start_locked(
                coordinator,
                request,
                directory,
                journal_path,
                journal,
                prior_lock,
                successor_lock,
                prior,
                successor,
                state,
                plan_ref,
                anchors,
                verified,
            )
        except BaseException as error:
            from .registry import write_json

            journal.update(
                state="PREPARATION_FAILED",
                safe_diagnostic="Successor preparation refused; protected progress retained",
                error_type=type(error).__name__,
            )
            write_json(journal_path, journal)
            raise


class UnsupportedRuntimeSuccessorMode(ValueError):
    """The existing successor protocol requires a Harness-integrated predecessor."""

    code = "UNSUPPORTED_RUNTIME_SUCCESSOR_MODE"

    def __init__(self):
        super().__init__(
            self.code + ": runtime-successor inputs requires a Harness integration with "
            "binding.execution_id and handoff; standalone pin migration is unsupported. "
            "Use runtime-successor origin to inspect the existing pin. For a Harness execution, "
            "use execution runtime-prepare/runtime-inspect/runtime-activate through the trusted "
            "Harness operator. For standalone AL, settle existing work and start a fresh run "
            "from the preserved accepted package; revalidate all verification and approval credit. "
            "See reference/implementation-continuity.md and reference/distribution.md."
        )


def _successor_integration(state):
    integration = state.get("integration")
    binding = integration.get("binding") if isinstance(integration, dict) else None
    if (not isinstance(binding, dict)
            or not isinstance(binding.get("execution_id"), str) or not binding["execution_id"]
            or not isinstance(integration.get("handoff"), str) or not integration["handoff"]):
        raise UnsupportedRuntimeSuccessorMode()
    return integration


def preparation_inputs(repository, platform, session, identifier, authority_ref):
    from .runtime_successor_lineage import git, observe_candidate

    _, verified = distribution(platform)
    previous_subject = subject(repository, platform, session)
    with publication_lock(previous_subject) as capability:
        previous = read(capability.path)
        validate(previous, previous_subject)
        with _session_lock(previous):
            state = read_state(previous)
            if previous["status"] != "ACTIVE" or not state:
                raise ValueError("predecessor is not active")
            integration = _successor_integration(state)
            request = {
                "predecessor": previous,
                "execution_id": integration["binding"]["execution_id"],
            }
            ref = Store.immutable(Store.directory(identifier) / "inputs.json", request)
            authorization = _authority(
                authority_ref,
                integration["handoff"],
                ref["stored_path"],
                inputs_path=ref["stored_path"],
            )
            return {
                "candidate_identity": observe_candidate(
                    repository, state["baseline_head"], authorization["classified_paths"]
                ),
                "approved_requirement_hash": revisions(
                    state["task_package"]["sha256"], state["amendments"]
                )[-1],
                "predecessor_subject": {"session_id": session, "run_id": previous["run_id"]},
                "distribution_identity": verified["manifest"]["closure_digest"],
                "predecessor": previous,
                "initialization_ref": previous["initialization"],
                "predecessor_state_sha256": digest(capture(state_path(previous))[0]),
                "source_inventory": {
                    "missing_tracked_files": [
                        os.fsdecode(p)
                        for p in git(repository, "ls-files", "--deleted", "-z").split(b"\0")
                        if p
                    ]
                },
            }
