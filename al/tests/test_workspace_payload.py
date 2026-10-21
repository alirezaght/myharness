"""Workspace byte/payload primitives; full public deployment is tested separately."""

import copy
from pathlib import Path

import pytest

from al.installer import workspace_payload as P
from al.installer import workspace_source as S
from al.installer.registry import canonical, digest, write_json
from al.installer.validator_origin import capture
from al.tests.test_manifest import make_source


def source_bytes(root):
    contents, files, rows = {}, {}, []
    for path in sorted((root / "al").rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        raw, state = capture(path)
        files[name], contents[name] = state, raw
        rows.append(
            {
                "path": name,
                "worktree": {
                    "type": "file",
                    "sha256": state["sha256"],
                    "executable": bool(state["mode"] & 0o111),
                },
            }
        )
    # This helper is byte-level input, never a terminal approval or apply authority.
    source = {
        "digest": digest(canonical(files)),
        "files": files,
        "identity": {"root": str(root)},
        "content_digest": digest(canonical(files)),
    }
    return source, contents, rows


def test_capture_preserves_dirty_untracked_bytes_modes_and_refuses_drift(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    file = root / "module.py"
    file.write_bytes(b"value = 2\n")
    file.chmod(0o755)
    _, state = capture(file)
    rows = [
        {
            "path": "module.py",
            "worktree": {"type": "file", "sha256": state["sha256"], "executable": True},
        },
        {"path": "deleted.py", "worktree": {"type": "deleted"}},
    ]
    files, contents = S.capture_files(root, rows)
    assert files == {"module.py": state, "deleted.py": None}
    assert contents == {"module.py": b"value = 2\n"}
    file.chmod(0o644)
    with pytest.raises(ValueError, match="differs from approved"):
        S.capture_files(root, rows)
    file.chmod(0o755)
    file.write_bytes(b"value = 3\n")
    with pytest.raises(ValueError, match="differs from approved"):
        S.capture_files(root, rows)
    file.write_bytes(b"value = 2\n")
    (root / "deleted.py").write_bytes(b"new\n")
    with pytest.raises(ValueError, match="reappeared"):
        S.capture_files(root, rows)


@pytest.mark.parametrize("name", ["../escape", "/absolute", ".git/config", "x/../module.py"])
def test_unsafe_approved_input_is_never_a_copy_authority(tmp_path, name):
    with pytest.raises(ValueError, match="unsafe"):
        S.capture_files(tmp_path, [{"path": name, "worktree": {"type": "deleted"}}])


def test_source_symlink_duplicate_and_nonregular_refuse(tmp_path):
    (tmp_path / "link").symlink_to("/etc/passwd")
    row = {"path": "link", "worktree": {"type": "file", "sha256": "0" * 64, "executable": False}}
    with pytest.raises(ValueError, match="symlink"):
        S.capture_files(tmp_path, [row])
    row = {"path": "absent", "worktree": {"type": "deleted"}}
    with pytest.raises(ValueError, match="duplicate"):
        S.capture_files(tmp_path, [row, row])
    with pytest.raises(ValueError, match="regular"):
        S.capture_files(tmp_path, [{"path": "link", "worktree": {"type": "symlink"}}])


def test_workspace_payload_has_real_layout_and_no_tagged_provenance(tmp_path):
    root = make_source(tmp_path)
    source, contents, _ = source_bytes(root)
    changed = "al/codex/skills/al/SKILL.md"
    contents[changed] += b"\nApproved dirty bytes\n"
    source["files"][changed]["sha256"] = digest(contents[changed])
    source["content_digest"] = digest(canonical(source["files"]))
    manifest = P.build_payload(source, contents)
    assert P.validate_payload(manifest) == manifest
    assert not {"tag", "version", "tag_object", "commit"} & manifest.keys()
    assert manifest["kind"] == "approved-workspace-payload-v1"
    assert manifest["files"][changed[3:]]["sha256"] == digest(contents[changed])
    assert all("/tests/" not in name and "__pycache__" not in name for name in manifest["files"])
    cache = tmp_path / "cache"
    for name, state in manifest["files"].items():
        file = cache / "payload" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(contents["al/" + name])
        file.chmod(state["mode"])
    write_json(cache / "manifest.json", manifest)
    assert P.verify_payload(cache, manifest["digest"]) == manifest
    (cache / "extra").write_text("not reviewed")
    with pytest.raises(ValueError, match="membership"):
        P.verify_payload(cache, manifest["digest"])
    (cache / "extra").unlink()
    selected = cache / "payload" / changed[3:]
    selected.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="resource changed"):
        P.verify_payload(cache, manifest["digest"])


def test_workspace_manifest_does_not_accept_forged_tag_or_incomplete_layout(tmp_path):
    root = make_source(tmp_path)
    source, contents, _ = source_bytes(root)
    manifest = P.build_payload(source, contents)
    forged = dict(manifest, tag="v1.0.0")
    forged["digest"] = digest(canonical({k: v for k, v in forged.items() if k != "digest"}))
    with pytest.raises(ValueError, match="identity"):
        P.validate_payload(forged)
    missing = copy.deepcopy(manifest)
    del missing["files"]["codex/skills/al/SKILL.md"]
    missing["digest"] = digest(canonical({k: v for k, v in missing.items() if k != "digest"}))
    with pytest.raises(ValueError, match="incomplete"):
        P.validate_payload(missing)
    contents["al/codex/skills/al/SKILL.md"] = b"unapproved"
    with pytest.raises(ValueError, match="not an approved"):
        P.build_payload(source, contents)
