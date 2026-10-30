"""Logical change illustrations are source-bound views, never design approvals."""

import json
from dataclasses import dataclass, replace

from harness.db import transaction
from harness.design_models import DesignSection, EntryKind, EntryStatus
from harness.diagram_model import build_model
from harness.diagram_scenes import render_scenes
from harness.execution_contract import canonical, digest
from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_epochs import require_active_epoch
from harness.execution_models import ExecutionConflict
from harness.execution_store import get_execution, latest_execution

KIND = "diagram.change_illustration"


@dataclass(frozen=True)
class RequirementSource:
    id: int
    ref: str
    statement: str
    section: DesignSection = DesignSection.REQUIRED_BEHAVIOUR
    status: EntryStatus = EntryStatus.STATED
    kind: EntryKind = EntryKind.ELEMENT
    response: str | None = None

    @property
    def effective(self):
        return True

    @property
    def text(self):
        return self.statement


def sources(controller, task_id):
    entries = {
        e.ref: e
        for e in controller.workspace(task_id).design_entries()
        if e.effective and e.kind == EntryKind.ELEMENT
    }
    if entries:
        return entries
    from harness.interview import InterviewEngine

    return {
        f"Q{r.question_id}" if r.question_id else f"requirement-{i}": RequirementSource(
            100000 + i, f"Q{r.question_id}" if r.question_id else f"requirement-{i}", r.statement
        )
        for i, r in enumerate(InterviewEngine(controller, task_id).summary().requirements, 1)
    }


def binding(controller, execution):
    entries = sources(controller, execution["task_id"])
    return {
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
        "authority_generation": execution["authority_generation"],
        "spec_hash": execution["binding"]["content_hash"],
        "design_hash": digest({ref: e.text for ref, e in entries.items()}),
    }


def model_of(entries, parts):
    synthetic, metadata = [], {}
    if not isinstance(parts, list) or not 1 <= len(parts) <= 64:
        raise ValueError("bounded diagram parts required")
    for part in parts:
        if not isinstance(part, dict) or set(part) != {
            "sources",
            "notation",
            "change",
            "inferred",
            "explanation",
        }:
            raise ValueError("closed logical diagram part required")
        refs = part["sources"]
        if (
            not isinstance(refs, list)
            or not 1 <= len(refs) <= 16
            or any(ref not in entries for ref in refs)
        ):
            raise ValueError("logical diagram must reference current effective design entries")
        if (
            part["change"] not in {"existing", "changed", "new"}
            or type(part["inferred"]) is not bool
        ):
            raise ValueError("explicit change and inference status required")
        if any(
            not isinstance(part[k], str) or not 1 <= len(part[k]) <= 12000
            for k in ("notation", "explanation")
        ):
            raise ValueError("bounded diagram notation and explanation required")
        changed = [
            replace(
                entries[ref], status=EntryStatus.STATED, statement=part["notation"], response=None
            )
            for ref in refs
        ]
        partial = build_model(changed)
        if partial.issues or partial.empty:
            raise ValueError("valid component/flow diagram notation required")
        for scene in render_scenes(partial.data):
            for item in scene["items"]:
                if item["kind"] == "component" and not item["detail"].get("declared"):
                    continue
                if item["id"] in metadata and metadata[item["id"]] != part:
                    raise ValueError("conflicting logical diagram element definitions")
                metadata[item["id"]] = part
        synthetic.extend(changed)
    model = build_model(synthetic)
    scenes = render_scenes(model.data)
    # Flow scenes already contain their components. Keep a separate component
    # scene only when it adds elements or connections to the logical view.
    other_ids = {i["id"] for s in scenes if s["type"] != "component" for i in s["items"]}
    scenes = [
        s
        for s in scenes
        if s["type"] != "component"
        or any(i["shape"] == "arrow" or i["id"] not in other_ids for i in s["items"])
    ]
    return scenes, metadata


