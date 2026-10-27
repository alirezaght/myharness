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
        lambda executable, host: {
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
