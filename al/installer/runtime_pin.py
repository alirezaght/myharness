"""Private content-addressed runtime pins; a pin is not run authority."""

import os
import shutil
import stat
import tempfile
from pathlib import Path

from .interpreter_execution import capture_profile, validate_profile
from .registry import write_json
from .runtime_pin_capture import relative, stable_distribution
from .validator_origin import canonical, capture, decode, digest, interpreter_identity, safe

FIELDS = {
    "schema_version",
    "kind",
    "distribution_identity",
    "platform",
    "complete_file_manifest",
    "resource_map",
    "interpreter",
    "interpreter_execution_identity",
    "closure_digest",
    "pin_identity",
}


def _directory(path, *, create=True):
    path = safe(path)
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = path.stat()
    if (
        not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.getuid()
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        raise ValueError("runtime pin storage must be private and owned")
    return path


def _sync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_pin(trusted_distribution, platform, storage):
    """Publish only captured bytes; the caller independently retains the returned binding."""
    identity, files, contents, resources = stable_distribution(trusted_distribution, platform)
    interpreter = interpreter_identity()
    profile = capture_profile(interpreter["executable"], interpreter, interpreter["executable"])
    manifest = {
        "schema_version": 1,
        "kind": "runtime-pin-v1",
        "distribution_identity": identity,
        "platform": platform,
        "complete_file_manifest": files,
        "resource_map": resources,
        "interpreter": interpreter,
        "interpreter_execution_identity": profile,
        "closure_digest": digest(canonical(files)),
    }
    manifest["pin_identity"] = digest(canonical(manifest))
    storage = _directory(storage)
    destination = storage / manifest["pin_identity"]
    binding = {
        "pin_identity": manifest["pin_identity"],
        "pin_manifest_digest": digest(canonical(manifest) + b"\n"),
    }
    if destination.exists():
        verify_pin(storage, binding)
        return binding
    stage = Path(tempfile.mkdtemp(prefix=".pin-", dir=storage))
    try:
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        for name, raw in contents.items():
            path = payload / relative(name)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fchmod(stream.fileno(), files[name]["mode"])
                os.fsync(stream.fileno())
        write_json(stage / "manifest.json", manifest)
        if (
            stable_distribution(trusted_distribution, platform)
            != (identity, files, contents, resources)
            or interpreter_identity() != interpreter
        ):
            raise ValueError("runtime distribution or interpreter moved before publication")
        for directory, _, _ in os.walk(stage, topdown=False):
            _sync(directory)
        try:
            os.rename(stage, destination)
        except FileExistsError:
            verify_pin(storage, binding)
        _sync(storage)
        verify_pin(storage, binding)
        return binding
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_pin(storage, binding, *, readonly=False):
    if not isinstance(binding, dict) or set(binding) != {"pin_identity", "pin_manifest_digest"}:
        raise ValueError("invalid protected runtime pin binding")
    pin = binding["pin_identity"]
    if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
        raise ValueError("invalid runtime pin identity")
    root = _directory(storage, create=not readonly) / pin
    safe(root)
    raw, metadata = capture(root / "manifest.json")
    if metadata["mode"] != 0o600 or digest(raw) != binding["pin_manifest_digest"]:
        raise ValueError("protected runtime pin manifest changed")
    manifest = decode(raw)
    if (
        set(manifest) != FIELDS
        or manifest["schema_version"] != 1
        or manifest["kind"] != "runtime-pin-v1"
    ):
        raise ValueError("unsupported runtime pin manifest")
    if (
        manifest["pin_identity"] != pin
        or digest(canonical({k: v for k, v in manifest.items() if k != "pin_identity"})) != pin
    ):
        raise ValueError("runtime pin identity differs")
    files = manifest["complete_file_manifest"]
    if manifest["closure_digest"] != digest(canonical(files)):
        raise ValueError("runtime pin closure differs")
    validate_profile(manifest["interpreter_execution_identity"], manifest["interpreter"])
    if set(p.name for p in root.iterdir()) != {"payload", "manifest.json"}:
        raise ValueError("runtime pin metadata membership differs")
    contents = {}
    for directory, dirs, names in os.walk(root / "payload", followlinks=False):
        for name in dirs + names:
            safe(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            logical = path.relative_to(root / "payload").as_posix()
            raw, state = capture(path)
            if logical not in files or dict(state, kind="file") != files[logical]:
                raise ValueError("runtime pin payload changed")
            contents[logical] = raw
    if set(contents) != set(files):
        raise ValueError("runtime pin payload membership differs")
    if not isinstance(manifest["resource_map"], dict) or set(
        manifest["resource_map"].values()
    ) != set(files):
        raise ValueError("runtime pin resource coverage differs")
    for name in files:
        relative(name)
    return {"root": root, "manifest": manifest, "bytes": contents}
