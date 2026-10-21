"""Content-addressed workspace payloads with no fabricated tag provenance."""

import os
from pathlib import Path
import shutil
import tempfile

from .engine import D, R, T
from .manifest import load_layout, required, runtime_schema, selected
from .registry import canonical, digest, namespace, read_json, write_json
from .workspace_source import revalidate_source


def build_payload(source, contents):
    layout = load_layout()
    files = {}
    for name, raw in contents.items():
        if not name.startswith("al/"):
            continue
        relative = name[3:]
        kind = selected(relative, layout)
        if kind is None:
            continue
        state = source["files"].get(name)
        if not state or digest(raw) != state["sha256"] or state["mode"] not in {0o644, 0o755}:
            raise ValueError("workspace resource is not an approved regular payload: " + name)
        files[relative] = dict(state, platform=kind[0], kind=kind[1])
    if required(layout) - files.keys():
        raise ValueError("workspace payload lacks required resources")
    for platform in layout["platforms"]:
        name = "al/" + platform + "/skills/al/scripts/al_schema.py"
        if runtime_schema(contents[name]) != layout["runtime_schema"]:
            raise ValueError("unsupported workspace runtime schema")
    body = {
        "schema_version": 1,
        "kind": "approved-workspace-payload-v1",
        "source_digest": source["digest"],
        "source_identity": source["identity"]["root"],
        "subdir": "al",
        "source_content_digest": source["content_digest"],
        "platforms": layout["platforms"],
        "runtime_schema": layout["runtime_schema"],
        "layout_digest": digest(canonical(layout)),
        "files": files,
    }
    return dict(body, digest=digest(canonical(body)))


def validate_payload(value):
    fields = {
        "schema_version",
        "kind",
        "source_digest",
        "source_identity",
        "subdir",
        "source_content_digest",
        "platforms",
        "runtime_schema",
        "layout_digest",
        "files",
        "digest",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "approved-workspace-payload-v1"
        or digest(canonical({k: v for k, v in value.items() if k != "digest"})) != value["digest"]
    ):
        raise ValueError("invalid workspace payload identity")
    for key in ("source_digest", "source_content_digest"):
        if (
            not isinstance(value[key], str)
            or len(value[key]) != 64
            or any(c not in "0123456789abcdef" for c in value[key])
        ):
            raise ValueError("invalid workspace source content binding")
    if (
        not isinstance(value["source_identity"], str)
        or not Path(value["source_identity"]).is_absolute()
    ):
        raise ValueError("invalid workspace source identity")
    layout = load_layout()
    if value["subdir"] != "al":
        raise ValueError("unsupported workspace payload source location")
    if (
        value["layout_digest"] != digest(canonical(layout))
        or value["platforms"] != layout["platforms"]
        or value["runtime_schema"] != layout["runtime_schema"]
    ):
        raise ValueError("workspace payload layout/policy differs")
    files = value["files"]
    if not isinstance(files, dict) or required(layout) - files.keys():
        raise ValueError("incomplete workspace payload")
    folded = set()
    for name, state in files.items():
        classification = selected(name, layout)
        if (
            R.relative(name).as_posix() != name
            or not classification
            or not isinstance(state, dict)
            or set(state) != {"sha256", "mode", "platform", "kind"}
            or classification != (state["platform"], state["kind"])
            or state["mode"] not in {0o644, 0o755}
            or name.casefold() in folded
            or not isinstance(state["sha256"], str)
            or len(state["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in state["sha256"])
        ):
            raise ValueError("unsupported or duplicate workspace payload resource")
        folded.add(name.casefold())
    return value


def verify_payload(path, expected_digest):
    path = R.safe_absolute(path)
    manifest = validate_payload(read_json(path / "manifest.json"))
    if manifest["digest"] != expected_digest:
        raise ValueError("workspace payload digest changed")
    names = set()
    for member in path.rglob("*"):
        R.safe_absolute(member)
        if member.is_file():
            names.add(member.relative_to(path).as_posix())
        elif not member.is_dir():
            raise ValueError("special workspace cache member")
    if names != {"manifest.json"} | {"payload/" + name for name in manifest["files"]}:
        raise ValueError("workspace cache membership changed")
    for name, state in manifest["files"].items():
        if T.state(path / "payload" / name) != {k: state[k] for k in ("sha256", "mode")}:
            raise ValueError("workspace cached resource changed")
    return manifest


def cache_payload(source, state_root):
    contents = revalidate_source(source)
    manifest = build_payload(source, contents)
    parent = namespace(state_root) / "workspace-payloads"
    path = R.safe_absolute(parent / manifest["digest"])
    if path.exists():
        verify_payload(path, manifest["digest"])
        return path, manifest
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".workspace-", dir=parent))
    try:
        for name, state in manifest["files"].items():
            D.atomic(stage / "payload" / name, contents["al/" + name], state["mode"])
        write_json(stage / "manifest.json", manifest)
        verify_payload(stage, manifest["digest"])
        revalidate_source(source)
        if path.exists():
            verify_payload(path, manifest["digest"])
        else:
            os.rename(stage, path)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return path, manifest
