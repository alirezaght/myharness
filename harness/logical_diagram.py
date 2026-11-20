"""Truthful workspace projections, independent of approved diagram versions."""

import re

from harness.authoring_contract import digest, exact, text
from harness.phases import PHASE_ORDER


def validate_graph(graph, valid_refs):
    exact(graph, {"nodes", "edges"})
    if not isinstance(graph["nodes"], list) or not 2 <= len(graph["nodes"]) <= 80:
        raise ValueError("concrete graph requires 2 to 80 nodes")
    if not isinstance(graph["edges"], list) or not 1 <= len(graph["edges"]) <= 160:
        raise ValueError("concrete graph requires 1 to 160 relationships")
    ids = set()
    for entity, rows in (("node", graph["nodes"]), ("edge", graph["edges"])):
        for row in rows:
            keys = {"id", "label", "kind", "status", "sources", "assumption"}
            exact(row, keys | ({"from", "to"} if entity == "edge" else set()))
            if (
                not isinstance(row["id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["id"])
                or row["id"] in ids
            ):
                raise ValueError("graph IDs must be unique stable identifiers")
            ids.add(row["id"])
            text(row["label"], "graph label", 500)
            if row["status"] not in {"proposed", "assumption"}:
                raise ValueError("model cannot grant accepted or completed status")
            kinds = (
                {"actor", "component", "data", "interface"}
                if entity == "node"
                else {"relationship"}
            )
            if row["kind"] not in kinds:
                raise ValueError("invalid logical graph kind")
            if (
                not isinstance(row["sources"], list)
                or len(row["sources"]) > 20
                or any(not isinstance(ref, str) or ref not in valid_refs for ref in row["sources"])
            ):
                raise ValueError("graph source must resolve in task input")
            text(row["assumption"], "proposal rationale", 2000)
    nodes = {row["id"] for row in graph["nodes"]}
    for edge in graph["edges"]:
        if edge["from"] not in nodes or edge["to"] not in nodes:
            raise ValueError("graph endpoint does not exist")
    if not any(row["kind"] in {"component", "interface"} for row in graph["nodes"]):
        raise ValueError("system graph must contain a component or interface")
    return graph


def source_refs(controller, task_id):
    ws = controller.workspace(task_id)
    refs = {"request"}
    from harness.investigation_store import InvestigationStore

    bundle = InvestigationStore(controller, None, task_id).read_active()
    if bundle:
        refs.update(f"investigation:{task_id}:{s['id']}" for s in bundle["content"]["sources"])
    from harness.effective_records import effective_records

    refs.update(f"Q{q.id}" for q in effective_records(ws).questions)
    refs.update(e.ref for e in ws.design_entries())
    cp = ws.latest_checkpoint("context_accepted")
    for kind in ("criteria", "invariants"):
        refs.update(
            r["id"] for r in (cp.payload.get("requirements", {}).get(kind, []) if cp else [])
        )
    return refs


def project_graph(controller, task, recorded, requirements, execution=None):
    from harness.authoring_store import AuthoringStore

    prefix = f"workflow:{task.id}:"
    nodes = [
        {
            "id": prefix + "request",
            "label": task.request,
            "kind": "request",
            "status": "stated",
            "sources": ["request"],
            "provenance": "Original user request",
        }
    ]
    edges = []
    for index, phase in enumerate(PHASE_ORDER):
        ident = prefix + str(phase)
        nodes.append(
            {
                "id": ident,
                "label": str(phase).replace("_", " ").title(),
                "kind": "workflow_stage",
                "status": "current" if task.phase == phase else "workflow",
                "sources": [f"task:{task.id}"],
                "provenance": "Harness workflow; ordering is not completion evidence",
            }
        )
        edges.append(
            {
                "id": prefix + f"step-{index}",
                "from": nodes[-2]["id"],
                "to": ident,
                "label": "workflow",
                "sources": [f"task:{task.id}"],
                "kind": "workflow",
                "status": "workflow",
            }
        )
    for row in requirements:
        if row["key"] == "request":
            continue
        ident = prefix + "requirement:" + row["key"]
        nodes.append(
            {
                "id": ident,
                "label": row["text"],
                "kind": "requirement",
                "status": row["status"],
                "sources": [row["key"]],
                "provenance": row["provenance"],
            }
        )
        edges.append(
            {
                "id": ident + ":defines",
                "from": prefix + "request",
                "to": ident,
                "label": "requires",
                "sources": [row["key"]],
                "kind": "traceability",
                "status": "recorded",
            }
        )
    from harness.effective_records import effective_records

    for question in effective_records(controller.workspace(task.id)).questions:
        if question.status != "open":
            continue
        ident = prefix + f"question:{question.id}"
        nodes.append(
            {
                "id": ident,
                "label": question.text,
                "kind": "question",
                "status": "unresolved",
                "sources": [f"Q{question.id}"],
                "provenance": "Recorded open question",
            }
        )
        edges.append(
            {
                "id": ident + ":asks",
                "from": prefix + "request",
                "to": ident,
                "label": "needs an answer",
                "sources": [f"Q{question.id}"],
                "kind": "question",
                "status": "unresolved",
            }
        )
    nodes.append(
        {
            "id": prefix + "missing-system",
            "label": "System components and relationships need explicit design",
            "kind": "missing_detail",
            "status": "unresolved",
            "sources": ["request"],
            "provenance": "No accepted system architecture is inferred",
        }
    )
    edges.append(
        {
            "id": prefix + "needs-design",
            "from": prefix + "request",
            "to": prefix + "missing-system",
            "label": "needs concrete logical design",
            "sources": ["request"],
            "kind": "missing_detail",
            "status": "unresolved",
        }
    )
    if execution:
        ident = prefix + "execution:" + execution["id"]
        nodes.append(
            {
                "id": ident,
                "label": "Execution " + execution["id"],
                "kind": "execution",
                "status": execution.get("status", "unknown"),
                "sources": [f"execution:{execution['id']}"],
                "provenance": "Recorded execution",
            }
        )
        edges.append(
            {
                "id": ident + ":bound",
                "from": prefix + "request",
                "to": ident,
                "label": "recorded run",
                "sources": [f"execution:{execution['id']}"],
                "kind": "execution",
                "status": "recorded",
            }
        )
    workflow = {"nodes": nodes, "edges": edges}
    primary, kind = (
        recorded if recorded["nodes"] else workflow,
        "recorded" if recorded["nodes"] else "workflow",
    )
    binding = controller.conn.execute(
        "SELECT * FROM authoring_tasks WHERE task_id=?", (task.id,)
    ).fetchone()
    graph_revision = None
    if binding:
        store = AuthoringStore(controller, binding["project"], task.id)
        revision = store.revision(binding["working_id"]) if binding["working_id"] else None
        # A successful current model candidate is visible as proposed design, while
        # the saved user text remains the working editor baseline.
        candidate_row = controller.conn.execute(
            "SELECT r.id FROM authoring_revisions r JOIN authoring_jobs j ON j.task_id=r.task"
            "_id WHERE r.task_id=? AND r.attribution='model_proposal' AND j.state='succeeded'"
            " AND json_extract(j.result_json,'$.revision')=r.id ORDER BY r.rowid DESC LIMIT 1",
            (task.id,),
        ).fetchone()
        from harness.investigation_store import InvestigationStore

        sources = InvestigationStore(controller, None, task.id)
        semantic = sources.semantic()

        def source_current(row):
            return semantic is None or row["source"].get("source_investigation") == semantic

        if candidate_row and not (revision and revision["graph"] and source_current(revision)):
            revision = store.revision(candidate_row["id"])
        if revision and revision["graph"] and source_current(revision):
            candidate = revision["graph"]["value"]
            try:
                validate_graph(candidate, source_refs(controller, task.id))
            except ValueError:
                pass
            else:
                graph_revision = revision["graph"]["id"]
                primary = {
                    key: [
                        dict(
                            row,
                            id=f"authoring:{task.id}:{row['id']}",
                            **(
                                {
                                    "from": f"authoring:{task.id}:{row['from']}",
                                    "to": f"authoring:{task.id}:{row['to']}",
                                }
                                if key == "edges"
                                else {}
                            ),
                        )
                        for row in candidate[key]
                    ]
                    for key in ("nodes", "edges")
                }
                kind = "proposed_system"

    if kind != "workflow":
        detail = next(node for node in nodes if node["id"] == prefix + "missing-system")
        detail.update(
            kind="system_detail",
            status="proposed" if kind == "proposed_system" else "recorded",
            label="Proposed concrete system design available; review assumptions"
            if kind == "proposed_system"
            else "Recorded system design available; review sources",
        )
        design_edge = next(edge for edge in edges if edge["id"] == prefix + "needs-design")
        design_edge.update(label="system design available", status=detail["status"])

    def layout(graph):
        for i, node in enumerate(graph["nodes"]):
            node.setdefault("x", 60 + i % 3 * 240)
            node.setdefault("y", 60 + i // 3 * 150)
        return graph

    layout(workflow)
    layout(primary)
    revision = digest(
        {"primary": primary, "workflow": workflow, "kind": kind, "graph_revision": graph_revision}
    )
    return {
        "primary": primary,
        "workflow": workflow,
        "recorded": recorded,
        "kind": kind,
        "revision": revision,
        "graph_revision": graph_revision,
    }
