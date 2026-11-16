"""Read and reconcile immutable protected preparation publications."""

from . import runtime_successor_store as Store
from .registry import write_json
from .runtime_pin_transaction import publication_lock, read
from .runtime_successor_channel import fingerprint
from .validator_origin import capture, digest


def inspect_successor(identifier):
    from .runtime_successor import _request

    directory = Store.directory(identifier)
    path = directory / "receipt.json"
    if not path.exists():
        return {"state": "PREPARING", "request_id": identifier, "product_authority": False}
    value = read(path)
    plan = read(directory / "plan.json")
    request = _request(read(directory / "request.json"), identifier)
    successor = value["successor_transaction"]
    if (value.get("kind") != "prepared-successor-v1"
            or value.get("state") != "PREPARED"
            or value["request_id"] != identifier
            or value["request_sha256"] != fingerprint(request)
            or plan["request"] != request
            or digest(capture(directory / "plan.json")[0]) != value["preparation_sha256"]
            or plan["successor_transaction"] != dict(successor, initialization=None, status="RESERVED")
            or value["successor_run_id"] != successor["run_id"]
            or value.get("product_authority") is not False
            or value.get("native_resumed") is not False):
        raise ValueError("protected preparation receipt identity differs")
    journal = read(directory / "journal.json")
    reference = {"stored_path": str(path), "sha256": digest(capture(path)[0])}
    if journal.get("request_ref") != {
            "stored_path": str(directory / "request.json"),
            "sha256": digest(capture(directory / "request.json")[0])}:
        raise ValueError("protected preparation request journal differs")
    if journal.get("receipt_ref") not in (None, reference):
        raise ValueError("protected preparation journal receipt differs")
    for key, filename in (("lineage_ref", "lineage.json"),
                          ("successor_state_ref", "successor-state.json"),
                          ("predecessor_binding_ref", "predecessor-binding.json")):
        if value[key]["stored_path"] != str(directory / filename):
            raise ValueError("protected preparation artifact location differs")
        Store.read_reference(value[key])
    if journal.get("receipt_ref") is None:
        # Receipt publication precedes journal acknowledgment. Reconcile its exact
        # reserved subject; this never invokes initialization or grants authority.
        with publication_lock(value["successor_subject"]) as capability:
            if read(capability.path) != successor:
                raise ValueError("protected prepared successor binding differs")
            journal.update(state="PREPARED", receipt_ref=reference)
            write_json(directory / "journal.json", journal)
    return {
        "state": "PREPARED",
        "request_id": identifier,
        "receipt": value,
        "receipt_ref": reference,
        "product_authority": False,
        "native_resumed": False,
    }

