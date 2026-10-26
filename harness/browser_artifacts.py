"""Private immutable browser evidence and bound recovery receipts."""

import os
from pathlib import Path

from harness.execution_contract import canonical, digest, loads
from harness.execution_handoff import atomic_file, read_handoff
from harness.execution_store import get_execution


def runtime_root(conn, execution_id, *, create=True):
    execution = get_execution(conn, execution_id)
    envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
    path = Path(execution["handoff_path"]).parent / "browser"
    repository = Path(envelope["repository"]).resolve()
    # Existing ancestors must be genuine directories, never links.
    for ancestor in [path, *path.parents]:
        if ancestor.is_symlink():
            raise ValueError("browser runtime symlink refused")
    if path.resolve().is_relative_to(repository):
        raise ValueError("private browser runtime must be outside repository")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def run_root(conn, run):
    path = runtime_root(conn, run["execution_id"]) / run["id"]
    if path.is_symlink():
        raise ValueError("browser run symlink refused")
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def publish(root, name, value):
    path = root / name
    if path.is_symlink():
        raise ValueError("browser artifact symlink refused")
    atomic_file(path, canonical(value).encode())
    return digest(path.read_bytes())


def descriptor(root, name, raw, media_type):
    if Path(name).name != name or (root / name).is_symlink():
        raise ValueError("invalid artifact name")
    atomic_file(root / name, raw)
    return {"path": name, "sha256": digest(raw), "size": len(raw), "media_type": media_type}


def verify_evidence(root, result):
    for evidence in result.get("evidence", []):
        name = evidence["path"]
        path = root / name
        if (
            Path(name).name != name
            or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
        ):
            raise ValueError("unsafe browser evidence path")
        raw = path.read_bytes()
        if len(raw) != evidence["size"] or digest(raw) != evidence["sha256"]:
            raise ValueError("browser evidence changed")


def criterion_name(criterion_id):
    return "criterion-" + digest(criterion_id)[7:] + ".json"


def receipt_binding(run, saved):
    return {
        "run_id": run["id"],
        "approval_id": saved["id"],
        "plan_id": saved["plan_id"],
        "execution_id": run["execution_id"],
        "binding_hash": digest(saved["binding"]),
    }


def read_receipt(root, name, expected):
    path = root / name
    if path.is_symlink():
        raise ValueError("receipt symlink refused")
    value = loads(path.read_bytes(), max_bytes=4 * 1024 * 1024)
    if value.get("binding") != expected:
        raise ValueError("browser receipt binding mismatch")
    return value


def verify_run_artifacts(conn, run):
    from harness.browser_store import approval, get_plan

    saved = approval(conn, run["execution_id"], run["approval_id"])
    plan = get_plan(conn, run["execution_id"], saved["plan_id"])
    root = runtime_root(conn, run["execution_id"], create=False) / run["id"]
    if root.is_symlink():
        raise ValueError("runtime symlink refused")
    for result in run["results"]:
        criterion = next(c for c in plan["criteria"] if c["id"] == result["criterion_id"])
        receipt = read_receipt(root, criterion_name(criterion["id"]), receipt_binding(run, saved))
        if receipt.get("criterion_hash") != digest(criterion) or receipt.get("result") != result:
            raise ValueError("criterion receipt changed")
        verify_evidence(root, result)
        if (
            result["status"] == "passed"
            and plan["evidence"]["screenshot"] == "each_criterion"
            and not any(e["media_type"] == "image/png" for e in result["evidence"])
        ):
            raise ValueError("required screenshot absent")

    selected = {c["id"] for c in plan["criteria"] if c["classification"] == "selected"}
    if run["state"] in {"Completed", "Failed", "Blocked"}:
        if {r["criterion_id"] for r in run["results"]} != selected:
            raise ValueError("browser result inventory incomplete")
        completion = read_receipt(root, "completion.json", receipt_binding(run, saved))
        if completion.get("state") != run["state"]:
            raise ValueError("completion receipt state changed")
        expected = {
            criterion_name(c): digest((root / criterion_name(c)).read_bytes()) for c in selected
        }
        if completion.get("receipts") != expected:
            raise ValueError("completion receipts changed")
        if run["state"] == "Completed" and any(r["status"] != "passed" for r in run["results"]):
            raise ValueError("completed run has nonpassing criteria")
