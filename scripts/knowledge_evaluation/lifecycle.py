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
    from .method import participant_profile

    profile = participant_profile(adapter.package) if adapter.budget else None
    # Qualified observation must precede database access and every authority side effect.
    observed = observe_return(
        operation.registry.native_root,
        principal["thread_id"],
        child,
        adapter.package["role"],
        **({"expected_profile": profile} if profile is not None else {}),
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
        if adapter.budget:
            receipt["evaluation_method"] = adapter.package["method"]
            receipt["participant_profile"] = profile
            receipt["acknowledgment_at_publication"] = "not yet delivered"
        adapter.completion = receipt
        write_once(Path(adapter.package["output_directory"]) / "producer-completion.json", receipt)
        if adapter.package["arm"] == "B":
            adapter.revalidate()
        return receipt


def reconcile_output(adapter, principal, arguments):
    """Observe only own immutable metadata and streaming content hash, never report text."""
    import hashlib
    import os
    import stat

    operation = adapter.operation
    with closing(connect(operation.database)) as conn:
        _, state = operation.authority(HarnessController(conn), principal, operation="read")
    directory = operation.artifacts.directory
    index_path = directory / "index.json"
    if not index_path.exists():
        return None

    def read_index():
        fd = os.open(index_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_size > 4 * 1024 * 1024
            ):
                raise ValueError("bounded own output index required")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("own output index exceeds bound")
            return raw
        finally:
            os.close(fd)

    before = read_index()
    index = json.loads(before)
    owner = principal["thread_id"]
    identifier = index.get("outputs", {}).get(owner, {}).get("answer.md")
    if not identifier:
        return None
    record = index["records"][identifier]
    expected_hash = "sha256:" + sha(arguments["content"].encode())
    if (
        record.get("owner") != owner
        or record.get("role") != principal["role"]
        or record.get("source") != "native-agent-output"
        or record.get("name") != "answer.md"
        or record.get("sha256") != expected_hash
        or record.get("graph_revision") != arguments["expected_graph_revision"]
        or record["graph_revision"] != state["integration"]["graph_revision"]
        or record.get("size") != len(arguments["content"].encode())
    ):
        raise ValueError("own committed output provenance differs")
    path = Path(record["path"])
    if path.parent != directory or not path.name.startswith(identifier + "."):
        raise ValueError("own output resource escaped execution")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or initial.st_uid != os.getuid()
            or initial.st_size > 262144
        ):
            raise ValueError("own immutable regular output required")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            remaining = 262145
            while remaining and (chunk := stream.read(min(65536, remaining))):
                digest.update(chunk)
                remaining -= len(chunk)
            if not remaining:
                raise ValueError("own output exceeds reconciliation bound")
        final = os.fstat(fd)
        stable = ("st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            any(getattr(initial, k) != getattr(final, k) for k in stable)
            or "sha256:" + digest.hexdigest() != expected_hash
            or read_index() != before
        ):
            raise ValueError("own output changed during reconciliation")
    finally:
        os.close(fd)
    reference = {k: record[k] for k in ("artifact_id", "sha256", "size", "name", "owner", "role")}
    adapter.output_state.update(
        output_committed=True,
        artifact=reference,
        reconciliation="authenticated own index/provenance/content hash",
    )
    return reference
