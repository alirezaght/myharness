"""Workspace cache discrimination cannot turn arbitrary labels into tagged releases."""

import copy

import pytest

from al.installer import payload_provenance as P
from al.installer import workspace_payload as W
from al.installer.manifest import cache_release
from al.installer.registry import canonical, digest, write_json
from al.installer.source import resolve_release
from al.tests.test_manifest import make_source
from al.tests.test_workspace_payload import source_bytes


def payload(tmp_path):
    source = make_source(tmp_path)
    binding, contents, _ = source_bytes(source)
    manifest = W.build_payload(binding, contents)
    cache = tmp_path / "workspace-cache"
    for name, row in manifest["files"].items():
        path = cache / "payload" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents["al/" + name])
        path.chmod(row["mode"])
    write_json(cache / "manifest.json", manifest)
    return source, cache, manifest


def test_separate_workspace_and_tagged_cache_contracts(tmp_path):
    source, workspace, manifest = payload(tmp_path)
    assert P.payload_kind(manifest) == "workspace"
    assert P.display_version(manifest) == "workspace-" + manifest["source_content_digest"][:12]
    assert P.verify_expected(workspace, manifest) == manifest
    tagged, release = cache_release(
        resolve_release(str(source), version="1.0.0"), tmp_path / "state"
    )
    assert P.payload_kind(release) == "tagged"
    assert P.display_version(release) == "1.0.0"
    assert P.verify_expected(tagged, release) == release
    with pytest.raises(ValueError):
        P.verify_expected(workspace, release)
    with pytest.raises(ValueError):
        P.verify_expected(tagged, manifest)


def test_unknown_or_forged_provenance_never_falls_back_to_tagged(tmp_path):
    _, cache, manifest = payload(tmp_path)
    for field, value in [
        ("kind", "unknown"),
        ("source_digest", "x"),
        ("subdir", "../escape"),
        ("schema_version", True),
    ]:
        forged = copy.deepcopy(manifest)
        forged[field] = value
        forged["digest"] = digest(canonical({k: v for k, v in forged.items() if k != "digest"}))
        with pytest.raises(ValueError):
            P.payload_kind(forged)
        with pytest.raises(ValueError):
            P.verify_expected(cache, forged)
    (cache / "payload/codex/skills/al/SKILL.md").chmod(0o600)
    with pytest.raises(ValueError, match="resource changed"):
        P.verify_expected(cache, manifest)
