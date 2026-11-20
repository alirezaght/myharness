"""One closed grammar for logical storage reservations and argv operands."""

from __future__ import annotations

import re
from pathlib import Path

from .storage_filesystem import StorageError, canonical, digest

CLASSES = {"verification-fixtures", "operation-scratch", "build-cache"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def _name(value):
    return isinstance(value, str) and bool(NAME.fullmatch(value)) and value not in {".", ".."}


def normalize(value):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "scratch", "outputs", "operands"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or any(not isinstance(value[k], list) for k in ("scratch", "outputs", "operands"))
        or not 1 <= len(value["scratch"]) <= 16
        or len(value["outputs"]) > 32
        or not 0 <= len(value["operands"]) <= 64
        or len(canonical(value)) > 65536
    ):
        raise StorageError("invalid_storage_declaration")
    scratch, outputs, operands = [], [], []
    for row in value["scratch"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"name", "class"}
            or not _name(row["name"])
            or not isinstance(row["class"], str)
            or row["class"] not in CLASSES
        ):
            raise StorageError("invalid_scratch_declaration")
        scratch.append(dict(row))
    for row in value["outputs"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"name", "filename", "required", "max_bytes"}
            or not _name(row["name"])
            or not _name(row["filename"])
            or type(row["required"]) is not bool
            or type(row["max_bytes"]) is not int
            or not 1 <= row["max_bytes"] <= 256 * 1024**2
        ):
            raise StorageError("invalid_output_declaration")
        outputs.append(dict(row))
    names = {"scratch": {r["name"] for r in scratch}, "output": {r["name"] for r in outputs}}
    if (
        len(names["scratch"]) != len(scratch)
        or len(names["output"]) != len(outputs)
        or len({r["filename"] for r in outputs}) != len(outputs)
        or sum(r["max_bytes"] for r in outputs) > 256 * 1024**2
    ):
        raise StorageError("duplicate_or_excessive_storage")
    for row in value["operands"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"index", "token", "kind", "name", "suffix"}
            or type(row["index"]) is not int
            or not 0 <= row["index"] < 100
            or not isinstance(row["kind"], str)
            or row["kind"] not in names
            or not _name(row["name"])
            or row["name"] not in names[row["kind"]]
            or not isinstance(row["suffix"], str)
        ):
            raise StorageError("invalid_storage_operand")
        kind = "SCRATCH" if row["kind"] == "scratch" else "OUTPUT"
        if row["token"] != f"@AL_{kind}:{row['name']}@":
            raise StorageError("invalid_storage_token")
        suffix = Path(row["suffix"])
        if (
            row["suffix"]
            and (
                suffix.is_absolute()
                or ".." in suffix.parts
                or len(suffix.parts) > 16
                or any(not _name(p) for p in suffix.parts)
            )
            or row["kind"] == "output"
            and row["suffix"]
        ):
            raise StorageError("unsafe_storage_suffix")
        operands.append(dict(row))
    if len({r["index"] for r in operands}) != len(operands):
        raise StorageError("duplicate_storage_operand")
    return {
        "schema_version": 1,
        "scratch": sorted(scratch, key=lambda r: r["name"]),
        "outputs": sorted(outputs, key=lambda r: r["name"]),
        "operands": sorted(operands, key=lambda r: r["index"]),
    }


def declaration_digest(value):
    normalized = normalize(value)
    return digest(normalized) if normalized is not None else None


def render_argv(argv, declaration, temporary, *, create=False):
    declaration = normalize(declaration)
    if declaration is None:
        return list(argv)
    temporary = Path(temporary)
    if not temporary.is_absolute() or ".." in temporary.parts:
        raise StorageError("invalid_storage_temporary")
    if create:
        for name in ("scratch", "outputs"):
            (temporary / name).mkdir(mode=0o700, parents=True, exist_ok=True)
        for row in declaration["scratch"]:
            (temporary / "scratch" / row["name"]).mkdir(mode=0o700, exist_ok=True)
    result, mapped = list(argv), set()
    output_names = {r["name"]: r["filename"] for r in declaration["outputs"]}
    for row in declaration["operands"]:
        index = row["index"]
        if index >= len(argv) or argv[index] != row["token"]:
            raise StorageError("storage_operand_mismatch")
        target = (
            temporary / "scratch" / row["name"] / row["suffix"]
            if row["kind"] == "scratch"
            else temporary / "outputs" / output_names[row["name"]]
        )
        result[index] = str(target)
        mapped.add(index)
    if any(
        isinstance(arg, str) and "@AL_" in arg and index not in mapped
        for index, arg in enumerate(argv)
    ):
        raise StorageError("unresolved_storage_operand")
    return result


def allocation_class(declaration):
    classes = {row["class"] for row in normalize(declaration)["scratch"]}
    return next(iter(classes)) if len(classes) == 1 else "operation-scratch"


def evidence_binding(entry):
    result = {
        key: entry.get(key)
        for key in (
            "run",
            "id",
            "reservation",
            "status",
            "kind",
            "counts",
            "fp_before",
            "fp_after",
            "log",
            "log_sha256",
        )
    }

    if entry.get("storage_settlement") is not None:
        result.update(
            {key: entry.get(key) for key in ("storage_settlement", "storage_capture_state")}
        )
    if entry.get("recipe_context") is not None:
        result["recipe_context"] = entry["recipe_context"]
    if entry.get("recipe_assignment") is not None:
        result.update(
            {
                key: entry.get(key)
                for key in (
                    "recipe_assignment",
                    "recipe_result",
                    "recipe_process",
                    "recipe_log_copy",
                )
            }
        )
    return result


def prepare_directories(declaration, temporary):
    declaration = normalize(declaration)
    if declaration is None:
        return
    temporary = Path(temporary)
    for name in ("scratch", "outputs"):
        (temporary / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    for row in declaration["scratch"]:
        (temporary / "scratch" / row["name"]).mkdir(mode=0o700, exist_ok=True)


def default_declaration():
    return {
        "schema_version": 1,
        "scratch": [{"name": "operation", "class": "operation-scratch"}],
        "outputs": [],
        "operands": [],
    }
