"""Durable authoring claims and atomic result fences; no provider calls in transactions."""

import json

from harness.authoring_contract import AuthoringConflict, canonical, validate_context
from harness.authoring_store import identity, source_state
from harness.db import transaction, utcnow


def job_row(service, ident):
    row = service.conn.execute(
        "SELECT * FROM authoring_jobs WHERE id=? AND task_id=? AND project=?",
        (ident, service.task_id, service.project),
    ).fetchone()
    if row is None:
        raise LookupError("unknown task job")
    return dict(row)


def immutable_input(service, *, next_generation=False):
    snapshot = service.snapshot()
    from harness.authoring_context import projection

    effective = projection(service)
    base = service.store.base()
    if next_generation:
        base["generation"] += 1
    from harness.logical_diagram import source_refs

    return {
        "schema_version": 1,
        "base": base,
        "body": snapshot["working"]["body"],
        "graph": snapshot["working"]["graph"],
        "contributions": effective["contributions"],
        "revision_targets": effective["revision_targets"],
        "source": source_state(service.controller, service.task_id),
        "questions": snapshot["questions"],
        "recommendations": snapshot["recommendations"],
        "historical": snapshot["historical"],
        "effective_lineage": snapshot["effective_lineage"],
        "planning": snapshot["planning"],
        "valid_refs": sorted(source_refs(service.controller, service.task_id)),
        "request": service.controller.get_task(service.task_id).request,
    }


def queue_job(service, kind, host, *, next_generation=False):
    service.store.writing()
    ident = identity()
    inputs = immutable_input(service, next_generation=next_generation)
    from harness.authoring_policy import availability

    inputs["native_profile"] = availability(host)
    validate_context(inputs)
    service.conn.execute(
        "INSERT INTO authoring_jobs(id,task_id,project,kind,host,input_json,state,created_at)"
        " VALUES(?,?,?,?,?,?,'queued',?)",
        (ident, service.task_id, service.project, kind, host, canonical(inputs), utcnow()),
    )
    service.conn.execute(
        "INSERT INTO authoring_attempts(job_id,attempt,claim,owner,state,settlement,input_jso"
        "n) VALUES(?,1,0,NULL,'queued','settled',?)",
        (ident, canonical(inputs)),
    )
    return ident


def claim_next(conn, owner, project=None):
    with transaction(conn):
        row = conn.execute(
            "SELECT * FROM authoring_jobs WHERE state='queued'"
            + (" AND project=?" if project else "")
            + " ORDER BY rowid LIMIT 1",
            (project,) if project else (),
        ).fetchone()
        if not row:
            return None
        claim = row["claim"] + 1
        conn.execute(
            "UPDATE authoring_jobs SET state='running',claim=?,owner=?,settlement='unknown' W"
            "HERE id=? AND state='queued'",
            (claim, owner, row["id"]),
        )
        conn.execute(
            "UPDATE authoring_attempts SET state='running',claim=?,owner=?,settlement='unknow"
            "n' WHERE job_id=? AND attempt=?",
            (claim, owner, row["id"], row["attempt"]),
        )
        return dict(
            conn.execute("SELECT * FROM authoring_jobs WHERE id=?", (row["id"],)).fetchone()
        )


def matches(row, claim):
    return (
        row["state"] == "running"
        and not row["cancelled"]
        and all(row[k] == claim[k] for k in ("attempt", "claim", "owner"))
    )


def settle(service, claim, state, error=None, *, settlement="settled"):
    row = job_row(service, claim["id"])
    if row["attempt"] != claim["attempt"] or row["claim"] != claim["claim"]:
        return
    service.conn.execute(
        "UPDATE authoring_jobs SET state=?,error=?,settlement=? WHERE id=?",
        (state, error, settlement, row["id"]),
    )
    service.conn.execute(
        "UPDATE authoring_attempts SET state=?,settlement=?,error=? WHERE job_id=? AND attempt=?",
        (state, settlement, error, row["id"], row["attempt"]),
    )


