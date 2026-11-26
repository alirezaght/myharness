# ruff: noqa: F811
"""Alternative commands and forged caller descriptions never narrow real resource authority."""

from pathlib import Path

import pytest

from tests.test_policy_admission import (  # noqa: F401
    configure,
    document,
    observed,
    patch,
    policy_runtime,
    processes,
)


def test_command_effects_cannot_bypass_actual_write_scope(policy_runtime):
    r = policy_runtime
    configure(r, document(r, rules={"write_paths": ["svc/narrow"]}))
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        r.operation.dispatch(
            r.principal,
            "run_command",
            {
                "argv": ["python3", "-c", "open('svc/escape','w').write('bad')"],
                "cwd": ".",
                "effects": "read_only",
                "target": "svc/narrow",
                "task_id": "work",
                "expected_revision": r.revision,
            },
        )
    assert not (Path(r.operation.envelope["repository"]) / "svc/escape").exists()


@pytest.mark.parametrize("restriction", ["network", "database", "production"])
def test_unqualified_resource_enforcement_denies_before_subprocess(policy_runtime, restriction):
    r = policy_runtime
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        configure(r, document(r, rules={restriction: "deny"}))
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_versions").fetchone()[0] == 0
    assert not r.operation.processes.records


def test_supplied_role_does_not_erase_role_policy(policy_runtime):
    r = policy_runtime
    doc = document(r, rules={"deny_tools": ["apply_patch"]})
    doc.update(scope_kind="role", scope_ref="al-engineer")
    configure(r, doc)
    with pytest.raises(ValueError, match="AUTHORITY_STALE"):
        r.operation.dispatch(r.principal | {"role": "al-builder"}, "apply_patch", patch(r))


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("knowledge_query", {"query": "untrusted file contents"}),
        ("read_file", {"path": "harness:task-package"}),
    ],
)
def test_narrow_file_policy_does_not_silently_allow_unresolved_alternative_reads(
    policy_runtime, name, arguments
):
    r = policy_runtime
    configure(r, document(r, rules={"read_paths": ["svc/narrow"]}))
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        r.operation.dispatch(r.principal, name, arguments)


def test_unknown_dispatch_preserves_fixed_closed_error(policy_runtime):
    from tests.test_policy_admission import configure

    r = policy_runtime
    configure(r)
    with pytest.raises(ValueError, match="^unknown operation$"):
        r.operation.dispatch(r.principal, "untrusted-tool-name-canary", {})
    assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
        :
    ] == (0, 0)


def _audit_fixture(r, *, role_only=False):
    root = Path(r.operation.envelope["repository"])
    (root / "pyproject.toml").write_text('[project]\nname="claim-fixture"\nversion="0.1.0"\n')
    (root / "svc").mkdir(exist_ok=True)
    (root / "svc/live.py").write_text("value = 1\n")
    doc = document(r, maximum=20)
    if role_only:
        doc.update(scope_kind="role", scope_ref="al-engineer")
    configure(r, doc)
    return root


