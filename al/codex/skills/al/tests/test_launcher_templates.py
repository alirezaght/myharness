"""Runner-scoped runtime-pin templates: copied per session, re-verified, never shared in place."""
import json
import os

import launcher_context as L
from installer.runtime_pin import verify_pin


def _session(tmp_path, name):
    root = tmp_path / name
    root.mkdir(mode=0o700)
    return L.pinned_storage(root, "codex")


def test_later_sessions_copy_and_reverify_the_first_sessions_pin(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    templates.mkdir(mode=0o700)
    monkeypatch.setenv(L.TEMPLATES, str(templates))
    first_storage, first = _session(tmp_path, "first")
    published = [p for p in templates.iterdir() if p.is_dir()]
    assert len(published) == 1 and json.loads((published[0] / "binding.json").read_text()) == first

    second_storage, second = _session(tmp_path, "second")
    assert second == first and second_storage != first_storage
    verify_pin(second_storage / "pins", second)
    # Each session owns a separate copy: no file is shared with the template or the first session.
    a = first_storage / "pins" / first["pin_identity"] / "manifest.json"
    b = second_storage / "pins" / second["pin_identity"] / "manifest.json"
    c = published[0] / "pin" / "manifest.json"
    assert len({os.stat(p).st_ino for p in (a, b, c)}) == 3


def test_a_template_that_fails_verification_is_not_used(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    templates.mkdir(mode=0o700)
    monkeypatch.setenv(L.TEMPLATES, str(templates))
    _, first = _session(tmp_path, "first")
    published = next(p for p in templates.iterdir() if p.is_dir())
    payload = next(p for p in (published / "pin" / "payload").rglob("*.py"))
    payload.chmod(0o600)
    payload.write_text(payload.read_text() + "\n# tampered\n")
    storage, pin = _session(tmp_path, "second")
    assert pin != first
    verify_pin(storage / "pins", pin)
    assert [p.name for p in (storage / "pins").iterdir()] == [pin["pin_identity"]]


def test_without_a_runner_template_directory_each_session_prepares_its_own_pin(tmp_path, monkeypatch):
    monkeypatch.delenv(L.TEMPLATES, raising=False)
    _, first = _session(tmp_path, "first")
    _, second = _session(tmp_path, "second")
    assert first != second
