"""Same-path installer prefixes preserve isolation and fail before unsafe restore."""

import os

import pytest

from al.tests import update_templates as T


def seeded(root):
    (root / "state").mkdir()
    (root / "state" / "record").write_bytes(b"genuine prerequisite bytes")
    (root / "state" / "record").chmod(0o600)
    (root / "link").symlink_to("state/record")
    return {"root": root, "nested": {"values": [1]}}


def prefix(tmp_path):
    container = tmp_path / "prefix"
    container.mkdir(mode=0o700)
    return T.build(container, seeded)


def test_restores_complete_same_path_tree_and_independent_return_data(tmp_path):
    calls = []
    container = tmp_path / "prefix"
    container.mkdir(mode=0o700)

    def factory(root):
        calls.append(root)
        return seeded(root)

    template = T.build(container, factory)
    original = T._digest(template.backup)
    first = T.restore(template)
    assert calls == [template.active] and first["root"] == template.active
    assert not T._files(template.active) & T._files(template.backup)
    first["nested"]["values"].append(2)
    (first["root"] / "state" / "record").write_bytes(b"changed")
    (first["root"] / "state" / "record").chmod(0o777)
    (first["root"] / "created").write_text("remove me")
    (first["root"] / "link").unlink()
    assert T._digest(template.backup) == original
    second = T.restore(template)
    assert second == {"root": template.active, "nested": {"values": [1]}}
    assert T._digest(template.active) == original == T._digest(template.backup)
    assert not (template.active / "created").exists()
    assert os.readlink(template.active / "link") == "state/record"
    assert not T._files(template.active) & T._files(template.backup)


def test_changed_backup_refuses_before_modifying_active_tree(tmp_path):
    template = prefix(tmp_path)
    active_before = T._digest(template.active)
    (template.backup / "state" / "record").write_bytes(b"tampered pristine data")
    with pytest.raises(ValueError, match="pristine fixture prefix was modified"):
        T.restore(template)
    assert T._digest(template.active) == active_before


def test_symlinks_are_copied_without_following_external_targets(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "untouched").write_text("outside the fixture")
    container = tmp_path / "prefix"
    container.mkdir(mode=0o700)

    def factory(root):
        (root / "external").symlink_to(external, target_is_directory=True)
        return {"root": root}

    template = T.build(container, factory)
    T.restore(template)
    assert os.readlink(template.active / "external") == str(external)
    assert (external / "untouched").read_text() == "outside the fixture"


def test_replaced_active_directory_refuses_without_following_it(tmp_path):
    template = prefix(tmp_path)
    template.active.rename(template.container / "previous-active")
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    (external / "untouched").write_text("keep")
    template.active.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="private owned directory"):
        T.restore(template)
    assert (external / "untouched").read_text() == "keep"


def test_hardlinked_backup_refuses_without_changing_active(tmp_path):
    template = prefix(tmp_path)
    target = template.backup / "state" / "record"
    target.unlink()
    target.hardlink_to(template.active / "state" / "record")
    before = T._digest(template.active)
    with pytest.raises(ValueError, match="aliased"):
        T.restore(template)
    assert T._digest(template.active) == before


def test_failed_construction_does_not_publish_a_pristine_prefix(tmp_path):
    container = tmp_path / "prefix"
    container.mkdir(mode=0o700)

    def failed(root):
        (root / "partial").write_text("incomplete")
        raise RuntimeError("fixture construction failed")

    with pytest.raises(RuntimeError, match="fixture construction failed"):
        T.build(container, failed)
    assert not (container / "pristine").exists()


def test_restore_copy_failure_preserves_active_and_pristine(tmp_path, monkeypatch):
    template = prefix(tmp_path)
    before = T._digest(template.active)

    def failed(source, destination):
        destination.mkdir()
        raise OSError("fixture copy interrupted")

    monkeypatch.setattr(T, "_copy", failed)
    with pytest.raises(OSError, match="copy interrupted"):
        T.restore(template)
    assert T._digest(template.active) == before == T._digest(template.backup)
    with pytest.raises(ValueError, match="staging is unsettled"):
        T.restore(template)


def test_live_or_unsupported_return_values_are_not_cached(tmp_path):
    container = tmp_path / "prefix"
    container.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="live or unsupported"):
        T.build(container, lambda root: {"live": object()})
    assert not (container / "pristine").exists()
