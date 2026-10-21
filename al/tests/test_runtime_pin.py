"""An immutable runtime pin remains independent from its installation."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from al.installer.runtime_pin import prepare_pin, verify_pin
from al.installer.runtime_pin_capture import stable_distribution

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def distribution(tmp_path):
    root = tmp_path / "distribution"
    shutil.copytree(
        SOURCE, root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache")
    )
    return root


@pytest.fixture
def prepared(distribution, tmp_path):
    storage = tmp_path / "pins"
    binding = prepare_pin(distribution, "codex", storage)
    return distribution, storage, binding


def test_pin_captures_complete_runtime_resources_and_survives_installation_removal(prepared):
    source, storage, binding = prepared
    first = verify_pin(storage, binding)
    manifest = first["manifest"]
    resources = manifest["resource_map"]
    assert "SKILL.md" in resources
    assert "scripts/al_state.py" in resources
    assert "reference/gates.md" in resources
    assert len([name for name in resources if name.startswith("roles/")]) == 7
    assert not any("/tests/" in name or "__pycache__" in name for name in first["bytes"])
    assert manifest["interpreter_execution_identity"]["image"]["sha256"]
    shutil.rmtree(source)
    second = verify_pin(storage, binding)
    assert second == first


@pytest.mark.parametrize(
    "mutation", ["bytes", "mode", "missing", "added", "symlink", "manifest-mode", "manifest-bytes"]
)
def test_independent_binding_refuses_pin_tampering(prepared, mutation):
    _, storage, binding = prepared
    root = storage / binding["pin_identity"]
    member = root / "payload/codex/skills/al/scripts/al_state.py"
    if mutation == "bytes":
        member.write_bytes(member.read_bytes() + b"\n")
    elif mutation == "mode":
        member.chmod((member.stat().st_mode & 0o777) ^ 0o100)
    elif mutation == "missing":
        member.unlink()
    elif mutation == "added":
        (member.parent / "al_unbound.py").write_text('raise RuntimeError("unbound")\n')
    elif mutation == "symlink":
        member.unlink()
        member.symlink_to("/dev/null")
    elif mutation == "manifest-mode":
        (root / "manifest.json").chmod(0o644)
    else:
        (root / "manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        verify_pin(storage, binding)


def test_unprotected_self_digest_does_not_replace_external_binding(prepared):
    _, storage, binding = prepared
    old = copy.deepcopy(binding)
    root = storage / binding["pin_identity"]
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["platform"] = "claude"
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert binding == old
    with pytest.raises(ValueError, match="manifest changed"):
        verify_pin(storage, binding)


@pytest.mark.parametrize(
    "mutation", ["missing-role", "extra-role", "missing-script", "source-link"]
)
def test_distribution_membership_is_proven_before_publication(distribution, tmp_path, mutation):
    if mutation == "missing-role":
        (distribution / "codex/agents/al-engineer.toml").unlink()
    elif mutation == "extra-role":
        (distribution / "codex/agents/al-unknown.toml").write_text("")
    elif mutation == "missing-script":
        (distribution / "codex/skills/al/scripts/al_state.py").unlink()
    else:
        path = distribution / "codex/skills/al/scripts/al_extra.py"
        path.symlink_to("/dev/null")
    with pytest.raises(ValueError):
        prepare_pin(distribution, "codex", tmp_path / "pins")
    assert not (tmp_path / "pins").exists()


def test_both_platforms_have_their_actual_roles_and_required_resources(distribution):
    for platform, suffix in [("codex", ".toml"), ("claude", ".md")]:
        identity, files, contents, resources = stable_distribution(distribution, platform)
        assert identity["platform"] == platform
        assert set(files) == set(contents) == set(resources.values())
        assert all(name.endswith(suffix) for name in resources if name.startswith("roles/"))
        if platform == "claude":
            assert "scripts/al_readonly_guard.py" in resources


def test_storage_cannot_be_unowned_or_world_readable(distribution, tmp_path):
    storage = tmp_path / "pins"
    storage.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private and owned"):
        prepare_pin(distribution, "codex", storage)
    assert not list(storage.iterdir())


def test_source_movement_between_capture_and_publication_leaves_no_usable_pin(
    distribution, tmp_path, monkeypatch
):
    from al.installer import runtime_pin

    original = runtime_pin.capture_profile

    def capture_then_change(*args):
        result = original(*args)
        path = distribution / "codex/skills/al/scripts/al_new_member.py"
        path.write_text("x = 1\n")
        return result

    monkeypatch.setattr(runtime_pin, "capture_profile", capture_then_change)
    storage = tmp_path / "pins"
    with pytest.raises(ValueError, match="moved before publication"):
        prepare_pin(distribution, "codex", storage)
    assert list(storage.iterdir()) == []
