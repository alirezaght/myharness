"""Resolve durable runtime only from an issuer-bound immutable handoff."""

import json
import os
import re
import stat
from pathlib import Path

from harness.execution_contract import digest
from harness.execution_models import ExecutionConflict
from harness.template_classification import _ordinary


def durable_runtime(binding, repository, *, session_id=None, run_id=None):
    if not isinstance(binding, dict) or set(binding) != {"storage", "transaction"}:
        raise ExecutionConflict("durable runtime lacks its issuer binding")
    expected = binding["transaction"]
    storage = Path(binding["storage"])
    subject = {k: expected[k] for k in ("repository_identity", "platform", "session_id")}
    path = storage / "runs" / (digest(subject).split(":")[1] + ".json")
    raw, mode = _ordinary(path, 4 * 1024 * 1024)
    current = json.loads(raw)
    stable = {k: v for k, v in expected.items() if k not in {"status", "initialization"}}
    if (
        mode != 0o600
        or current.get("schema_version") != 3
        or current.get("kind") != "run-pin-binding-v3"
        or current.get("status") not in {"RESERVED", "ACTIVE"}
        or {k: v for k, v in current.items() if k not in {"status", "initialization"}} != stable
        or expected["repository_identity"]["root"] != str(repository)
        or session_id is not None
        and current["session_id"] != session_id
        or run_id is not None
        and current["run_id"] != run_id
    ):
        raise ExecutionConflict("durable runtime protected transaction differs")
    harness = current["harness_binding"]
    if (
        set(harness) != {"authority_identity", "execution_id", "epoch_id", "authority_generation"}
        or not re.fullmatch(r"[0-9a-f]{64}", harness["authority_identity"])
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", harness["execution_id"])
        or harness["epoch_id"]
        != ("legacy-0" if harness["authority_generation"] == 0 else "epoch-" + current["run_id"])
        or type(harness["authority_generation"]) is not int
        or harness["authority_generation"] < 0
    ):
        raise ExecutionConflict("durable runtime Harness identity differs")
    directory = (
        storage
        / "harness-runtimes"
        / harness["authority_identity"]
        / harness["execution_id"]
        / harness["epoch_id"]
    )
    if (
        current.get("runtime_layout") != "harness-durable-epoch-v1"
        or current["runtime_directory"] != str(directory)
        or storage.is_relative_to(repository)
        or Path(repository).is_relative_to(storage)
    ):
        raise ExecutionConflict("durable runtime directory is not issuer-derived")
    cursor = storage
    for part in [None, *directory.relative_to(storage).parts]:
        if part is not None:
            cursor = cursor / part
        if any(p.is_symlink() for p in (cursor, *cursor.parents)):
            raise ExecutionConflict("durable runtime alias refused")
        info = cursor.stat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()
        ):
            raise ExecutionConflict("durable runtime storage must be private and owned")
    return directory


def selected_runtime_binding(execution, envelope):
    binding = envelope.get("runtime_binding")
    if binding is None:
        raw, mode = _ordinary(
            Path(execution["handoff_path"]).parent / "runtime-binding.json", 4 * 1024 * 1024
        )
        if mode != 0o600:
            raise ExecutionConflict("runtime binding publication permissions changed")
        binding = json.loads(raw)
    transaction = binding["transaction"]
    harness = transaction["harness_binding"]
    expected_authority = digest(
        {"database": envelope["database"], "approval": execution["binding"]}
    ).split(":")[1]
    if (
        harness["authority_identity"] != expected_authority
        or harness["execution_id"] != execution["id"]
        or harness["epoch_id"] != execution["epoch_id"]
        or harness["authority_generation"] != execution["authority_generation"]
    ):
        raise ExecutionConflict("durable runtime does not match execution epoch authority")
    return binding
