"""Transactional semantic ingestion and read-only dashboard projections."""

import json
import time
from pathlib import Path

from harness.db import transaction, utcnow
from harness.execution_contract import canonical, integer, validate_event
from harness.execution_models import ExecutionConflict
from harness.execution_store import bind_al_run, entities, entity, get_execution, put_entity


def _known(conn, eid, family, identity):
    row = entity(conn, eid, family, identity)
    if row is None:
        raise ExecutionConflict("event references unknown " + family + ": " + identity)
    return row


def _project(conn, execution, event):
    eid, kind, payload = execution["id"], event["kind"], event["payload"]
    if kind == "delivery.correction.acknowledged":
        from harness.delivery_projection import acknowledge_correction

        acknowledge_correction(conn, execution, payload)
    elif kind == "run.started":
        conn.execute("UPDATE executions SET status='Running' WHERE id=?", (eid,))
    elif kind == "run.ended":
        if payload["status"] != "RUNNING":
            conn.execute(
                "UPDATE executions SET status='Interrupted' WHERE id=? AND status!='Completed'",
                (eid,),
            )
    elif kind == "issue.updated":
        old = entity(conn, eid, "issue", payload["id"])
        if payload["revision"] != (old["revision"] + 1 if old else 1):
            raise ExecutionConflict("stale issue revision")
        for task in payload["task_ids"]:
            _known(conn, eid, "task", task)
        if old and (
            old["state"] == "Resolved"
            or any(
                old[k] != payload[k]
                for k in (
                    "cause",
                    "task_ids",
                    "paths",
                    "capability_or_check_ref",
                    "observed_binding",
                )
            )
        ):
            raise ExecutionConflict("issue scope or terminal observation changed")
        put_entity(conn, eid, "issue", payload["id"], payload)
    elif kind in {"output.authored", "output.invalidated"}:
        task = _known(conn, eid, "task", payload["task_id"])
        if kind == "output.authored":
            if payload["task_revision"] != task["revision"]:
                raise ExecutionConflict("stale authored task revision")
            task["authored_output"] = payload
        else:
            task.pop("authored_output", None)
        put_entity(conn, eid, "task", task["id"], task)
    elif kind.startswith(("agent.", "task.")):
        family = kind.split(".")[0]
        old = entity(conn, eid, family, payload["id"])
        if kind.endswith("registered"):
            if old:
                raise ExecutionConflict("entity identity already registered")
            if family == "task":
                if payload["id"] in payload["dependencies"]:
                    raise ExecutionConflict("cyclic task dependency")
                for dep in payload["dependencies"]:
                    _known(conn, eid, "task", dep)
                    conn.execute(
                        (
                            "INSERT INTO "
                            "execution_edges(execution_id,family,source,target,data,epoch_i"
                            "d) VALUES (?,?,?,?,?,?)"
                        ),
                        (eid, "dependency", payload["id"], dep, "{}", execution["epoch_id"]),
                    )
            elif payload.get("task"):
                _known(conn, eid, "task", payload["task"])
        elif old is None:
            raise ExecutionConflict("update before entity registration")
        elif old["status"] in {"Completed", "Failed"} and payload["status"] != old["status"]:
            if family != "task" or payload.get("revision", 0) <= old.get("revision", 0):
                raise ExecutionConflict("terminal entity cannot reopen without amendment")
        put_entity(conn, eid, family, payload["id"], (old or {}) | payload)
    elif kind == "blocker.raised":
        if entity(conn, eid, "blocker", payload["id"]):
            raise ExecutionConflict("blocker identity already registered")
        for task in payload["tasks"]:
            _known(conn, eid, "task", task)
        put_entity(conn, eid, "blocker", payload["id"], payload | {"state": "Open"})
    elif kind == "blocker.resolved":
        old = _known(conn, eid, "blocker", payload["id"])
        delivery = conn.execute(
            "SELECT id FROM execution_decisions WHERE id=? AND execution_id=? AND blocker_id=?",
            (payload["delivery_id"], eid, payload["id"]),
        ).fetchone()
        if delivery is None:
            raise ExecutionConflict("blocker resolution lacks its execution-bound delivery")
        conn.execute(
            "UPDATE execution_decisions SET state='Resolved',updated_at=? "
            "WHERE id=? AND state='RevalidationRequired'",
            (utcnow(), payload["delivery_id"]),
        )
        put_entity(conn, eid, "blocker", payload["id"], old | payload | {"state": "Resolved"})
    elif kind == "decision.acknowledged":
        decision = conn.execute(
            "SELECT * FROM execution_decisions WHERE id=? AND execution_id=?",
            (payload["delivery_id"], eid),
        ).fetchone()
        if (
            decision is None
            or decision["blocker_id"] != payload["blocker_id"]
            or decision["delivery"] is None
        ):
            raise ExecutionConflict("foreign decision acknowledgement")
        from harness.execution_contract import digest

        if digest(json.loads(decision["delivery"])) != payload["decision_hash"]:
            raise ExecutionConflict("decision acknowledgement content mismatch")
        if payload["state"] not in {"Rejected", "Resolved", "RevalidationRequired"}:
            raise ExecutionConflict("invalid decision acknowledgement state")
        conn.execute(
            "UPDATE execution_decisions SET state=?,error=NULL,updated_at=? WHERE id=?",
            (payload["state"], utcnow(), decision["id"]),
        )
        old = _known(conn, eid, "blocker", payload["blocker_id"])
        put_entity(
            conn,
            eid,
            "blocker",
            old["id"],
            old | {"decision_state": payload["state"], "delivery_id": decision["id"]},
        )
    elif kind == "handoff.created":
        for key in ("from", "to"):
            _known(conn, eid, "task", payload[key])
        conn.execute(
            (
                "INSERT OR REPLACE INTO "
                "execution_edges(execution_id,family,source,target,data,epoch_id) VALUES "
                "(?,?,?,?,?,?)"
            ),
            (
                eid,
                "handoff",
                payload["from"],
                payload["to"],
                canonical(payload),
                execution["epoch_id"],
            ),
        )
    elif kind in {"dependency.changed", "ownership.changed"}:
        task = _known(conn, eid, "task", payload["task"])
        key = "dependencies" if kind == "dependency.changed" else "files"
        if not set(task[key]) <= set(payload[key]):
            raise ExecutionConflict("graph update removed dependency/ownership")
        if key == "dependencies":
            graph = {row["id"]: row["dependencies"] for row in entities(conn, eid, "task")}
            graph[task["id"]] = payload[key]
            for dep in payload[key]:
                _known(conn, eid, "task", dep)

            def visit(node, stack):
                if node in stack:
                    raise ExecutionConflict("cyclic event dependency graph")
                for dep in graph[node]:
                    visit(dep, stack | {node})

            for node in graph:
                visit(node, set())
            conn.execute(
                "DELETE FROM execution_edges "
                "WHERE execution_id=? AND family='dependency' AND source=? AND epoch_id=?",
                (eid, task["id"], execution["epoch_id"]),
            )
            for dep in payload[key]:
                conn.execute(
                    (
                        "INSERT INTO "
                        "execution_edges(execution_id,family,source,target,data,epoch_id) "
                        "VALUES (?,?,?,?,?,?)"
                    ),
                    (eid, "dependency", task["id"], dep, "{}", execution["epoch_id"]),
                )
        task[key] = payload[key]
        put_entity(conn, eid, "task", task["id"], task)
    elif kind == "phase.changed":
        put_entity(conn, eid, "run", "phase", payload)
    elif kind.startswith("test."):
        put_entity(conn, eid, "test", payload["id"], payload)
    elif kind == "implementation.result":
        if (
            payload["status"] != "APPROVED"
            or not isinstance(payload["approval"], dict)
            or not {"review", "task_hash", "fresh", "comparison", "evidence", "fingerprint"}
            <= payload["approval"].keys()
        ):
            raise ExecutionConflict("implementation result lacks guarded AL receipt")
        conn.execute(
            "UPDATE executions SET status='Completed',result=?,error=NULL WHERE id=?",
            (canonical(payload), eid),
        )
    elif kind == "delivery.updated":
        put_entity(conn, eid, "delivery", "publication", payload)
    elif kind.startswith("amendment."):
        put_entity(conn, eid, "amendment", payload["id"], payload)


