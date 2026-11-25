"""Issuer-derived private epoch storage, independent of checkout lifetime."""

import re
from pathlib import Path

from .runtime_pin import _directory
from .validator_origin import safe

LAYOUT = "harness-durable-epoch-v1"
FIELDS = {"runtime_layout", "runtime_directory", "harness_binding"}


def directory(transaction, storage, *, create=False):
    binding = transaction.get("harness_binding")
    if (not isinstance(binding, dict) or set(binding) != {
            "authority_identity", "execution_id", "epoch_id", "authority_generation"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("authority_identity")))
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", str(binding.get("execution_id")))
            or binding.get("epoch_id") != ("legacy-0" if binding.get("authority_generation") == 0 else "epoch-" + transaction["run_id"])
            or type(binding.get("authority_generation")) is not int
            or binding["authority_generation"] < 0):
        raise ValueError("invalid durable Harness epoch binding")
    root = safe(storage)
    expected = root / "harness-runtimes" / binding["authority_identity"] / binding["execution_id"] / binding["epoch_id"]
    repository = Path(transaction["repository_identity"]["root"])
    if (transaction.get("runtime_layout") != LAYOUT
            or transaction.get("runtime_directory") != str(expected)
            or root.is_relative_to(repository) or repository.is_relative_to(root)):
        raise ValueError("durable runtime is not derived from protected issuer storage")
    if create:
        current = root
        for component in expected.relative_to(root).parts:
            current = _directory(current / component)
    else:
        # Missing runtime remains missing; validation never recreates lost authority.
        current = root
        for component in expected.relative_to(root).parts:
            current = safe(current / component)
            if current.exists():
                _directory(current, create=False)
    return safe(expected)


def discover(storage_root):
    """Enumerate only independently protected v3 bindings, never state-provided paths."""
    from .runtime_pin_transaction import key, read
    from .runtime_stale_runs import is_stale

    root = safe(storage_root)
    runs = root / "runs"
    if not runs.exists():
        return []
    _directory(runs, create=False)
    paths = sorted(runs.glob("*.json"))
    if len(paths) > 10000:
        raise ValueError("protected durable activity enumeration exceeds limit")
    found = []
    for path in paths:
        value = read(path)
        if value.get("schema_version") != 3:
            continue
        subject = {k: value[k] for k in ("repository_identity", "platform", "session_id")}
        if (value.get("kind") != "run-pin-binding-v3" or path.name != key(subject) + ".json"
                or value.get("status") not in {"RESERVED", "ACTIVE"}
                or not re.fullmatch(r"[0-9a-f]{8}", value.get("run_id", ""))):
            raise ValueError("durable activity protected transaction is invalid")
        if is_stale(value):
            # Its repository is gone, so the run cannot resume; doctor reports it as stale and
            # `alctl prune-stale-runs` archives it (#37).
            continue
        target = directory(value, root)
        if value["status"] == "RESERVED" or not (target / (value["session_id"] + ".json")).is_file():
            raise ValueError("durable run is reserved or its active state is unavailable")
        found.append({"repository": value["repository_identity"], "platform": value["platform"],
                      "runtime_root": str(target)})
    return found
