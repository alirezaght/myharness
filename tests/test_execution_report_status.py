"""Status surfaces verified named reports without rewriting their immutable store."""

import json

import pytest

from harness.execution_artifacts import ArtifactStore, output_reports


def test_named_reports_are_hashed_metadata_and_observation_is_read_only(tmp_path):
    directory = tmp_path / "drafts"
    store = ArtifactStore(directory)
    principal = {"thread_id": "architect-1", "role": "al-architect"}
    result = store.submit_output(principal, "blocker.md", "Explicit blocker report", 2, 2)
    before = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir() if p.is_file()
    }
    rows = output_reports(directory)
    assert rows == [
        {
            "owner": "architect-1",
            "name": "blocker.md",
            "role": "al-architect",
            "artifact_id": result["artifact_id"],
            "sha256": result["sha256"],
            "size": len("Explicit blocker report"),
            "resource": f"artifact:{result['artifact_id']}:{result['sha256']}",
        }
    ]
    assert "Explicit blocker report" not in json.dumps(rows)
    assert before == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir() if p.is_file()
    }


def test_report_status_refuses_tampered_content_and_foreign_paths(tmp_path):
    store = ArtifactStore(tmp_path / "drafts")
    result = store.submit_output(
        {"thread_id": "root", "role": "orchestrator"}, "final.md", "Final report", 0, 0
    )
    path = store.resolve(result["artifact_id"], result["sha256"])
    path.write_text("Changed report")
    with pytest.raises(ValueError, match="provenance changed"):
        output_reports(store.directory)
    path.write_text("Final report")
    outside = tmp_path / "outside.md"
    path.rename(outside)
    index = store.directory / "index.json"
    value = json.loads(index.read_bytes())
    value["records"][result["artifact_id"]]["path"] = str(outside)
    index.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="provenance changed"):
        output_reports(store.directory)