def ingest_event(conn, execution_id, event):
    """No caller transaction: record, projection and cursor are one commit."""
    current = get_execution(conn, execution_id)
    if (
        current["epoch_id"] != "legacy-0"
        and isinstance(event, dict)
        and event.get("binding", {}).get("al_run_id") != current["al_run_id"]
    ):
        raise ExecutionConflict(
            "stale_epoch: historical callback cannot mutate successor projection"
        )
    try:
        validate_event(event)
        with transaction(conn):
            execution = get_execution(conn, execution_id)
            binding = event["binding"]
            expected = execution["binding"] | {
                "handoff_hash": execution["handoff_hash"],
                "al_session_id": execution["al_session_id"],
                "al_run_id": execution["al_run_id"] or binding["al_run_id"],
            }
            if binding != expected:
                raise ExecutionConflict("foreign execution/approval binding")
            if execution["al_run_id"] is None:
                if event["sequence"] != 1 or event["kind"] != "run.started":
                    raise ExecutionConflict("AL binding requires first run-start event")
                # Only a reservation-owned worker imports a verified exact source.
                if execution["worker_token"] is None:
                    raise ExecutionConflict("no worker has claimed the expected AL source")
                bind_al_run(conn, execution_id, binding["al_session_id"], binding["al_run_id"])
            raw = canonical(event)
            old = conn.execute(
                "SELECT record FROM execution_events "
                "WHERE epoch_id=? AND (event_id=? OR (execution_id=? AND sequence=?))",
                (execution["epoch_id"], event["event_id"], execution_id, event["sequence"]),
            ).fetchall()
            if old:
                if len(old) == 1 and old[0]["record"] == raw:
                    return False
                raise ExecutionConflict("conflicting duplicate execution event")
            if (
                execution["status"] in {"Completed", "Failed"}
                and event["kind"] != "delivery.correction.acknowledged"
            ):
                raise ExecutionConflict("terminal execution cannot accept new work events")
            if event["sequence"] != execution["cursor"] + 1:
                raise ExecutionConflict("execution event sequence gap")
            conn.execute(
                (
                    "INSERT INTO "
                    "execution_events(event_id,execution_id,sequence,kind,record,epoch_id) "
                    "VALUES (?,?,?,?,?,?)"
                ),
                (
                    event["event_id"],
                    execution_id,
                    event["sequence"],
                    event["kind"],
                    raw,
                    execution["epoch_id"],
                ),
            )
            _project(conn, execution, event)
            conn.execute(
                "UPDATE executions SET cursor=?,source_contact=?,updated_at=? WHERE id=?",
                (event["sequence"], time.time(), utcnow(), execution_id),
            )
        return True
    except (ValueError, KeyError, TypeError) as error:
        # Diagnostics are committed separately and never advance a rejected cursor.
        from harness.execution_logs import append_log

        append_log(conn, execution_id, "protocol", str(error))
        conn.execute(
            "UPDATE executions SET error=? WHERE id=?", ("AL protocol: " + str(error), execution_id)
        )
        raise


