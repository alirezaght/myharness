"""Historical requirements stay version-bound; current payloads remain strict."""
import copy
import json
from pathlib import Path

import pytest

from al.installer.manifest import build_manifest, cache_release, load_layout, validate_manifest, verify_cache
from al.installer.registry import canonical, digest
from al.installer.source import TAG, resolve_release
from al.tests.test_manifest import git_test

ROOT = Path(__file__).resolve().parents[2]
NEW_HELPERS = ("al_planning_rebind.py", "al_readonly_lines.py")
PUBLISHED_VERSIONS = tuple(
    tag[1:] for tag in git_test(ROOT, "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*").splitlines()
    if TAG.fullmatch(tag)
)
assert PUBLISHED_VERSIONS, "Compatibility tests require the genuine published release tags"


@pytest.mark.parametrize("version", ["0.1.0", "0.1.1", "0.1.3", "0.1.7", "0.1.8", "0.1.24"])
def test_genuine_historical_payloads_keep_their_original_manifest(version, tmp_path):
    release = resolve_release(str(ROOT), version=version)
    historical = json.loads(git_test(ROOT, "show", f"v{version}:al/release-layout.json"))
    expected = build_manifest(release, layout=historical)
    observed = build_manifest(release)
    assert observed == expected
    cache, manifest = cache_release(release, tmp_path / "registry")
    before = (cache / "manifest.json").read_bytes()
    assert verify_cache(cache, manifest["digest"]) == expected
    assert (cache / "manifest.json").read_bytes() == before


def test_historical_contract_still_requires_its_guard():
    release = resolve_release(str(ROOT), version="0.1.7")
    del release["tree"]["claude/skills/al/scripts/al_readonly_guard.py"]
    with pytest.raises(ValueError, match="missing required"):
        build_manifest(release)


@pytest.mark.parametrize("helper", NEW_HELPERS)
def test_current_release_cannot_omit_either_new_helper(helper):
    release = resolve_release(str(ROOT), version="0.1.24")
    release.update(version="0.1.25", tag="v0.1.25")
    current = ROOT / "al"
    for platform in ("codex", "claude"):
        for name in NEW_HELPERS:
            relative = f"{platform}/skills/al/scripts/{name}"
            release["tree"][relative] = {"raw": (current / relative).read_bytes(), "mode": 0o644}
    del release["tree"][f"codex/skills/al/scripts/{helper}"]
    with pytest.raises(ValueError, match="missing required"):
        build_manifest(release)


@pytest.mark.parametrize("version,tag", [("00.1.7", "v00.1.7"), ("0.1.7", "v0.1.8"), ("0.1.7-rc1", "v0.1.7-rc1"), (None, "v0.1.7")])
def test_invalid_release_identity_cannot_select_a_historical_contract(version, tag):
    release = resolve_release(str(ROOT), version="0.1.7")
    release.update(version=version, tag=tag)
    with pytest.raises(ValueError, match="release.*identity"):
        build_manifest(release)


def test_rehashed_malformed_manifest_cannot_claim_historical_exemption():
    release = resolve_release(str(ROOT), version="0.1.7")
    old_layout = json.loads(git_test(ROOT, "show", "v0.1.7:al/release-layout.json"))
    manifest = build_manifest(release, layout=old_layout)
    changed = copy.deepcopy(manifest)
    changed["tag"] = "v0.1.8"
    changed["digest"] = digest(canonical({k: v for k, v in changed.items() if k != "digest"}))
    with pytest.raises(ValueError, match="release.*identity"):
        validate_manifest(changed)


@pytest.mark.parametrize("helper", NEW_HELPERS)
def test_unversioned_workspace_still_requires_new_helpers(helper, tmp_path):
    from al.installer import workspace_payload as payload
    from al.tests.test_manifest import make_source
    from al.tests.test_workspace_payload import source_bytes

    source, contents, _ = source_bytes(make_source(tmp_path))
    manifest = payload.build_payload(source, contents)
    name = f"codex/skills/al/scripts/{helper}"
    del manifest["files"][name]
    manifest["digest"] = digest(canonical({k: v for k, v in manifest.items() if k != "digest"}))
    with pytest.raises(ValueError, match="incomplete workspace"):
        payload.validate_payload(manifest)
    del contents["al/" + name]
    with pytest.raises(ValueError, match="workspace payload lacks required"):
        payload.build_payload(source, contents)


@pytest.mark.parametrize("version,required", [("0.1.71", False), ("0.1.76", False), ("0.1.77", True),
                                              ("1.0.0", True), ("0.0.99", True)])
def test_planning_revision_helper_is_required_from_the_release_after_0_1_76(version, required):
    # Only the audited published 0.1 line keeps the historical omission; an unsupported
    # older line (0.0.99) or a later major requires the helper (PLANNING-REVISION-01).
    from al.installer.release_compatibility import required_scripts

    layout = load_layout()
    assert "al_planning_revision.py" in layout["required_scripts"]
    selected = required_scripts(layout, {"version": version, "tag": "v" + version})
    assert ("al_planning_revision.py" in selected) is required
    assert selected - {"al_planning_revision.py"} == set(layout["required_scripts"]) - {"al_planning_revision.py"}


@pytest.mark.parametrize("version", PUBLISHED_VERSIONS)
def test_each_published_contract_matches_the_genuine_layout(version):
    from al.installer.release_compatibility import required_scripts

    actual = json.loads(git_test(ROOT, "show", f"v{version}:al/release-layout.json"))
    current = load_layout()
    # Historic contracts must stay fixed when the next release adds a helper.
    if tuple(map(int, version.split('.'))) <= (0, 1, 24):
        current["required_scripts"] = [*current["required_scripts"], "future_required.py"]
    assert required_scripts(current, {"version": version, "tag": "v" + version}) == set(
        actual["required_scripts"]
    )
