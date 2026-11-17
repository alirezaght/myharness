"""Sealed execution imports must include source-backed knowledge operations."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.execution_bootstrap import authority_snapshot
from harness.execution_bridge import verify_snapshot
from harness.execution_models import ExecutionConflict

_QUERY = r"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from harness.db import connect
from harness.knowledge.extract import extract_source
from harness.knowledge import lessons, retention, commit_sources
from harness.knowledge.models import (
    EXTRACTOR_VERSION, ContextRequest, PreparedSnapshot, QueryLimits,
    SourceInventory, SourceVersion, digest, identity,
)
from harness.knowledge.policy import operator_scope
from harness.knowledge.query import explain, query
from harness.knowledge.store import publish_snapshot

root = Path(sys.argv[2])
scope = operator_scope(root)
raw = (root / 'service.py').read_bytes()
source_id = identity(scope.project_id, 'service.py')
sha = digest(raw)
source = SourceVersion(source_id, identity(source_id, sha, EXTRACTOR_VERSION, "python"),
                       'service.py', 'python', sha, raw, {'fixture': True})
with connect(root / 'fixture.db') as conn:
    publish_snapshot(conn, PreparedSnapshot(
        scope, SourceInventory((source,), True, (), {'fixture': True}),
        (extract_source(source),), 0), expected_active=None)
    bundle = query(conn, scope, ContextRequest('calculate', limits=QueryLimits(bytes=4096)))
    assert bundle.payload['assertions'] and bundle.payload['citations']
    aid = bundle.payload['assertions'][0]['assertion_id']
    explanation = explain(conn, scope, bundle.bundle_hash, aid)
    assert explanation['citations'][0]['content_sha256'] == sha
    assert len(json.dumps(bundle.to_dict()).encode()) < 4096
    for imported in (lessons, retention, commit_sources):
        assert Path(imported.__file__).is_relative_to(Path(sys.argv[1]))
    module = sys.modules['harness.knowledge.query']
    assert Path(module.__file__).is_relative_to(Path(sys.argv[1]))
    print(json.dumps({'assertions': len(bundle.payload['assertions']),
                      'citations': len(explanation['citations']),
                      'module': module.__file__}))
"""


@pytest.mark.parametrize("versioned", [False, True])
def test_snapshot_runs_bounded_sourced_knowledge_query(tmp_path, versioned):
    root = Path(authority_snapshot(tmp_path / "runtime", versioned=versioned))
    repo = tmp_path / "unrelated-working-directory"
    repo.mkdir()
    (repo / "service.py").write_text("def calculate():\n    return 3\n")
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _QUERY, str(root), str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["assertions"] and payload["citations"]
    assert verify_snapshot(root)
    members = json.loads((root / "manifest.json").read_text())["files"]
    assert "knowledge/query.py" in members
    assert all("__pycache__" not in name and "dashboard/" not in name for name in members)


@pytest.mark.parametrize("versioned", [False, True])
def test_snapshot_reuse_and_integrity(tmp_path, versioned):
    directory = tmp_path / "runtime"
    root = Path(authority_snapshot(directory, versioned=versioned))
    manifest = (root / "manifest.json").read_bytes()
    assert authority_snapshot(directory, versioned=versioned) == str(root)
    assert (root / "manifest.json").read_bytes() == manifest
    original = (root / "harness/db.py").read_bytes()
    (root / "harness/db.py").write_bytes(original + b"\n# changed\n")
    with pytest.raises(ValueError, match="snapshot changed"):
        authority_snapshot(directory, versioned=versioned)
    (root / "harness/db.py").write_bytes(original)
    (root / "harness/unlisted.py").write_text("raise RuntimeError('unlisted')\n")
    with pytest.raises(ExecutionConflict, match="membership differs"):
        verify_snapshot(root)


def test_existing_legacy_snapshot_is_not_extended_in_place(tmp_path):
    root = Path(authority_snapshot(tmp_path))
    manifest = json.loads((root / "manifest.json").read_text())
    for name in list(manifest["files"]):
        if name.startswith("knowledge/"):
            (root / "harness" / name).unlink()
            del manifest["files"][name]
    (root / "harness/knowledge").rmdir()
    manifest["fingerprint"] = hashlib.sha256(
        b"".join(
            name.encode() + (root / "harness" / name).read_bytes()
            for name in sorted(manifest["files"])
        )
    ).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    previous = (root / "manifest.json").read_bytes()
    assert verify_snapshot(root) == manifest["fingerprint"]
    assert authority_snapshot(tmp_path) == str(root)
    assert (root / "manifest.json").read_bytes() == previous
    assert not (root / "harness/knowledge").exists()
    successor = Path(authority_snapshot(tmp_path, versioned=True))
    assert successor != root
    assert (successor / "harness/knowledge/query.py").is_file()
    assert verify_snapshot(successor)
