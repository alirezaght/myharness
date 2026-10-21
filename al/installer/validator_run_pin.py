"""Read-only original pin data selected by the protected validator registry."""

import os
from pathlib import Path
import sys
import types

from .execution_process import observe
from .interpreter_execution import validate_live
from .runtime_pin import _directory, verify_pin
from .runtime_pin_initialization import read_initialization
from .runtime_pin_transaction import key, read, read_state, subject, validate
from .validator_origin import canonical, digest, safe


def capture_run_pin(captured, repository, session, run, platform):
    # Canonical callers do not acquire authority by searching target-selected
    # storage or HOME. A new-origin terminal query needs its managed launcher.
    if captured["mode"] != "snapshot":
        return None
    root = safe(captured["snapshot"])
    if (
        root.parent.name != "snapshots"
        or root.parents[1].name != "cli"
        or root.parents[2].name != "installer"
    ):
        raise ValueError("unknown protected terminal registry layout")
    base = safe(root.parents[3] / "al-run-pins")
    value = subject(repository, platform, session)
    transaction_path = safe(base / "runs" / (key(value) + ".json"))
    if not transaction_path.exists():
        return None
    for directory in (base, base / "runs", base / "initializations", base / "pins"):
        _directory(directory, create=False)
    transaction = read(transaction_path)
    validate(transaction, value)
    if transaction["status"] != "ACTIVE" or transaction["run_id"] != run:
        raise ValueError("terminal query requires the exact active protected run binding")
    verified = verify_pin(base / "pins", transaction["pin"], readonly=True)
    manifest = verified["manifest"]
    if (
        manifest["platform"] != platform
        or manifest["interpreter"] != captured["origin"]["interpreter"]
    ):
        raise ValueError("terminal validator and original pin platform/interpreter differ")
    validate_live(
        manifest["interpreter_execution_identity"], manifest["interpreter"], observe(os.getpid())
    )
    initial = read_initialization(transaction, storage_root=base)
    state = read_state(transaction)
    if state is None:
        raise ValueError("terminal pinned state is missing")
    current = captured["origin"]["platforms"][platform]["resources"]
    original = manifest["resource_map"]
    expected = {name if name.startswith("reference/") else "scripts/" + name for name in current}
    if {name for name in original if name.startswith(("scripts/", "reference/"))} != expected:
        raise ValueError("terminal runtime resource membership differs from original pin policy")
    identities = {}
    for name, path in current.items():
        logical = name if name.startswith("reference/") else "scripts/" + name
        member = original[logical]
        if (
            verified["bytes"][member] != captured["bytes"][path]
            or manifest["complete_file_manifest"][member]["mode"]
            != captured["origin"]["closure"][path]["mode"]
        ):
            raise ValueError("terminal current policy/resource differs from original pin: " + name)
        identities[name] = str(verified["root"] / "payload" / member)
    if (
        read(transaction_path) != transaction
        or read_initialization(transaction, storage_root=base) != initial
    ):
        raise ValueError("protected run binding moved during terminal capture")
    return {
        "schema_version": 1,
        "kind": "terminal-run-pin-data-v1",
        "storage": str(base),
        "transaction": transaction,
        "initialization_digest": digest(canonical(initial)),
        "resource_identities": identities,
        "execution_origin": {
            "platform": platform,
            "closure_digest": manifest["closure_digest"],
            "origin_digest": manifest["pin_identity"],
            "physical_scripts_root": str(
                verified["root"] / "payload" / platform / "skills/al/scripts"
            ),
        },
    }


def install_run_context(data):
    if data is None:
        return
    if "_al_run_pin_context" in sys.modules:
        raise ValueError("terminal original pin context already installed")
    context = types.ModuleType("_al_run_pin_context")
    context.transaction = data["transaction"]
    context.storage = data["storage"]
    context.readonly = True
    sys.modules[context.__name__] = context
