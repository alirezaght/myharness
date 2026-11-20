"""Immutable source projection for proposal editing; no inferred requirement links."""

import hashlib
import json
import re

from harness.diagrams import DiagramService
from harness.interview import InterviewEngine


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_snapshot(controller, task, store, execution):
    ws = controller.workspace(task.id)
    summary = InterviewEngine(controller, task.id).summary().to_dict()
    requirements = [
        {
            "key": "request",
            "label": "Original request",
            "text": task.request,
            "kind": "request",
            "status": "stated",
            "provenance": "original user request",
        }
    ]
    for index, row in enumerate(summary["requirements"]):
        requirements.append(
            {
                "key": f"Q{row['question_id']}" if row["question_id"] else f"interview:{index}",
                "label": row["topic"],
                "text": row["statement"],
                "kind": "requirement",
                "status": "stated",
                "provenance": row["provenance"],
            }
        )
    checkpoint = ws.latest_checkpoint("context_accepted")
    context = checkpoint.payload.get("requirements", {}) if checkpoint else {}
    for kind in ("criteria", "invariants"):
        for row in context.get(kind, []):
            requirements.append(
                {
                    "key": row["id"],
                    "label": row["id"],
                    "text": row.get("text", row.get("statement", canonical(row))),
                    "kind": kind,
                    "status": "accepted",
                    "quote": row.get("quote"),
                    "provenance": row.get("source", "accepted context"),
                }
            )
    unavailable = None
    spec = None
    try:
        record = store.current() or store.draft()
        if record is None:
            records = store.records()
            record = records[-1] if records else None
        if record:
            spec = {
                "body": record.body,
                "version": record.version,
                "status": str(record.status),
                "hash": digest(record.body),
            }
    except ValueError as error:
        unavailable = str(error)
    if spec:
        for index, section in enumerate(re.split(r"(?m)(?=^## )", spec["body"])):
            if not section.strip():
                continue
            heading = section.splitlines()[0].lstrip("# ")
            requirements.append(
                {
                    "key": f"spec-section:{index}",
                    "label": heading,
                    "text": section,
                    "kind": "spec_section",
                    "status": spec["status"],
                    "provenance": f"Approved specification v{spec['version']}"
                    if spec["version"]
                    else "Draft specification",
                }
            )
    plan = [
        {
            "key": entry.ref,
            "label": entry.section.heading,
            "text": entry.text,
            "kind": "design",
            "status": str(entry.status),
            "provenance": str(entry.provenance or entry.origin),
            "effective": entry.effective,
        }
        for entry in ws.design_entries()
    ]
    known = {row["key"] for row in requirements}
    for row in requirements + plan:
        row["hash"] = digest(row["text"])
        row["links"] = sorted(
            set(re.findall(r"(?<![\w-])(?:AC[-_]?\d+|INV[-_]?\d+|Q\d+)(?![\w-])", row["text"]))
            & known
        )
    model = DiagramService(controller, task.id).model()
    graph = {"nodes": [], "edges": []}
    nodes = model.data["components"] + model.data["nodes"]
    for machine in model.data["machines"]:
        nodes += machine["states"]
    for index, node in enumerate(nodes):
        graph["nodes"].append(
            {
                "id": node["id"],
                "label": node["name"],
                "sources": node["sources"],
                "x": 60 + index % 3 * 240,
                "y": 60 + index // 3 * 150,
            }
        )
    edges = model.data["connections"] + model.data["messages"] + model.data["flows"]
    for machine in model.data["machines"]:
        edges += machine["transitions"]
    for edge in edges:
        graph["edges"].append(
            {
                "id": edge["id"],
                "from": edge["from"],
                "to": edge["to"],
                "label": edge.get("text", ", ".join(edge.get("labels", edge.get("events", [])))),
                "sources": edge["sources"],
            }
        )
    for edge in model.data["deployments"]:
        graph["edges"].append(
            {
                "id": edge["id"],
                "from": edge["component"],
                "to": edge["node"],
                "label": "deployed",
                "sources": edge["sources"],
            }
        )
    from harness.investigation_store import InvestigationStore
    from harness.logical_diagram import project_graph

    source_store = InvestigationStore(controller, None, task.id)
    bundle = source_store.read_active()
    source_evidence = {
        "bundle": bundle["id"] if bundle else None,
        "coverage": InterviewEngine(controller, task.id).coverage().to_dict(),
        "sources": bundle["content"]["sources"] if bundle else [],
    }
    logical = project_graph(controller, task, graph, requirements, execution)
    graph = logical["primary"]
    return {
        "logical": logical,
        "investigation": source_evidence,
        "task": task.id,
        "requirements": requirements,
        "plan": plan,
        "graph": graph,
        "spec": spec,
        "accepted_context": context,
        "unavailable": unavailable,
        "unresolved": summary["checklist"]["unresolved"],
        "diagram_hash": model.content_hash,
        "diagram_issues": [issue.problem for issue in model.issues],
        "execution": {
            key: execution.get(key)
            for key in ("id", "al_session_id", "al_run_id", "epoch_id", "authority_generation")
        }
        if execution
        else None,
    }


def workspace_section(controller, task, store, execution):
    from harness.workspace_proposals import WorkspaceProposalService

    snapshot = source_snapshot(controller, task, store, execution)
    base = digest(snapshot)
    proposals = WorkspaceProposalService(controller, task.id).list(base)
    return {
        "base": base,
        "source": snapshot,
        "proposals": proposals,
        "events": [
            {"kind": event.kind, "at": event.created_at, "payload": event.payload}
            for event in controller.workspace(task.id).events()[-100:]
        ],
    }
