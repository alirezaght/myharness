"""Retain accepted ticket, criterion and invariant identities in execution handoffs."""

import hashlib
import re


def registry(controller, task_id, body):
    ws = controller.workspace(task_id)
    accepted = ws.latest_checkpoint("context_accepted")
    if accepted and accepted.payload.get("requirements"):
        value = accepted.payload["requirements"]
        return {
            "ticket": value.get("ticket", {"present": False, "reason": "not supplied"}),
            "criteria": [row["id"] for row in value["criteria"]],
            "invariants": [row["id"] for row in value.get("invariants", [])],
            "requirement_sources": value,
        }
    task = controller.get_task(task_id)
    criteria_section = body.split("## Acceptance criteria\n", 1)[-1].split("\n## ", 1)[0]
    criteria = list(dict.fromkeys(re.findall(r"(?<![\w-])AC[-_]?\d+(?![\w-])", criteria_section)))
    invariants = list(
        dict.fromkeys(re.findall(r"(?m)^\s*(?:[-*]\s*)?((?:INV|I)[-_]?\d+)\s*[:.)-]", body))
    )
    tickets = list(dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", task.request)))
    ticket = {"present": False, "reason": "No supplied ticket identity in the accepted task"}
    if len(tickets) == 1:
        ticket = {
            "present": True,
            "id": tickets[0],
            "content": task.request,
            "source": "original user request; ticket content beyond the supplied request "
            "is not independently verified",
        }
    return {
        "ticket": ticket,
        "criteria": criteria or ["AC-01"],
        "invariants": invariants,
        "requirement_sources": {
            "kind": "accepted-spec-registry-v1",
            "accepted_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "criteria_source": "Acceptance criteria section",
            "invariants_source": "Explicit invariant declarations",
            "legacy_criterion": not bool(criteria),
        },
    }
