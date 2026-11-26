"""The launcher's runtime pin verification cache skips only an unchanged payload re-hash (#36)."""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from al.installer import runtime_pin, runtime_pin_cache
from al.installer.runtime_pin import prepare_pin, verify_pin
from al.installer.runtime_pin_cache import record_path, verify_pin_cached
from al.installer.validator_origin import canonical, digest
from al.installer.bootstrap import bootstrap_cli
from al.installer.validator_origin import capture_canonical, verify_snapshot
from al.tests.runtime_run_helpers import _environment, invoke

MEMBER = "codex/skills/al/scripts/al_state.py"
_PIN = {}


def _distribution(root):
    """Small distribution for payload/cache decisions; no application code is executed."""
    layout = {
        "schema_version": 1,
        "platforms": ["codex"],
        "package": "skills/al",
        "package_roots": ["SKILL.md", "scripts", "reference"],
        "roles": ["al-engineer"],
        "required_scripts": ["al_state.py"],
        "required_claude_scripts": [],
        "exclude_parts": ["__pycache__"],
        "exclude_suffixes": [".pyc"],
        "prerequisites": {"python": "3.11", "git": "2.30"},
        "runtime_schema": 2,
    }
    files = {
        "release-layout.json": json.dumps(layout),
        "alctl.py": '"""Synthetic bootstrap resource."""\n',
        "installer/fixture.py": "VALUE = 1\n",
        "codex/agents/al-engineer.toml": 'name = "al-engineer"\n',
        "codex/skills/al/SKILL.md": "# Synthetic pin decision fixture\n",
        "codex/skills/al/reference/guide.md": "# Reference resource\n",
        MEMBER: "VALUE = 1\n",
        "codex/skills/al/scripts/nested/helper.py": "VALUE = 2\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def pinned(tmp_path_factory, tmp_path, monkeypatch):
    """A private copy of a genuinely prepared small pin and a payload counter."""
    if "template" not in _PIN:
        base = tmp_path_factory.mktemp("pin-cache-template")
        distribution = _distribution(base / "distribution")
        storage = base / "al-run-pins/pins"
        storage.parent.mkdir(mode=0o700)
        binding = prepare_pin(distribution, "codex", storage)
        _PIN.update(template=storage, binding=binding)
    storage = tmp_path / "al-run-pins/pins"
    storage.parent.mkdir(mode=0o700)
    shutil.copytree(_PIN["template"], storage, symlinks=True)
    # The copy is new; the coarse-clock guard is exercised by its own test.
    monkeypatch.setattr(runtime_pin_cache, "RACY_NS", 0)
    calls = []
    original = runtime_pin.verify_payload

    def counted(root, files):
        calls.append(root)
        return original(root, files)

    monkeypatch.setattr(runtime_pin, "verify_payload", counted)
    return storage, dict(_PIN["binding"]), calls


def _member(storage, binding, name=MEMBER):
    return storage / binding["pin_identity"] / "payload" / name


def _cached(storage, binding):
    first = verify_pin_cached(storage, binding)
    assert record_path(storage, binding["pin_identity"]).is_file()
    return first


def test_cache_hit_skips_the_full_payload_verification(pinned):
    storage, binding, calls = pinned
    first = verify_pin_cached(storage, binding)
    assert len(calls) == 1 and first["bytes"]
    path = record_path(storage, binding["pin_identity"])
    assert path.parent == storage.parent / "pin-verification"
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert oct(path.parent.stat().st_mode & 0o777) == oct(0o700)
    record = json.loads(path.read_bytes())
    assert record["files"] == first["manifest"]["complete_file_manifest"]

    second = verify_pin_cached(storage, binding)
    assert len(calls) == 1
    assert second["manifest"] == first["manifest"] and second["root"] == first["root"]
    assert second["bytes"] is None
    # The run child passes the bytes it already hashed; they come back unchanged.
    third = verify_pin_cached(storage, binding, captured=first["bytes"])
    assert len(calls) == 1 and third["bytes"] == first["bytes"]
    # Captured bytes that differ from the manifest are never vouched for by the cache.
    forged = dict(first["bytes"], **{MEMBER: first["bytes"][MEMBER] + b"\n"})
    assert verify_pin_cached(storage, binding, captured=forged)["bytes"] == first["bytes"]
    assert len(calls) == 2


def test_content_edit_with_preserved_mtime_and_size_misses(pinned):
    storage, binding, calls = pinned
    _cached(storage, binding)
    member = _member(storage, binding)
    before = member.stat()
    raw = member.read_bytes()
    time.sleep(0.01)
    os.chmod(member, 0o600)
    member.write_bytes(raw[:-1] + (b"#" if raw[-1:] != b"#" else b"!"))
    os.chmod(member, before.st_mode & 0o777)
    os.utime(member, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = member.stat()
    assert (after.st_size, after.st_mtime_ns, after.st_ino) == (
        before.st_size, before.st_mtime_ns, before.st_ino)
    with pytest.raises(ValueError, match="payload changed"):
        verify_pin_cached(storage, binding)
    assert len(calls) == 2


def test_replaced_file_with_identical_bytes_misses(pinned):
    storage, binding, calls = pinned
    _cached(storage, binding)
    member = _member(storage, binding)
    before = member.stat()
    replacement = member.with_name(".replacement")
    shutil.copy2(member, replacement)
    os.replace(replacement, member)
    assert member.stat().st_ino != before.st_ino
    verify_pin_cached(storage, binding)
    assert len(calls) == 2
    # The full verification passed, so the new facts are recorded and hit again.
    verify_pin_cached(storage, binding)
    assert len(calls) == 2


def test_permission_change_misses(pinned):
    storage, binding, calls = pinned
    _cached(storage, binding)
    member = _member(storage, binding)
    mode = member.stat().st_mode & 0o777
    member.chmod(mode ^ 0o100)
    with pytest.raises(ValueError, match="payload changed"):
        verify_pin_cached(storage, binding)
    member.chmod(mode)
    verify_pin_cached(storage, binding)
    assert len(calls) == 3
    directory = member.parent
    directory.chmod(0o755)
    verify_pin_cached(storage, binding)
    assert len(calls) == 4


@pytest.mark.parametrize("change", ["added", "removed", "added-directory"])
def test_added_or_removed_member_misses(pinned, change):
    storage, binding, calls = pinned
    _cached(storage, binding)
    member = _member(storage, binding)
    if change == "added":
        (member.parent / "al_unbound.py").write_text("raise RuntimeError('unbound')\n")
    elif change == "added-directory":
        (member.parent / "extra").mkdir()
    else:
        member.unlink()
    if change == "added-directory":
        # An empty directory is not a payload file; the full verification decides.
        verify_pin_cached(storage, binding)
    else:
        with pytest.raises(ValueError, match="payload"):
            verify_pin_cached(storage, binding)
    assert len(calls) == 2


def _rewrite(path, record):
    record = {k: v for k, v in record.items() if k != "record_digest"}
    record["record_digest"] = digest(canonical(record))
    path.write_bytes(canonical(record))


@pytest.mark.parametrize(
    "corruption",
    ["garbage", "truncated", "foreign-pin", "foreign-owner", "forged-digest", "other-files",
     "mode", "symlink", "directory-mode", "stale-facts"],
)
def test_corrupt_or_foreign_record_is_ignored(pinned, corruption, tmp_path):
    storage, binding, calls = pinned
    _cached(storage, binding)
    path = record_path(storage, binding["pin_identity"])
    record = json.loads(path.read_bytes())
    if corruption == "garbage":
        path.write_bytes(b"\x00not json")
    elif corruption == "truncated":
        path.write_bytes(path.read_bytes()[:100])
    elif corruption == "foreign-pin":
        _rewrite(path, dict(record, pin_identity="0" * 64))
    elif corruption == "foreign-owner":
        _rewrite(path, dict(record, owner=record["owner"] + 1))
    elif corruption == "forged-digest":
        record["quick_check"]["."][1] += 1
        path.write_bytes(canonical(record))
    elif corruption == "other-files":
        files = dict(record["files"])
        files[MEMBER] = dict(files[MEMBER], sha256="0" * 64)
        _rewrite(path, dict(record, files=files))
    elif corruption == "mode":
        path.chmod(0o644)
    elif corruption == "symlink":
        elsewhere = tmp_path / "elsewhere.json"
        shutil.copy2(path, elsewhere)
        path.unlink()
        path.symlink_to(elsewhere)
    elif corruption == "directory-mode":
        path.parent.chmod(0o755)
    else:
        record["quick_check"]["payload/" + MEMBER][8] -= 1
        _rewrite(path, record)
    verify_pin_cached(storage, binding)
    assert len(calls) == 2
    if corruption == "directory-mode":
        # Nothing is ever written into an unprivate directory.
        assert json.loads(path.read_bytes()) == record
        verify_pin_cached(storage, binding)
        assert len(calls) == 3
    else:
        # The full verification replaced the bad record with a good one.
        verify_pin_cached(storage, binding)
        assert len(calls) == 2


def test_failed_verification_is_never_cached(pinned):
    storage, binding, calls = pinned
    member = _member(storage, binding)
    original = member.read_bytes()
    member.write_bytes(original + b"\n")
    for _ in range(2):
        with pytest.raises(ValueError):
            verify_pin_cached(storage, binding)
    assert len(calls) == 2
    assert not record_path(storage, binding["pin_identity"]).exists()
    # A manifest the binding does not name is refused before the cache is consulted.
    member.write_bytes(original)
    _cached(storage, binding)
    manifest = storage / binding["pin_identity"] / "manifest.json"
    manifest.chmod(0o644)
    with pytest.raises(ValueError, match="manifest changed"):
        verify_pin_cached(storage, binding)
    manifest.chmod(0o600)
    with pytest.raises(ValueError, match="invalid protected runtime pin binding"):
        verify_pin_cached(storage, dict(binding, extra=1))


def test_payload_moving_during_full_verification_is_not_cached(pinned, monkeypatch):
    storage, binding, calls = pinned
    member = _member(storage, binding)
    original = runtime_pin.verify_payload

    def moving(root, files):
        result = original(root, files)
        os.utime(member, ns=(1, 1))
        return result

    monkeypatch.setattr(runtime_pin, "verify_payload", moving)
    verify_pin_cached(storage, binding)
    assert not record_path(storage, binding["pin_identity"]).exists()


def test_recently_written_pin_is_not_cached(pinned, monkeypatch):
    storage, binding, calls = pinned
    monkeypatch.setattr(runtime_pin_cache, "RACY_NS", 3600 * 10**9)
    verify_pin_cached(storage, binding)
    verify_pin_cached(storage, binding)
    assert len(calls) == 2
    assert not record_path(storage, binding["pin_identity"]).exists()


def test_readonly_and_unwritten_modes_never_create_the_record_directory(pinned):
    storage, binding, calls = pinned
    verify_pin_cached(storage, binding, readonly=True)
    verify_pin_cached(storage, binding, write=False)
    assert not record_path(storage, binding["pin_identity"]).parent.exists()
    assert len(calls) == 2


def test_install_update_rollback_and_doctor_paths_never_consult_the_cache(pinned, monkeypatch):
    storage, binding, calls = pinned
    _cached(storage, binding)

    def forbidden(*args, **kwargs):
        raise AssertionError("the verification cache was consulted")

    monkeypatch.setattr(runtime_pin_cache, "_record", forbidden)
    monkeypatch.setattr(runtime_pin_cache, "fingerprint", forbidden)
    # verify_pin is the verification of every non-dispatch caller (pin installation,
    # reviewed update/rollback, successor, validator and service); it always reads everything.
    assert verify_pin(storage, binding)["bytes"]
    assert verify_pin(storage, binding, readonly=True)["bytes"]
    assert len(calls) == 3
    from al.installer.doctor import doctor

    doctor(state_root=storage.parent.parent)
    # Only the launcher dispatch path and its run child use the cache.
    installer = Path(runtime_pin.__file__).parent
    users = sorted(
        path.name for path in installer.glob("*.py")
        if path.name != "runtime_pin_cache.py"
        and ("runtime_pin_cache" in path.read_text() or "verify_pin_cached" in path.read_text())
    )
    assert users == ["runtime_pin_boot.py", "runtime_run.py"]


def test_launcher_dispatch_uses_the_cache_and_old_pins_verify_in_full():
    from al.installer.runtime_pin_boot import CHILD

    assert "if 'installer/runtime_pin_cache.py' in captured:" in CHILD
    assert "verify_pin_cached(base/'pins',pin,captured=captured,write=False)" in CHILD
    assert "else:verified=verify_pin(base/'pins',pin)" in CHILD
    run = (Path(runtime_pin.__file__).parent / "runtime_run.py").read_text()
    assert "verify_pin(" not in run.replace("verify_pin_cached(", "")


@pytest.fixture
def readonly_launched(tmp_path):
    source = Path(__file__).resolve().parents[1]
    before = capture_canonical(source)["origin"]["closure"]
    home, repo = tmp_path / "readonly-home", tmp_path / "readonly-repo"
    home.mkdir(); repo.mkdir()
    env = _environment(home)
    for args in [("init", "-q"), ("-c", "user.name=fixture", "-c", "user.email=f@invalid", "commit", "--allow-empty", "-qm", "initial")]:
        result = invoke(["git", *args], repo, env)
        assert result.returncode == 0, result.stderr
    launcher = home / "bin/alctl"
    boot = bootstrap_cli(source, launcher, home / "installer")
    snapshot = Path(boot["snapshot"])
    verify_snapshot(snapshot, boot["validator_binding"])
    yield snapshot, home, repo, launcher, env
    assert capture_canonical(source)["origin"]["closure"] == before
    verify_snapshot(snapshot, boot["validator_binding"])


def test_public_commands_reuse_the_record_and_refuse_a_changed_pin(readonly_launched):
    launched = readonly_launched
    from al.tests.runtime_run_helpers import public_run, run_record, start

    start(launched)
    _, record = run_record(launched)
    _, home, _, _, _ = launched
    storage = home / "installer/al-run-pins/pins"
    path = record_path(storage, record["pin"]["pin_identity"])
    time.sleep(runtime_pin_cache.RACY_NS / 1e9 + 0.1)
    first = public_run(launched, "codex", "show", "--line")
    assert first.returncode == 0, first.stderr[-2000:]
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    written = path.stat()
    second = public_run(launched, "codex", "show", "--line")
    assert second.returncode == 0 and second.stdout.startswith("AL │"), second.stderr[-2000:]
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (written.st_ino, written.st_mtime_ns)
    member = _member(storage, record["pin"])
    mode = member.stat().st_mode & 0o777
    member.chmod(0o600)
    member.write_bytes(member.read_bytes() + b"\n")
    member.chmod(mode)
    refused = public_run(launched, "codex", "show", "--line")
    assert refused.returncode != 0
    assert "runtime pin payload changed" in refused.stdout + refused.stderr
