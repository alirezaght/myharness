"""Disposable cooperative owners and attached source payloads; never native provider proof."""

import uuid

import pytest


@pytest.fixture
def session(controller, tmp_path):
    from harness.authoring import project_identity
    from harness.planning_session import PlanningSessionService
    from harness.planning_session_identity import prepare_capability_artifact, resolve_caller

    caller = resolve_caller(
        "codex", {"CODEX_SESSION_ID": str(uuid.uuid4()), "CODEX_THREAD_ID": str(uuid.uuid4())}
    )
    root = tmp_path / "project"
    root.mkdir()
    project = project_identity(root)
    payload = {
        "schema_version": 1,
        "project": project,
        "request_id": "session-start-fixture",
        "request": "AR-2906",
        "complexity": "trivial",
    }
    cap = prepare_capability_artifact(
        {"project": project, "request_id": payload["request_id"]}, caller, tmp_path / "capabilities"
    )
    service = PlanningSessionService(controller, root)
    receipt = service.start(payload, caller, cap)
    return service, receipt, caller, cap


def request(service, receipt, request_id):
    return {
        "schema_version": 1,
        "project": receipt["project"],
        "task": receipt["task"],
        "attachment_id": receipt["attachment_id"],
        "epoch": receipt["epoch"],
        "request_id": request_id,
        "expected": service.base(),
    }


def new_cap(service, caller, request_id, directory):
    from harness.planning_session_identity import prepare_capability_artifact

    return prepare_capability_artifact(
        {"project": service.project, "request_id": request_id}, caller, directory
    )


def attached_payload(bridge, receipt, request_id, *, decisions=True, repository_only=False):
    from dataclasses import asdict

    from harness.interview_coverage import coverage_entries
    from harness.investigation_repository import Reader, source_record
    from tests.investigation_fixture import synthesis

    (bridge.root / "__init__.py").write_bytes(b"")
    (bridge.root / "export.py").write_text("export_csv = True\n")
    from harness.authoring import AuthoringService
    from harness.investigation_artifacts import derive

    observation = Reader(
        bridge.root,
        managed_policy=derive(AuthoringService(bridge.controller, bridge.root, bridge.task_id)),
    ).observe("CSV export")
    sources = (
        observation["sources"]
        if repository_only
        else [
            source_record(
                "ticket",
                "https://one.atlassian.net/browse/AR-2906",
                "Preserve existing CSV exports and export accounts as CSV.",
                {},
            )
        ]
        + observation["sources"]
    )
    output = synthesis(
        {
            "base": {"task": bridge.task_id},
            "investigation": {
                "sources": sources,
                "catalogue": [
                    asdict(e) for e in coverage_entries(bridge.controller, bridge.task_id)
                ],
            },
        },
        decisions=decisions,
    )
    req = request(bridge, receipt, request_id)
    envelope = {
        "schema_version": 3,
        "origin": "attached",
        "task": bridge.task_id,
        "project": bridge.project,
        "attachment_id": receipt["attachment_id"],
        "epoch": receipt["epoch"],
        "selection": {
            "issue": "AR-2906",
            "site": {"id": "cloud-one", "url": "https://one.atlassian.net"},
        },
        "sources": sources,
        "evidence": output["investigation"],
        "observations": ["Synthetic ticket and confined repository reads"],
        "limitations": observation["limitations"],
        "captured_base": bridge.base(),
        "candidate": {"body": output["body"], "graph": output["graph"]["concrete"]},
    }
    if repository_only:
        envelope["selection"] = {"schema_version": 1, "kind": "repository"}
    return req | {"consumes": [], "content": {"source_bundle": {"attached_envelope": envelope}}}


def ordinary_owner(controller, tmp_path):
    from harness.authoring import project_identity
    from harness.planning_session import PlanningSessionService
    from harness.planning_session_identity import prepare_capability_artifact, resolve_caller

    root = tmp_path / "ordinary"
    root.mkdir()
    (root / "export.py").write_text("def export_csv(accounts):\n    return accounts\n")
    (root / "test_export.py").write_text("def test_export_csv():\n    assert True\n")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "synthetic-export"\nversion = "1.0.0"\ndependencies = []\n'
    )
    caller = resolve_caller(
        "codex", {"CODEX_SESSION_ID": str(uuid.uuid4()), "CODEX_THREAD_ID": str(uuid.uuid4())}
    )
    project = project_identity(root)
    cap = prepare_capability_artifact(
        {"project": project, "request_id": "ordinary-start-fixture"},
        caller,
        tmp_path / "capabilities",
    )
    bridge = PlanningSessionService(controller, root)
    receipt = bridge.start(
        {
            "schema_version": 1,
            "project": project,
            "request_id": "ordinary-start-fixture",
            "request": "Add CSV export for accounts",
            "complexity": "trivial",
        },
        caller,
        cap,
    )
    return bridge, receipt, caller, cap
