"""Pin execution imports to the running harness, independently of target repository cwd."""

import os
import sys
from pathlib import Path

from harness.execution_contract import BOOTSTRAP


def module_argv(module):
    if module not in {
        "harness.execution_authority",
        "harness.execution_worker",
        "harness.execution_prepared_authority",
    }:
        raise ValueError("unsupported trusted execution module")
    return [sys.executable, "-I", "-c", BOOTSTRAP, module]


def execution_environment():
    return dict(os.environ, HARNESS_AUTHORITY_ROOT=str(Path(__file__).resolve().parent.parent))


def authority_snapshot(directory, *, versioned=False):
    """Copy the fixed bridge outside the target's writable implementation tree."""
    import hashlib
    import json

    if not versioned and (Path(directory) / "bridge-selection.json").exists():
        from harness.execution_bridge import selected_bridge

        return str(selected_bridge(directory))
    source = Path(__file__).resolve().parent
    destination = Path(directory) / "trusted" / "harness"
    files = {
        str(p.relative_to(source)): p.read_bytes()
        for p in source.rglob("*")
        if p.is_file()
        and (
            len(p.relative_to(source).parts) == 1
            and p.suffix in {".py", ".gz"}
            or p.parent == source / "migrations"
            and p.suffix == ".sql"
        )
    }
    fingerprint = hashlib.sha256(
        b"".join(k.encode() + v for k, v in sorted(files.items()))
    ).hexdigest()
    if versioned:
        destination = Path(directory) / "bridge-revisions" / fingerprint / "trusted/harness"
    marker = destination.parent / "manifest.json"
    if marker.exists():
        manifest = json.loads(marker.read_bytes())
        for name, sha in manifest["files"].items():
            if hashlib.sha256((destination / name).read_bytes()).hexdigest() != sha:
                raise ValueError("trusted authority snapshot changed")
        return str(destination.parent)
    destination.mkdir(parents=True, mode=0o700)
    for name, raw in files.items():
        (destination / name).parent.mkdir(parents=True, exist_ok=True)
        (destination / name).write_bytes(raw)
        (destination / name).chmod(0o600)
    marker.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
            }
        )
    )
    return str(destination.parent)


def native_environment(profile, transport, trusted_root):
    """Only native model transport credentials cross the process boundary."""
    allowed = {"PATH", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL"}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env.update(
        HOME=profile.native_home,
        TMPDIR=profile.temporary,
        CODEX_HOME=profile.native_home,
        CLAUDE_CONFIG_DIR=profile.native_home,
        HARNESS_AUTHORITY_ROOT=trusted_root,
        ANTHROPIC_BASE_URL=transport["url"],
        ANTHROPIC_AUTH_TOKEN=transport["token"],
        OPENAI_API_KEY=transport["token"],
        OPENAI_BASE_URL=transport["url"] + "/v1",
    )
    return env


def native_assets(profile, initialization, host):
    """Pin native role and skill lookup under the isolated home across resume."""
    import shutil

    payload = Path(initialization["pin_root"]) / "payload" / host
    home = Path(profile.native_home)
    pairs = [(payload / "skills/al", home / "skills/al")]
    role_source = payload / "agents"
    pairs.append((role_source, home / ("agents/al" if host == "claude" else "agents")))
    for source, destination in pairs:
        if not source.is_dir():
            raise ValueError("pinned native role/skill resources unavailable")
        if destination.exists():
            expected = {
                p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
            }
            actual = {
                p.relative_to(destination): p.read_bytes()
                for p in destination.rglob("*")
                if p.is_file()
            }
            if expected != actual:
                raise ValueError("isolated native role/skill resources changed")
        else:
            shutil.copytree(source, destination)


def prepared_authority_snapshot(directory):
    """Record a private fixed authority endpoint and its actual interpreter identity."""
    import hashlib
    import json
    import stat

    from harness.execution_handoff import durable_file

    base = Path(directory) / "prepared-authority"
    root = Path(authority_snapshot(base))
    executable = Path(sys.executable).resolve(strict=True)
    before = executable.stat()
    hasher = hashlib.sha256()
    with executable.open("rb") as stream:
        while raw := stream.read(1024 * 1024):
            hasher.update(raw)
    after = executable.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("prepared authority interpreter changed")
    value = {
        "schema_version": 1,
        "kind": "prepared-harness-authority-v1",
        "snapshot": str(root),
        "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "interpreter": {
            "executable": str(executable),
            "sha256": hasher.hexdigest(),
            "mode": stat.S_IMODE(after.st_mode),
        },
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path = base / "authority.json"
    durable_file(path, raw)
    return {"stored_path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
