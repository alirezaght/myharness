"""Tool prerequisites stay distinct from authentication and product acceptance."""

import pytest

from harness.launch_readiness import REQUIRED, readiness
from tests.sample_repo import tree_snapshot


@pytest.fixture
def ready_package(tmp_path, monkeypatch):
    import harness.launch_readiness as module
    import harness.native_tool_policy as native

    base = tmp_path / "al"
    for name in REQUIRED:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if name.endswith(".json") else "Mandatory skill input.\n")
    monkeypatch.setattr(
        module,
        "installed_package",
        lambda host, root: {
            "package": str(base),
            "sha256": "sha256:" + "a" * 64,
            "executable": "fixture",
        },
    )
    monkeypatch.setattr(
        native,
        "executable_identity",
        lambda executable, host, **_: {
            "path": executable,
            "version": "qualified-fixture",
            "sha256": "b" * 64,
        },
    )
    return base


def test_preapproval_readiness_is_read_only_and_not_provider_or_acceptance_proof(
    ready_package,
    sample_repo,
):
    before = tree_snapshot(sample_repo)
    result = readiness(sample_repo, "codex")
    assert result["ready"] is True
    assert result["ready_scope"] == "launch_prerequisites"
    assert result["implementation_readiness"] == "UNVERIFIED"
    assert "candidate inspection" in result["readiness_notice"]
    assert result["authentication"].startswith("UNVERIFIED")
    assert result["product_acceptance"] == "NOT_ASSESSED"
    assert result["approval_required_for_implementation"] is True
    assert tree_snapshot(sample_repo) == before


def test_missing_transitive_reference_and_traversal_are_named_before_approval(
    ready_package,
    sample_repo,
):
    (ready_package / "reference/handoffs.md").write_text(
        "Read reference/schemas/not-installed.json and reference/../../outside.md.\n"
    )
    result = readiness(sample_repo, "codex")
    assert result["ready"] is False
    blocked = {c["id"] for c in result["checks"] if c["status"] == "BLOCKED"}
    assert "skill:reference/schemas/not-installed.json" in blocked
    assert "skill:reference/../../outside.md" in blocked


def test_unsupported_host_version_names_range_and_records_only_explicit_override(
    ready_package, sample_repo, monkeypatch, tmp_path
):
    import harness.launch_readiness as module

    monkeypatch.undo()
    executable = tmp_path / "claude-fixture"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' '2.3.0 (Claude Code)'\n")
    executable.chmod(0o700)
    monkeypatch.setattr(
        module,
        "installed_package",
        lambda host, root: {
            "package": str(ready_package),
            "sha256": "sha256:" + "a" * 64,
            "executable": str(executable),
        },
    )
    blocked = readiness(sample_repo, "claude")
    check = next(c for c in blocked["checks"] if c["id"] == "native-host-and-al-protocol")
    assert check["status"] == "BLOCKED"
    assert ">=2.1.310,<2.2.0" in check["reason"]
    assert "--accept-unverified-host-version" in check["reason"]
    accepted = readiness(sample_repo, "claude", accept_unverified_host_version=True)
    check = next(c for c in accepted["checks"] if c["id"] == "native-host-and-al-protocol")
    assert check["status"] == "PASS"
    assert check["override"]["status"] == "UNVERIFIED"
    assert check["override"]["version"] == "2.3.0"


def _dependency_tree(sample_repo, *, hardlink=False):
    import os

    (sample_repo / ".gitignore").write_text("node_modules/\n")
    for package in ("a", "b"):
        folder = sample_repo / "packages" / package / "node_modules/dep"
        folder.mkdir(parents=True)
        for index in range(3):
            (folder / str(index)).write_text("fixture")
    if hardlink:
        folder = sample_repo / "packages/b/node_modules/dep"
        os.link(folder / "0", folder / "alias")


def _layout(result):
    return next(c for c in result["checks"] if c["id"] == "dependency-layout")


def test_dependency_scan_limit_names_the_exact_next_command(
    ready_package, sample_repo, monkeypatch
):
    from harness.dependency_layout import LIMIT_ENV

    _dependency_tree(sample_repo)
    assert _layout(readiness(sample_repo, "codex", task_id="task-1"))["status"] == "PASS"
    monkeypatch.setenv(LIMIT_ENV, "4")
    check = _layout(readiness(sample_repo, "codex", task_id="task-1"))
    assert check["status"] == "BLOCKED" and check["scope"] == "packages/b/node_modules"
    repo = str(sample_repo.resolve())
    assert check["next"][0] == (
        LIMIT_ENV + "=8 harness execution readiness task-1 --host codex --repo " + repo
    )
    assert check["next"][1] == LIMIT_ENV + "=8 harness execution start task-1 --host codex"
    assert check["next"][0] in check["reason"]
    monkeypatch.setenv(LIMIT_ENV, "8")
    assert _layout(readiness(sample_repo, "codex", task_id="task-1"))["status"] == "PASS"


def test_dependency_hardlinks_name_inspect_reconcile_and_readiness(ready_package, sample_repo):

    from harness.dependency_layout import inspect, reconcile
    from harness.execution_contract import digest

    _dependency_tree(sample_repo, hardlink=True)
    check = _layout(readiness(sample_repo, "codex", task_id="task-1"))
    repo = str(sample_repo.resolve())
    assert check["status"] == "BLOCKED"
    assert check["next"] == [
        "harness dependencies inspect task-1 --repo "
        + repo
        + " --scopes packages/b/node_modules --output layout.json",
        "harness dependencies reconcile task-1 --repo "
        + repo
        + " --file layout.json --sha256 SHA256_PRINTED_BY_INSPECT",
        "harness execution readiness task-1 --host codex --repo " + repo,
    ]
    assert "TASK" in _layout(readiness(sample_repo, "codex"))["next"][0]
    # The named inspect/reconcile pair is what readiness then observes as resolved.
    plan = inspect(sample_repo, ["packages/b/node_modules"])
    reconcile(plan, digest(plan))
    assert _layout(readiness(sample_repo, "codex", task_id="task-1"))["status"] == "PASS"
