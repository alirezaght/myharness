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
    path = Store.directory(identifier) / "receipt.json"
    if not path.exists():
        return {"state": "PREPARING", "request_id": identifier, "product_authority": False}
    value = read(path)
    return {
        "state": "PREPARED",
        "request_id": identifier,
        "receipt": value,
        "receipt_ref": {"stored_path": str(path), "sha256": digest(capture(path)[0])},
        "product_authority": False,
        "native_resumed": False,
    }


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


def _authority(record_ref, handoff, context_path, resources_path=None, activation_path=None, inputs_path=None):
    raw, metadata = capture(record_ref["stored_path"])
    record = decode(raw)
    if metadata["mode"] != 0o600 or digest(raw) != record_ref["sha256"]:
        raise ValueError("prepared Harness authority record changed")
    expected = Path(handoff).parent / "prepared-authority/authority.json"
    if (
        Path(record_ref["stored_path"]) != expected
        or record["kind"] != "prepared-harness-authority-v1"
    ):
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
    if (
        not isinstance(value, dict)
        or set(value) != REQUEST_FIELDS
        or value["schema_version"] != 1
        or value["request_id"] != identifier
        or type(value["expected_authority_generation"]) is not int
        or value["expected_authority_generation"] < 0
        or not isinstance(value["reason"], str)
        or len(value["reason"].strip()) < 12
        or set(value["predecessor_subject"]) != {"session_id", "run_id"}
    ):
        raise ValueError("invalid successor preparation request")
    Store.request_id(identifier)
    return value


def prepare_successor(repository, platform, identifier, coordinator_fd):
    pin, verified = distribution(platform)
    coordinator = socket.socket(fileno=coordinator_fd)
    os.set_inheritable(coordinator_fd, False)
    coordinator.settimeout(300)
    request = _request(_frame(receive(coordinator), 1, "operation", "prepare"), identifier)
    if request["distribution_identity"] != verified["manifest"]["closure_digest"]:
        raise ValueError("successor distribution identity changed")
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
        require_quiescent(repository)
        state = read_state(prior)
        if any(w["state"] in {"ACTIVE", "UNRESOLVED"} for w in state["writers"].values()) or any(
            e["status"] == "RUNNING" or e.get("interrupted") and not e.get("reconciliation")
            for e in state["evidence"]["entries"]
        ):
            raise ValueError("predecessor operations are unsettled")
        anchors = source_anchors(prior, state)
        if (
            revisions(state["task_package"]["sha256"], state["amendments"])[-1]
            != request["approved_requirement_hash"]
        ):
            raise ValueError("accepted requirement lineage changed")
        successor = reserve_locked(successor_lock, pin, private_session=True)
        if successor["pin"] != pin:
            retained = verify_pin(storage() / "pins", successor["pin"])
            stable_fields = (
                "distribution_identity",
                "platform",
                "complete_file_manifest",
                "resource_map",
                "interpreter",
                "closure_digest",
            )
            if any(retained["manifest"][k] != verified["manifest"][k] for k in stable_fields):
                raise ValueError("reserved successor distribution or interpreter differs")
            verified = retained
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
                safe_diagnostic=str(error)[:512],
                error_type=type(error).__name__,
            )
            write_json(journal_path, journal)
            raise
        finally:
            coordinator.close()


def preparation_inputs(repository, platform, session, identifier, authority_ref):
    from .runtime_successor_lineage import observe_candidate
    _, verified = distribution(platform)
    previous_subject = subject(repository, platform, session)
    with publication_lock(previous_subject) as capability:
        previous = read(capability.path)
        validate(previous, previous_subject)
        with _session_lock(previous):
            state = read_state(previous)
            if previous['status'] != 'ACTIVE' or not state:
                raise ValueError('predecessor is not active')
            request = {'predecessor': previous, 'execution_id': state['integration']['binding']['execution_id']}
            ref = Store.immutable(Store.directory(identifier) / 'inputs.json', request)
            authorization = _authority(authority_ref,state['integration']['handoff'],ref['stored_path'],inputs_path=ref['stored_path'])
            return {'candidate_identity': observe_candidate(repository,state['baseline_head'],authorization['classified_paths']),
                'approved_requirement_hash':revisions(state['task_package']['sha256'],state['amendments'])[-1],
                'predecessor_subject':{'session_id':session,'run_id':previous['run_id']},
                'distribution_identity':verified['manifest']['closure_digest']}