def apply_result(service, claim, output, diagnostics=()):
    from harness.authoring_native import validate_output

    inputs = json.loads(claim["input_json"])
    output = validate_output(output, inputs)
    with transaction(service.conn):
        row = job_row(service, claim["id"])
        if row["applied_id"]:
            return row["applied_id"]
        current = matches(row, claim) and service.store.base() == inputs["base"]
        if not current:
            if (
                row["attempt"] != claim["attempt"]
                or row["claim"] != claim["claim"]
                or row["result_json"]
            ):
                return None
            # Cancellation/retry cannot be undone by a late writer.
            service.store.contribution(
                "stale_model",
                "model",
                {"job": row["id"], "text": output["assistant"], "applied": False},
            )
            service.conn.execute(
                "UPDATE authoring_jobs SET result_json=? WHERE id=?",
                (canonical({"stale_output_recorded": True}), row["id"]),
            )
            if matches(row, claim):
                settle(service, claim, "stale", "Input revision changed")
            return None
        if service.controller.get_task(service.task_id).phase == "interview":
            from harness.authoring import TransactionInterview

            engine = TransactionInterview(service.controller, service.task_id)
            questions = [
                engine.ask(q["topic"], q["text"], q["decision"]) for q in output["questions"]
            ]
            for recommendation in output["recommendations"]:
                engine.propose(
                    questions[recommendation["question"]].id,
                    recommendation["title"],
                    recommendation["recommendation"],
                    recommendation["rationale"],
                    recommendation["impact"],
                )
        contribution = service.store.contribution(
            "assistant",
            "model",
            {
                "job": row["id"],
                "text": output["assistant"],
                "questions": output["questions"],
                "recommendations": output["recommendations"],
                "planning": output["planning"],
                "content_complete": output["graph"] is not None,
            },
        )
        proposed = None
        if output["graph"] is not None:
            graph = output["graph"]
            graph_id = inputs["graph"]["id"] if graph.get("unchanged") else None
            proposed = service.store.add_revision(
                output["body"] if output["body"] is not None else inputs["body"],
                actor="model_proposal",
                parent=inputs["base"]["working"],
                graph=None if graph_id else graph["concrete"],
                graph_id=graph_id,
                obligations=["Developer must review generated specification and graph"],
            )
        # Structured questions/recommendations remain suggestions until explicit user adoption.
        service.conn.execute(
            "UPDATE authoring_jobs SET applied_id=?,result_json=? WHERE id=?",
            (
                contribution,
                canonical(
                    {"revision": proposed, "output": output, "diagnostics": list(diagnostics)}
                ),
                row["id"],
            ),
        )
        service.conn.execute(
            "UPDATE authoring_attempts SET result_json=? WHERE job_id=? AND attempt=?",
            (
                canonical(
                    {"revision": proposed, "output": output, "diagnostics": list(diagnostics)}
                ),
                row["id"],
                row["attempt"],
            ),
        )
        settle(service, claim, "succeeded")
        service.store.bump()
        return contribution


def cancel_job(service, ident):
    row = job_row(service, ident)
    if row["applied_id"] or row["state"] in {
        "succeeded",
        "failed",
        "stale",
        "interrupted",
        "cancelled",
    }:
        return {"job": ident, "state": row["state"]}
    service.conn.execute(
        "UPDATE authoring_jobs SET state='cancelled',cancelled=1 WHERE id=?", (ident,)
    )
    service.conn.execute(
        "UPDATE authoring_attempts SET state='cancelled' WHERE job_id=? AND attempt=?",
        (ident, row["attempt"]),
    )
    return {"job": ident, "state": "cancelled"}


def retry_job(service, ident, acknowledge_unknown):
    if type(acknowledge_unknown) is not bool:
        raise ValueError("acknowledge_unknown must be boolean")
    row = job_row(service, ident)
    if row["applied_id"]:
        return {"job": ident, "state": row["state"], "application": row["applied_id"]}
    if row["state"] not in {"failed", "stale", "cancelled", "interrupted"}:
        raise AuthoringConflict("job is not retryable")
    if row["attempt"] >= 3:
        raise ValueError("retry limit reached")
    if row["settlement"] == "unknown" and not acknowledge_unknown:
        raise AuthoringConflict("acknowledge unknown prior invocation before retry")
    attempt = row["attempt"] + 1
    inputs = immutable_input(service, next_generation=True)
    from harness.authoring_policy import availability

    inputs["native_profile"] = availability(row["host"])
    validate_context(inputs)
    service.conn.execute(
        "UPDATE authoring_jobs SET state='queued',attempt=?,claim=claim+1,cancelled=0,owner=N"
        "ULL,input_json=?,error=NULL,result_json=NULL,settlement='settled' WHERE id=?",
        (attempt, canonical(inputs), ident),
    )
    service.conn.execute(
        "INSERT INTO authoring_attempts(job_id,attempt,claim,owner,state,settlement,input_jso"
        "n) VALUES(?,?,?,NULL,'queued','settled',?)",
        (ident, attempt, row["claim"] + 1, canonical(inputs)),
    )
    return {"job": ident, "state": "queued", "attempt": attempt}


def interrupt_owned(conn, owner=None, *, project=None):
    with transaction(conn):
        predicate = "state='running'" + (" AND owner=?" if owner else "")
        params = [owner] if owner else []
        if project is not None:
            predicate += " AND project=?"
            params.append(project)
        rows = conn.execute(
            "SELECT id,attempt FROM authoring_jobs WHERE " + predicate, params
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE authoring_jobs SET state='interrupted',claim=claim+1,error='Worker in"
                "terrupted; prior outcome unknown',settlement='unknown' WHERE id=?",
                (row["id"],),
            )
            conn.execute(
                "UPDATE authoring_attempts SET state='interrupted',settlement='unknown' WHERE"
                " job_id=? AND attempt=?",
                (row["id"], row["attempt"]),
            )
