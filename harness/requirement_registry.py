"""Retain accepted ticket, criterion and invariant identities in execution handoffs."""

import hashlib
import re


def registry(controller, task_id, body):
    from harness.authoring_contract import digest
    from harness.interview_coverage import current_coverage
    from harness.investigation_store import InvestigationStore, review_basis

    investigation = InvestigationStore(controller, None, task_id)
    if investigation.binding():
        coverage = current_coverage(controller, task_id)
        if not coverage.accepted or review_basis(controller, task_id)["body"] != digest(body):
            raise ValueError("current exact source coverage must be reviewed before approval")
        bundle = investigation.read_active()
        request = bundle["content"]["request"]
        ticket = (
            {
                "present": False,
                "reason": "Accepted repository-only evidence; no Jira ticket supplied",
                "source": bundle["id"],
            }
            if request.get("kind") == "repository"
            else {
                "present": True,
                "id": request["issue"],
                "content": next(
                    source["content"]
                    for source in bundle["content"]["sources"]
                    if source["kind"] == "ticket"
                ),
                "source": bundle["id"],
            }
        )
        return {
            "ticket": ticket,
            "criteria": [r["key"] for r in coverage.rows],
            "invariants": [],
            "requirement_sources": {
                "kind": "accepted-investigation-v2",
                "bundle": bundle["id"],
                "acceptance": coverage.acceptance,
                "coverage": coverage.to_dict(),
                "accepted_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            },
        }
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
