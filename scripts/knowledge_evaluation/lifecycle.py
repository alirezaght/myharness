"""Narrow authenticated returned-producer finalization, with B ingestion denied at entry."""

import json
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from harness.controller import HarnessController
from harness.db import connect
from harness.execution_artifacts import output_reports
from harness.execution_authority import require_authored_report
from harness.execution_hosts import al_source

from .observer import observe_return
from .package import sha, write_once


@contextmanager
def ingestion_boundary(arm, refusals):
    if arm != "B":
        yield
        return
    from harness.knowledge import contributions

    def deny(*args, **kwargs):
        refusals.append(
            {"status": "unavailable", "reason": "evaluation B ingestion denied at entry"}
        )
        raise ValueError("evaluation B ingestion denied at entry")

    with patch.object(contributions, "ingest", deny):
        yield


def complete(adapter, principal):
    stage, operation = adapter.stage, adapter.operation
    if not stage or stage["action"] != "donor" or operation.envelope["host"] != "codex":
        raise ValueError("supported Codex donor finalization required")
    if principal["role"] != "orchestrator" or principal["thread_id"] != operation.registry.root:
        raise ValueError("actual authenticated owning root required")
    if getattr(adapter, "completion", None) or len(adapter.answers) != 1:
        raise ValueError("one unfinalized authored donor answer required")
    answer = adapter.answers[0]
    child = answer["principal"]
    observed = observe_return(
        operation.registry.native_root, principal["thread_id"], child, adapter.package["role"]
    )
    with closing(connect(operation.database)) as conn:
        controller = HarnessController(conn)
        execution, state = operation.authority(controller, principal, operation="read")
        if execution["status"] != "Running":
            raise ValueError("live owning root execution required")
        agent = state["integration"]["agents"].get(child)
        if (
            not agent
            or agent["task"] != stage["al_task_id"]
            or agent.get("yield_required")
            or agent["role"] != answer["role"]
            or agent["status"] != "Idle"
        ):
            raise ValueError("exact current idle donor lifecycle binding required")
        reports = output_reports(operation.artifacts.directory)
        report = next(
            (r for r in reports if r["owner"] == child and r["name"] == "answer.md"), None
        )
        if not report or report["sha256"] != answer["artifact"]["sha256"]:
            raise ValueError("exact authored donor report provenance required")
        # Validate the report graph before lifecycle mutation and again during normal ingestion.
        index = json.loads((operation.artifacts.directory / "index.json").read_bytes())
        if (
            index["records"][report["artifact_id"]]["graph_revision"]
            != state["integration"]["graph_revision"]
        ):
            raise ValueError("donor report graph revision stale")
        for status in ("Waiting", "Completed"):
            result = adapter.report_registration(
                "agent-update",
                "evaluation-" + status + ".json",
                {
                    "id": child,
                    "status": status,
                    "latest_output": report["resource"],
                    "activity": (
                        "Native return observed; awaiting supervised finalization"
                        if status == "Waiting"
                        else "Observed native invocation returned; report finalized"
                    ),
                },
                state,
            )
            _, state = operation.authority(controller, principal, operation="read")
        require_authored_report(execution, state, stage["al_task_id"], report["resource"])
        source = al_source(execution, operation.envelope)
        receipt = {
            "kind": "evaluation-producer-completion-v1",
            "task_id": execution["task_id"],
            "execution_id": execution["id"],
            "al_task_id": stage["al_task_id"],
            "repository": operation.envelope["repository"],
            "owner": child,
            "role": answer["role"],
            "report": report,
            "graph_revision": state["integration"]["graph_revision"],
            "source_sha256": sha(source.read_bytes()),
            "native_return": observed,
            "native_root": str(operation.registry.native_root.resolve()),
            "knowledge_contributions": result.get("knowledge_contributions", []),
            "B_ingestion_refusals": adapter.ingestion_refusals,
            "agent_status": state["integration"]["agents"][child]["status"],
            "mission_complete": False,
        }
        adapter.completion = receipt
        write_once(Path(adapter.package["output_directory"]) / "producer-completion.json", receipt)
        if adapter.package["arm"] == "B":
            adapter.revalidate()
        return receipt
