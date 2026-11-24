"""Provenance of each AL check result in Harness reports (#31): ran now, or reused.

AL's `test.result` events carry `provenance` only for a result reused from the action
cache (#26): the run and ledger entry that executed it, and the input tree both share. A
result without it executed in this run."""


def provenance(record, run_id=None):
    value = record.get("provenance") if isinstance(record, dict) else None
    if not isinstance(value, dict) or value.get("mode") != "reused":
        if isinstance(record, dict) and record.get("status") == "RUNNING":
            return {"mode": "running", "text": "running now"}
        return {"mode": "ran", "text": "ran now" + (" in run " + run_id if run_id else "")}
    origin = value.get("origin") if isinstance(value.get("origin"), dict) else {}
    run, entry, tree = origin.get("run"), origin.get("entry"), value.get("tree")
    text = "reused from run " + str(run) + (" (" + str(entry) + ")" if entry else "")
    if isinstance(tree, str) and tree:
        text += " on the same input tree " + tree[:12]
    return {"mode": "reused", "run": run, "entry": entry, "tree": tree, "text": text}


def annotate(records, run_id=None):
    """Copies of the test records, each with its provenance."""
    return [dict(r, provenance=provenance(r, run_id)) for r in records]


def summary(records):
    rows = [provenance(r) for r in records]
    return {
        "ran": sum(r["mode"] == "ran" for r in rows),
        "reused": sum(r["mode"] == "reused" for r in rows),
        "running": sum(r["mode"] == "running" for r in rows),
    }