def presentation(value):
    from harness.execution_logs import redact

    if isinstance(value, dict):
        return {key: presentation(item) for key, item in value.items()}
    if isinstance(value, list):
        return [presentation(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def event_page(conn, execution_id, after=0, limit=100):
    integer(after, "cursor")
    integer(limit, "limit", 1)
    if limit > 200:
        raise ValueError("limit exceeds 200")
    execution = get_execution(conn, execution_id)
    rows = conn.execute(
        "SELECT record FROM execution_events "
        "WHERE execution_id=? AND epoch_id=? AND sequence>? ORDER BY sequence LIMIT ?",
        (execution_id, execution["epoch_id"], after, limit),
    ).fetchall()
    events, size = [], 0
    for row in rows:
        if events and size + len(row["record"]) > 524288:
            break
        events.append(presentation(json.loads(row["record"])))
        size += len(row["record"])
    cursor = events[-1]["sequence"] if events else after
    return {
        "events": events,
        "next_after": cursor,
        "high_watermark": execution["cursor"],
        "has_more": cursor < execution["cursor"],
    }


def execution_view(conn, execution_id):
    saved = get_execution(conn, execution_id)
    if saved["error"]:
        from harness.execution_logs import redact

        saved["error"] = redact(saved["error"])
    tasks = entities(conn, execution_id, "task")
    completed = sum(task["status"] == "Completed" for task in tasks)
    blockers = entities(conn, execution_id, "blocker")
    decisions = [
        dict(r)
        for r in conn.execute(
            "SELECT id,blocker_id,blocker_revision,state,request,error,registry_id,delivery "
            "FROM execution_decisions WHERE execution_id=? AND epoch_id=? ORDER BY rowid",
            (execution_id, saved["epoch_id"]),
        )
    ]
    for decision in decisions:
        decision["request"] = json.loads(decision["request"])
        state = decision["state"]
        delivered = decision.pop("delivery") is not None
        undelivered_retirement = state == "Rejected" and not delivered
        decision["saved"] = True
        decision["retryable"] = state in {"Intent", "Ready", "Delivered"}
        decision["delivery_status"] = (
            ("failed" if decision["error"] else "pending")
            if state in {"Intent", "Ready"}
            else ("awaiting_acknowledgment" if state == "Delivered" else "acknowledged")
        )
        decision["outcome_notice"] = (
            "Recorded AL decision outcome; this does not verify environment repair."
        )
        if undelivered_retirement:
            decision["delivery_status"] = "not_delivered"
            decision["retryable"] = False
            decision["outcome_notice"] = (
                "Invalid saved request retired before delivery; no AL acknowledgment was recorded."
            )

    logs = [
        dict(r)
        for r in conn.execute(
            "SELECT id,channel,created_at FROM execution_logs "
            "WHERE execution_id=? AND epoch_id=? ORDER BY rowid DESC LIMIT 50",
            (execution_id, saved["epoch_id"]),
        )
    ]
    contact = saved["source_contact"]
    tasks = presentation(tasks)
    blockers = presentation(blockers)
    decisions = presentation(decisions)
    agents = presentation(entities(conn, execution_id, "agent"))
    task_map = {task["id"]: task for task in tasks}
    for agent in agents:
        task = task_map.get(agent.get("task"), {})
        agent["dependencies"] = task.get("dependencies", [])
        agent["blockers"] = [
            b["id"]
            for b in blockers
            if agent.get("task") in b.get("affected", b["tasks"]) and b["state"] != "Resolved"
        ]
        agent["duration"] = max(
            0, (agent.get("ended_at") or time.time()) - agent.get("started_at", time.time())
        )
        agent.setdefault("latest_output", None)
        agent.setdefault("activity", "")
        agent.setdefault("progress", None)
    from harness.execution_artifacts import output_reports

    reports = output_reports(Path(saved["handoff_path"]).parent / "operations/drafts")
    from harness.dashboard.action_readiness import last_readiness

    initialization = conn.execute(
        (
            "SELECT identity FROM execution_initializations WHERE execution_id=? AND "
            "epoch_id=? AND state='Active'"
        ),
        (execution_id, saved["epoch_id"]),
    ).fetchone()
    pin_identity = None
    if initialization and initialization["identity"]:
        pin_root = json.loads(initialization["identity"]).get("pin_root")
        if isinstance(pin_root, str):
            pin_identity = Path(pin_root).name

    from harness.execution_epochs import history_epochs
    from harness.execution_work_notice import authored_work_notice

    recorded_work = (
        None if saved["status"] == "Completed" else authored_work_notice(conn, execution_id)
    )

    issues = presentation(entities(conn, execution_id, "issue"))
    decisions_pending = [
        b["id"]
        for b in blockers
        if (
            b.get("kind") in {"specification", "developer_engineering_decision"}
            # The original protocol defined untyped blockers as design choices.
            # Preserve its recorded forms only in the legacy epoch; successor
            # runtimes require explicit kinds and keep tooling in issue records.
            or saved["epoch_id"] == "legacy-0"
            and "kind" not in b
        )
        and b["state"] != "Resolved"
    ]
    issue_refs = [row["id"] for row in issues if row["state"] != "Resolved"]
    implementation_status = (
        "developer_decision"
        if decisions_pending
        else "technically_unavailable"
        if saved["status"] in {"Unavailable", "RecoveryRequired", "Failed"}
        else "continuing"
        if saved["status"] in {"Running", "Launching", "WorkerOwned"}
        else "ready"
    )
    return {
        k: saved[k]
        for k in (
            "id",
            "epoch_id",
            "authority_generation",
            "task_id",
            "approval_id",
            "host",
            "status",
            "cursor",
            "al_session_id",
            "al_run_id",
            "native_id",
            "heartbeat",
            "source_contact",
            "error",
            "result",
            "created_at",
            "updated_at",
        )
    } | {
        "authored_work": recorded_work,
        "implementation": {"status": implementation_status, "issue_refs": issue_refs},
        "verification": {
            "status": "complete"
            if saved["status"] == "Completed" and saved.get("result")
            else "incomplete",
            "obligation_refs": issue_refs,
        },
        "developer_decisions": decisions_pending,
        "available_history_epochs": history_epochs(conn, execution_id),
        "readiness": last_readiness(execution_id, pin_identity),
        "status_explanation": "The agent stopped without a verified implementation result."
        if saved["status"] == "Interrupted"
        and saved["error"]
        and "no guarded AL implementation result" in saved["error"]
        else None,
        "binding": saved["binding"],
        "reports": reports,
        "agents": agents,
        "tasks": tasks,
        "blockers": blockers,
        "operational_issues": presentation(entities(conn, execution_id, "issue")),
        "decisions": decisions,
        "edges": [
            dict(r)
            for r in conn.execute(
                "SELECT family,source,target FROM execution_edges WHERE execution_id=?",
                (execution_id,),
            )
        ],
        "progress": {"completed": completed, "total": len(tasks), "unit": "tasks"}
        if tasks
        else None,
        "phase": entity(conn, execution_id, "run", "phase"),
        "feed": event_page(conn, execution_id, max(0, saved["cursor"] - 50), 50)["events"],
        "logs": logs,
        "source_status": "Unknown"
        if contact is None
        else ("Current" if time.time() - contact < 15 else "Stale"),
    }