def publish(controller, execution, content):
    if not isinstance(content, dict) or set(content) != {
        "spec_hash",
        "design_hash",
        "caption",
        "parts",
    }:
        raise ValueError("closed source-bound logical diagram required")
    if not isinstance(content["caption"], str) or not 1 <= len(content["caption"]) <= 300:
        raise ValueError("bounded logical diagram caption required")
    if len(canonical(content).encode()) > 65536:
        raise ValueError("logical diagram exceeds 64 KiB")
    with epoch_action_lock(execution):
        current = require_active_epoch(
            controller.conn,
            execution["id"],
            mutation=True,
            expected_epoch_id=execution["epoch_id"],
            expected_authority_generation=execution["authority_generation"],
        )
        expected = binding(controller, current)
        if any(content[key] != expected[key] for key in ("spec_hash", "design_hash")):
            raise ExecutionConflict(
                "logical diagram source changed; inspect the current specification"
            )
        from harness.execution_authority import require_execution_authority

        require_execution_authority(controller, current["id"], operation="read")
        model_of(sources(controller, current["task_id"]), content["parts"])
        with transaction(controller.conn):
            event = controller.workspace(current["task_id"]).record_event(
                KIND, {"binding": expected, "content": content, "authority": "REFERENCE_ONLY"}
            )
        return {
            "status": "Recorded",
            "event_id": event.id,
            "binding": expected,
            "specification_changed": False,
            "approval_granted": False,
        }


def diagram_operation(operation, controller, principal, state, request):
    current = get_execution(controller.conn, operation.execution_id)
    current_binding = binding(controller, current)
    source_notes = sources(controller, current["task_id"])
    if request == {"operation": "inspect"}:
        return {
            "binding": current_binding,
            "sources": {
                ref: {"section": str(e.section), "text": e.text}
                for ref, e in sources(controller, current["task_id"]).items()
            },
            "publication_schema": {
                "spec_hash": current_binding["spec_hash"],
                "design_hash": current_binding["design_hash"],
                "caption": "What changes",
                "parts": [
                    {
                        "sources": [next(iter(source_notes), "source-ref")],
                        "notation": "component Example",
                        "change": "changed",
                        "inferred": False,
                        "explanation": "How this relates to the approved source",
                    }
                ],
            },
            "authority": "REFERENCE_ONLY",
        }
    if (
        principal["role"] != "orchestrator"
        or request.get("operation") != "publish"
        or set(request) != {"operation", "content", "expected_graph_revision"}
    ):
        raise ValueError("only orchestrator may publish a closed logical diagram request")
    if request["expected_graph_revision"] != state["integration"]["graph_revision"]:
        raise ValueError("logical diagram graph revision is stale")
    return publish(controller, current, json.loads(request["content"]))


def reference_view(controller, task, execution=None):
    entries = sources(controller, task.id)
    if not entries:
        return None
    if not latest_execution(controller.conn, task.id) and all(
        isinstance(e, RequirementSource) for e in entries.values()
    ):
        return None
    if not build_model(entries.values()).empty:
        return None  # Existing explicit design diagrams already supply a logical view.
    current = latest_execution(controller.conn, task.id)
    expected = binding(controller, current) if current else None
    rows = controller.conn.execute(
        "SELECT payload FROM events WHERE task_id=? AND kind=? ORDER BY id DESC LIMIT 20",
        (task.id, KIND),
    )
    for row in rows:
        value = json.loads(row[0])
        if expected and value.get("binding") == expected:
            content = value["content"]
            scenes, metadata = model_of(entries, content["parts"])
            return {
                "kind": "logical_change",
                "caption": content["caption"],
                "binding": expected,
                "scenes": scenes,
                "elements": metadata,
                "sources": {ref: e.text for ref, e in entries.items()},
                "notice": (
                    "Planned change from the approved specification. Inferred relationships are "
                    "labelled; this view does not assert implementation or test completion."
                ),
            }
    # Always show the approved change rather than an empty panel. This initial
    # scope map makes no claim about undocumented component-to-component flows.
    first = next(iter(entries))
    parts = [
        {
            "sources": [first],
            "notation": "component Approved change",
            "change": "changed",
            "inferred": False,
            "explanation": task.request,
        }
    ]
    sections = {}
    for ref, entry in entries.items():
        sections.setdefault(entry.section.heading, []).append((ref, entry.text))
    for heading, items in sections.items():
        parts.append(
            {
                "sources": [ref for ref, _ in items][:16],
                "notation": f"component {heading}\nconnect Approved change -> {heading}: specifies",
                "change": "changed",
                "inferred": False,
                "explanation": "\n".join(text for _, text in items)[:12000],
            }
        )
    scenes, metadata = model_of(entries, parts)
    return {
        "kind": "change_scope",
        "caption": "What this task changes",
        "binding": expected,
        "scenes": scenes,
        "elements": metadata,
        "sources": {ref: e.text for ref, e in entries.items()},
        "affected_paths": sorted(
            {f for row in (execution or {}).get("tasks", []) for f in row["files"]}
        )[:24],
        "notice": (
            "Initial scope from the approved design and recorded ownership. Detailed component "
            "flows appear as the implementation plan records them."
        ),
    }