def _unresolved_public_producer(r, kind, monkeypatch):
    import sqlite3

    from harness.execution_artifacts import ArtifactStore
    from harness.policy import claims

    transition = claims.transition
    faulted = []

    def fail_terminal(conn, attempt_id, state):
        row = conn.execute(
            "SELECT operation,state FROM policy_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if row["state"] == "started" and state in {"closed", "unresolved"}:
            faulted.append(attempt_id)
            raise sqlite3.IntegrityError("review fixture settlement fault")
        return transition(conn, attempt_id, state)

    with monkeypatch.context() as patcher:
        patcher.setattr(claims, "transition", fail_terminal)
        with pytest.raises(sqlite3.IntegrityError, match="review fixture settlement fault"):
            if kind == "overlap":
                r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/producer.py"))
            else:
                r.operation.artifacts = ArtifactStore(r.operation.directory / "review-artifacts")
                r.operation.dispatch(
                    r.root,
                    "write_artifact",
                    {
                        "name": "producer.md",
                        "content": "observed producer output",
                        "expected_graph_revision": r.state["integration"]["graph_revision"],
                    },
                )
    assert len(faulted) == 1
    row = r.controller.conn.execute(
        "SELECT a.state,c.claim,c.state FROM policy_attempts a "
        "JOIN policy_resource_claims c ON c.attempt_id=a.id"
    ).fetchone()
    import json

    assert row[0] == "started" and row[2] != "closed"
    assert json.loads(row[1])["access"] == ("write" if kind == "overlap" else "unknown")


@pytest.mark.parametrize("producer", ["overlap", "unknown"])
def test_repository_audit_refuses_unresolved_source_before_traversal(
    policy_runtime, monkeypatch, producer
):
    from harness import repository_audit

    r = policy_runtime
    _audit_fixture(r)
    _unresolved_public_producer(r, producer, monkeypatch)
    traversed, texts = [], []
    original_inventory, original_text = repository_audit.inventory, repository_audit._text

    def inventory(root):
        value = original_inventory(root)
        traversed.extend(value[0])
        return value

    def text(root, path):
        value = original_text(root, path)
        texts.append(path)
        return value

    monkeypatch.setattr(repository_audit, "inventory", inventory)
    monkeypatch.setattr(repository_audit, "_text", text)
    rejected = None
    try:
        r.operation.dispatch(r.root, "read_file", {"path": "harness:repository-audit"})
    except ValueError as error:
        rejected = str(error)
    assert (
        rejected in {"ATTEMPT_NOT_EXECUTABLE", "UNSUPPORTED_ENFORCEMENT_CAPABILITY"}
        and not traversed
        and not texts
    ), (
        f"R3-FRESH-003: {producer} producer did not fence actual audit; "
        f"rejection={rejected}, inventoried={len(traversed)}, source_reads={texts}"
    )
    assert (
        r.operation.dispatch(r.root, "read_file", {"path": "harness:operational-issues"})["text"]
        == "{}"
    )


def test_repository_audit_holds_source_claim_against_racing_governed_writer(
    policy_runtime, monkeypatch
):
    import copy
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from harness import repository_audit
    from harness.execution_operations import OperationController

    r = policy_runtime
    root = _audit_fixture(r)
    other = copy.copy(r.operation)
    other.lock = threading.RLock()
    other.state = OperationController.state.__get__(other)
    original_inventory, original_text = repository_audit.inventory, repository_audit._text
    traversed, texts, outcomes = [], [], []

    def write():
        try:
            request = patch(r)
            request["patch"] = (
                "*** Begin Patch\n*** Update File: svc/live.py\n@@\n"
                "-value = 1\n+value = 2\n*** End Patch"
            )
            other.dispatch(r.principal, "apply_patch", request)
            return "committed"
        except ValueError as error:
            return str(error)

    def inventory(path):
        value = original_inventory(path)
        traversed.extend(value[0])
        with ThreadPoolExecutor(max_workers=1) as pool:
            outcomes.append(pool.submit(write).result(timeout=10))
        return value

    def text(path, name):
        value = original_text(path, name)
        texts.append(name)
        return value

    monkeypatch.setattr(repository_audit, "inventory", inventory)
    monkeypatch.setattr(repository_audit, "_text", text)
    rejected = None
    try:
        r.operation.dispatch(r.root, "read_file", {"path": "harness:repository-audit"})
    except ValueError as error:
        rejected = str(error)
    if rejected in {"ATTEMPT_NOT_EXECUTABLE", "UNSUPPORTED_ENFORCEMENT_CAPABILITY"}:
        assert not traversed and not texts and not outcomes
    else:
        assert traversed and texts, "actual audit inventory and source reader must execute"
        assert outcomes == ["ATTEMPT_NOT_EXECUTABLE"], (
            f"R3-FRESH-003: racing writer entered live audit window: {outcomes}"
        )
    assert (root / "svc/live.py").read_text() == "value = 1\n"
    assert (
        r.operation.dispatch(r.root, "read_file", {"path": "harness:operational-issues"})["text"]
        == "{}"
    )


@pytest.mark.parametrize("selection", ["unmatched-role", "removed-binding"])
def test_live_audit_has_no_unmatched_or_removed_policy_fallback(
    policy_runtime, monkeypatch, selection
):
    from harness import repository_audit

    r = policy_runtime
    _audit_fixture(r, role_only=selection == "unmatched-role")
    _unresolved_public_producer(r, "overlap", monkeypatch)
    if selection == "removed-binding":
        r.controller.conn.execute("DELETE FROM policy_bindings")
    original = repository_audit.inventory
    calls = []

    def inventory(root):
        calls.append(True)
        return original(root)

    monkeypatch.setattr(repository_audit, "inventory", inventory)
    with pytest.raises(
        ValueError, match="^(UNSUPPORTED_ENFORCEMENT_CAPABILITY|ATTEMPT_NOT_EXECUTABLE)$"
    ):
        r.operation.dispatch(r.root, "read_file", {"path": "harness:repository-audit"})
    assert not calls
    assert (
        r.operation.dispatch(r.root, "read_file", {"path": "harness:operational-issues"})["text"]
        == "{}"
    )


def _descriptor_route(r, route):
    relative = "harness:unrecognized.py" if route == "fallback" else "svc/diagnostic.py"
    target = Path(r.operation.envelope["repository"]) / relative
    target.parent.mkdir(exist_ok=True)
    target.write_text("ordinary descriptor source\n")
    if route == "fallback":
        return target, "read_file", {"path": relative}
    return target, "diagnostic", {"request": {"kind": route, "path": relative}}


@pytest.mark.parametrize("route", ["fallback", "source", "log", "config"])
@pytest.mark.parametrize("selection", ["selected", "unmatched-role", "removed-binding"])
def test_descriptor_backed_routes_do_not_skip_known_liability(
    policy_runtime, monkeypatch, route, selection
):
    r = policy_runtime
    target, name, request = _descriptor_route(r, route)
    _audit_fixture(r, role_only=selection == "unmatched-role")
    _unresolved_public_producer(r, "unknown", monkeypatch)
    if selection == "removed-binding":
        r.controller.conn.execute("DELETE FROM policy_bindings")
    # This genuinely metadata-only diagnostic still has no repository byte dependency.
    assert r.operation.dispatch(
        r.root, "diagnostic", {"request": {"kind": "attribution-compatibility"}}
    )["status"] in {"unsupported", "unverified"}
    rejected = None
    page = None
    try:
        page = r.operation.dispatch(r.root, name, request)
    except ValueError as error:
        rejected = str(error)
    assert rejected == "ATTEMPT_NOT_EXECUTABLE", (
        f"F003 route={route} selection={selection} returned source despite unknown liability; "
        f"error={rejected}, page_bytes={page.get('page_bytes') if page else None}"
    )
    assert target.read_text() == "ordinary descriptor source\n"


@pytest.mark.parametrize("route", ["fallback", "source", "log", "config"])
def test_descriptor_backed_routes_respect_configured_read_scope(policy_runtime, route):
    r = policy_runtime
    _, name, request = _descriptor_route(r, route)
    configure(r, document(r, maximum=20, rules={"read_paths": ["svc/allowed"]}))
    with pytest.raises(ValueError, match="^(POLICY_DENIED|UNSUPPORTED_ENFORCEMENT_CAPABILITY)$"):
        r.operation.dispatch(r.root, name, request)


@pytest.mark.parametrize("route", ["fallback", "source", "log", "config"])
@pytest.mark.parametrize("configured", [False, True])
def test_descriptor_backed_routes_preserve_legacy_and_matching_scope(
    policy_runtime, route, configured
):
    r = policy_runtime
    target, name, request = _descriptor_route(r, route)
    if configured:
        configure(
            r,
            document(
                r,
                maximum=20,
                rules={
                    "read_paths": [
                        target.relative_to(r.operation.envelope["repository"]).as_posix()
                    ]
                },
            ),
        )
    page = r.operation.dispatch(r.root, name, request)
    assert page["text"] == "ordinary descriptor source\n" and page["hash_status"] == "complete"


@pytest.mark.parametrize("kind", ["source", "log", "config"])
@pytest.mark.parametrize("dimension", ["operation_deadline_ms", "execution_elapsed_ms"])
def test_descriptor_diagnostic_name_does_not_exempt_or_qualify_time(
    policy_runtime, monkeypatch, kind, dimension
):
    import os

    r = policy_runtime
    target, name, request = _descriptor_route(r, kind)
    configure(r, document(r, dimension=dimension, maximum=0))
    inode = target.stat().st_ino
    real_read = os.read
    reads = []

    def reading(fd, amount):
        if os.fstat(fd).st_ino == inode:
            reads.append(True)
        return real_read(fd, amount)

    monkeypatch.setattr(os, "read", reading)
    with pytest.raises(ValueError, match="^(UNSUPPORTED_ENFORCEMENT_CAPABILITY|BUDGET_EXHAUSTED)$"):
        r.operation.dispatch(r.root, name, request)
    assert not reads
