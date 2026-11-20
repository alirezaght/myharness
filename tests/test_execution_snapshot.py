"""Sealed execution imports must include bounded trusted runtime packages."""

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

_RELIABILITY_IMPORT = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness.reliability import (contracts, identity, query, store, timeline_contracts,
                                 timeline_store, timeline_query, process_context, process_store)
root = Path(sys.argv[1]) / 'harness' / 'reliability'
assert all(Path(module.__file__).is_relative_to(root) for module in
           (contracts, identity, query, store, timeline_contracts, timeline_store,
            timeline_query, process_context, process_store))
assert contracts.COVERAGE['source'] == 'operation-dispatch'
print('trusted reliability package imported')
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
def test_snapshot_imports_bounded_reliability_package_from_trusted_tree(tmp_path, versioned):
    root = Path(authority_snapshot(tmp_path / "runtime", versioned=versioned))
    unrelated = tmp_path / "unrelated-working-directory"
    unrelated.mkdir()
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _RELIABILITY_IMPORT, str(root)],
        cwd=unrelated,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "trusted reliability package imported"
    assert verify_snapshot(root)
    members = json.loads((root / "manifest.json").read_text())["files"]
    assert {
        f"reliability/{name}"
        for name in (
            "__init__.py",
            "contracts.py",
            "identity.py",
            "store.py",
            "query.py",
            "timeline_contracts.py",
            "timeline_store.py",
            "timeline_query.py",
            "process_context.py",
            "process_store.py",
        )
    } <= members.keys()
    assert all("__pycache__" not in name for name in members)


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


def test_existing_snapshot_without_reliability_is_immutable_and_successor_is_separate(tmp_path):
    # Model an existing pinned inventory without reliability; this is not historical replay.
    root = Path(authority_snapshot(tmp_path))
    manifest = json.loads((root / "manifest.json").read_text())
    for name in list(manifest["files"]):
        if name.startswith("reliability/"):
            (root / "harness" / name).unlink()
            del manifest["files"][name]
    (root / "harness/reliability").rmdir()
    manifest["fingerprint"] = hashlib.sha256(
        b"".join(
            name.encode() + (root / "harness" / name).read_bytes()
            for name in sorted(manifest["files"])
        )
    ).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    before = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert verify_snapshot(root) == manifest["fingerprint"]
    assert authority_snapshot(tmp_path) == str(root)
    assert not (root / "harness/reliability").exists()
    successor = Path(authority_snapshot(tmp_path, versioned=True))
    assert successor != root
    assert verify_snapshot(successor)
    assert (successor / "harness/reliability/store.py").is_file()
    assert before == {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("versioned", [False, True])
def test_snapshot_imports_complete_explicit_policy_closure(tmp_path, versioned):
    from harness.execution_bootstrap import _POLICY_MODULES

    assert {
        "claims.py",
        "domains.py",
        "resource_bindings.py",
        "control_boundary.py",
        "control_git.py",
        "control_qualification.py",
        "control_channel.py",
        "control_sources_installer.py",
        "control_sources_codex.py",
        "control_sources_claude.py",
    } <= set(_POLICY_MODULES)
    root = Path(authority_snapshot(tmp_path / "policy-runtime", versioned=versioned))
    program = """
import importlib, json, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
for name in json.loads(sys.argv[2]):
    module = importlib.import_module('harness.policy.' + name.removesuffix('.py'))
    assert Path(module.__file__).is_relative_to(Path(sys.argv[1]) / 'harness/policy')
print('trusted policy closure imported')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(root), json.dumps(_POLICY_MODULES)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "trusted policy closure imported"
    assert verify_snapshot(root)
    members = json.loads((root / "manifest.json").read_bytes())["files"]
    assert {"policy/" + name for name in _POLICY_MODULES} <= members.keys()


def test_snapshot_uses_typed_exchange_modules_outside_checkout(tmp_path):
    root = Path(authority_snapshot(tmp_path / "runtime"))
    elsewhere = tmp_path / "empty-cwd"
    elsewhere.mkdir()
    code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness import agent_contracts as C, agent_exchange, agent_preexecution, agent_results
context = dict.fromkeys(C.CONTEXT - {'applicability'})
context['applicability'] = dict.fromkeys(context, 'pre-execution')
spec = {'reference': {'kind': 'task-request', 'id': 'actual-fixture',
                      'sha256': C.fingerprint('actual request')}, 'amendments': []}
value = C.make('requirement-analysis', context, spec, 'analysis', {'request': 'actual request'})
C.validate(value, context=context, specification=spec)
for module in (C, agent_exchange, agent_preexecution, agent_results):
    assert Path(module.__file__).is_relative_to(Path(sys.argv[1]))
print('trusted typed exchange used')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code, str(root)],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "trusted typed exchange used"
    assert verify_snapshot(root)
