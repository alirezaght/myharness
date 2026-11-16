"""First-run durable reservation through the sealed, read-only Harness authority."""

from pathlib import Path

from . import runtime_successor_store as Store
from .runtime_pin_transaction import key, read, runtime_directory, storage, subject, validate
from .validator_origin import capture, decode, digest


def binding_for_start(value, argv):
    from .runtime_successor import _authority

    if argv.count("--handoff") != 1:
        raise ValueError("Harness start requires one explicit authority handoff")
    index = argv.index("--handoff")
    if index + 1 >= len(argv):
        raise ValueError("Harness start handoff missing")
    path = Path(argv[index + 1])
    raw, info = capture(path)
    envelope = decode(raw)
    if info["mode"] != 0o600 or envelope.get("runtime_layout") != "harness-durable-epoch-v1":
        return None  # Existing sealed v1/v2 handoffs retain their original layout.
    handoff_ref = {"path": str(path), "sha256": "sha256:" + digest(raw)}
    inputs = {"kind": "initial-harness-runtime-inputs-v1", "subject": value,
              "execution_id": envelope["execution_id"], "authority_handoff_ref": handoff_ref}
    ref = Store.immutable(Store.directory("initial-" + key(value)) / "initial-inputs.json", inputs)
    authorized = _authority(envelope["runtime_authority_ref"], path, ref["stored_path"], inputs_path=ref["stored_path"])
    return authorized["harness_binding"]


def inspect_origin(repository, platform, session):
    """The protected issuer, never caller state, identifies the initialized runtime."""
    from .runtime_pin_transaction import publication_lock
    from .runtime_successor import distribution

    distribution(platform)
    value = subject(repository, platform, session)
    with publication_lock(value, readonly=True) as lock:
        transaction = read(lock.path)
        validate(transaction, value)
        if transaction["status"] != "ACTIVE":
            raise ValueError("runtime origin requires active protected initialization")
        return {"storage": str(storage(readonly=True)), "transaction": transaction,
                "source": str(runtime_directory(transaction) / (session + ".json"))}
