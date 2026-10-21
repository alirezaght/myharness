"""Closed workspace requests and hash-bound review artifacts, never approval flags."""

from pathlib import Path

from .engine import R
from .registry import canonical, digest
from .validator_origin import capture, decode

FIELDS = {
    "home",
    "session",
    "run",
    "platform",
    "reviews",
    "adoption",
    "runtime_roots",
    "historical",
    "orphan_review",
}


def artifact(reference):
    if reference is None:
        return {"reference": None, "value": {}}
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256", "mode"}:
        raise ValueError("workspace review requires an exact artifact reference")
    path = R.safe_absolute(reference["path"])
    raw, state = capture(path)
    if state != {k: reference[k] for k in ("sha256", "mode")}:
        raise ValueError("workspace review artifact bytes or mode changed")
    value = decode(raw)
    if not isinstance(value, dict):
        raise ValueError("workspace review artifact must be an object")
    return {"reference": dict(reference), "value": value}


def request(value):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("unsupported workspace request fields")
    if not {"home", "session", "run", "platform"} <= set(value):
        raise ValueError("explicit workspace home and invoking run required")
    if value["platform"] not in {"codex", "claude"}:
        raise ValueError("workspace platform required")
    import re

    if any(
        not isinstance(value[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value[k])
        for k in ("session", "run")
    ):
        raise ValueError("invalid workspace invoking identity")
    roots = value.get("runtime_roots", [])
    if not isinstance(roots, list):
        raise ValueError("workspace runtime roots must be additive descriptors")
    home = R.safe_absolute(Path(value["home"]).resolve())
    if not home.is_dir():
        raise ValueError("workspace home must already exist")
    return {
        "home": str(home),
        "session": value["session"],
        "run": value["run"],
        "platform": value["platform"],
        "runtime_roots": roots,
        "reviews": artifact(value.get("reviews")),
        "adoption": artifact(value.get("adoption")),
        "historical": value.get("historical"),
        "orphan_review": value.get("orphan_review"),
    }


def identity(value):
    """Bind prepared byte fields without publishing private configuration contents."""
    if isinstance(value, bytes):
        return {"kind": "prepared-bytes", "sha256": digest(value), "size": len(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("unsupported prepared identity key")
        return {k: identity(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [identity(v) for v in value]
    if value is None or type(value) in {str, int, bool}:
        return value
    raise ValueError("unsupported workspace prepared identity")


def sealed(value):
    return dict(value, digest=digest(canonical(value)))
