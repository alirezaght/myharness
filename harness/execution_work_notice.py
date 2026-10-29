"""Recorded authored source work is useful progress, never verified completion."""

from harness.execution_contract import validate_authored
from harness.execution_store import entities


def authored_work_notice(conn, execution_id):
    receipts = []
    paths = set()
    tasks = []
    for task in entities(conn, execution_id, "task"):
        receipt = task.get("authored_output")
        if not receipt:
            continue
        validate_authored(receipt)
        source = [row for row in receipt["output_refs_and_hashes"] if row["kind"] == "source"]
        if not source or not receipt["writer_ids"] or receipt["task_revision"] != task["revision"]:
            continue
        receipts.append(receipt["receipt_hash"])
        tasks.append(task["id"])
        paths.update(row["path"] for row in source)
    if not receipts:
        return None
    return {
        "kind": "authored_work_recorded_verification_pending",
        "task_ids": sorted(tasks),
        "receipt_refs": sorted(receipts),
        "source_file_count": len(paths),
        "message": (
            "Source work recorded; verification is pending. "
            "This does not establish full implementation or completion."
        ),
    }
