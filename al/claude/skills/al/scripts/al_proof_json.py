"""Closed JSON and immutable references for observable proof protocols."""

import json
import math
import re
from pathlib import Path

import al_artifacts as A
from al_schema import refuse


SHA = re.compile(r"[0-9a-f]{64}\Z")
REF = {"id", "kind", "sha256", "stored_path", "source", "captured_at"}


def raw(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value):
    return A.digest(raw(value))


def loads(content):
    if len(content) > 16 * 1024 * 1024:
        refuse("proof JSON exceeds bounded input size")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                refuse("duplicate proof JSON key: " + key)
            result[key] = value
        return result

    def invalid(_):
        refuse("nonfinite proof JSON")

    try:
        return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as exc:
        refuse("invalid proof JSON: " + str(exc))


def closed(value, fields, label):
    if not isinstance(value, dict):
        refuse("invalid closed " + label + ": expected an object")
    if set(value) != set(fields):
        missing, unexpected = sorted(set(fields) - set(value)), sorted(set(value) - set(fields))
        detail = []
        if missing: detail.append('missing fields: ' + ', '.join(missing))
        if unexpected: detail.append('unexpected fields: ' + ', '.join(unexpected))
        if label == 'proof artifact reference':
            detail.append('create the six-field reference with artifact record --file PATH --kind KIND')
        refuse("invalid closed " + label + ": " + '; '.join(detail))
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        refuse("missing " + label)
    return value


def strings(value, label, *, nonempty=False):
    if (
        not isinstance(value, list)
        or nonempty
        and not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        refuse("invalid unique " + label)
    return value


def reference(value):
    closed(value, REF, "proof artifact reference")
    if (
        value["id"] != value["sha256"]
        or not isinstance(value["sha256"], str)
        or not SHA.fullmatch(value["sha256"])
        or not isinstance(value["stored_path"], str)
        or not Path(value["stored_path"]).is_absolute()
        or not text(value["kind"], "artifact kind")
        or not isinstance(value["source"], str)
        or type(value["captured_at"]) not in {int, float}
        or not math.isfinite(value["captured_at"])
        or value["captured_at"] < 0
    ):
        refuse("invalid immutable proof artifact reference")
    return A.read(value)


def read(value):
    return loads(reference(value))


def snapshot(sid, value, kind, source="AL proof protocol"):
    return A.snapshot_bytes(sid, raw(value), kind, source)
