"""Bounded read-only AL documents from the exact protected execution pin."""

import hashlib
import json
import stat
from pathlib import Path


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _read(path, maximum):
    path = Path(path)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError("pinned resource symlink refused")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError("pinned resource must be a bounded regular file")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    after = path.stat()
    if (
        any(
            getattr(before, k) != getattr(after, k)
            for k in ["st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode"]
        )
        or len(raw) > maximum
    ):
        raise ValueError("pinned resource moved during read")
    return raw, before


def read_skill(initialization, host, resource, *, offset=0, limit=16384):
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 65536:
        raise ValueError("bounded pinned resource cursor required")
    root = Path(initialization["pin_root"])
    raw, _ = _read(root / "manifest.json", 4 * 1024 * 1024)
    manifest = json.loads(raw)
    identity = manifest.get("pin_identity")
    expected = hashlib.sha256(
        _canonical({k: v for k, v in manifest.items() if k != "pin_identity"})
    ).hexdigest()
    if identity != root.name or identity != expected or manifest.get("platform") != host:
        raise ValueError("pinned resource manifest identity differs")
    binding_raw, _ = _read(Path(initialization["binding_path"]), 4 * 1024 * 1024)
    binding = json.loads(binding_raw)
    if binding.get("pin") != {
        "pin_identity": identity,
        "pin_manifest_digest": hashlib.sha256(raw).hexdigest(),
    }:
        raise ValueError("pinned resource is not the execution protected pin")
    if resource == "harness:skill":
        relative = "SKILL.md"
    elif resource == "harness:skill/index":
        relative = None
    elif resource.startswith("harness:skill/reference/"):
        relative = resource[len("harness:skill/") :]
        if (
            ".." in Path(relative).parts
            or "\\" in relative
            or ":" in relative
            or relative != Path(relative).as_posix()
            or Path(relative).suffix not in {".md", ".json"}
        ):
            raise ValueError("invalid pinned skill reference path")
    else:
        raise ValueError("unsupported pinned skill resource")
    resources = manifest["resource_map"]
    if relative is None:
        names = sorted(
            k
            for k in resources
            if k == "SKILL.md" or k.startswith("reference/") and Path(k).suffix in {".md", ".json"}
        )
        content = json.dumps(
            {
                "pin_identity": identity,
                "resources": [
                    "harness:skill" if k == "SKILL.md" else "harness:skill/" + k for k in names
                ],
            },
            sort_keys=True,
        ).encode()
        logical = "resource-index"
    else:
        logical = resources.get(relative)
        prefix = host + "/skills/al/"
        if logical != prefix + relative:
            raise ValueError("reference is absent from the verified execution pin")
        expected_row = manifest["complete_file_manifest"].get(logical)
        if not expected_row:
            raise ValueError("pinned resource membership is missing")
        content, info = _read(root / "payload" / logical, 4 * 1024 * 1024)
        if expected_row != {
            "kind": "file",
            "mode": stat.S_IMODE(info.st_mode),
            "sha256": hashlib.sha256(content).hexdigest(),
        }:
            raise ValueError("pinned resource bytes differ")
    return {
        "text": content[offset : offset + limit].decode(errors="replace"),
        "offset": offset,
        "truncated": offset + limit < len(content),
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "pin_identity": identity,
        "resource": resource,
    }
