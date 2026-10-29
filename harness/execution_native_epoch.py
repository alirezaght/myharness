"""Supervisor snapshot of canonical native UUID membership before successor resume."""

import json
import uuid
from pathlib import Path

from harness.execution_contract import canonical
from harness.execution_handoff import durable_file
from harness.template_classification import _ordinary


def bind_native_boundary(directory, native_root, selector):
    root = Path(native_root).resolve(strict=True)
    path = Path(directory) / "native-epoch-boundary.json"
    binding = {key: selector[key] for key in ("epoch_id", "authority_generation", "activated_at")}
    try:
        raw, mode = _ordinary(path, 16 * 1024 * 1024)
    except FileNotFoundError:
        ids = set()
        for count, member in enumerate(root.glob("sessions/*/*/*/*.jsonl"), 1):
            if count > 250000:
                raise ValueError(
                    "native epoch membership enumeration exceeds bounded capability"
                ) from None
            if member.is_symlink() or member.stat().st_nlink != 1:
                raise ValueError("native epoch metadata alias refused") from None
            value = member.stem[-36:]
            try:
                parsed = str(uuid.UUID(value))
            except ValueError:
                raise ValueError("native epoch metadata membership format unknown") from None
            if parsed != value:
                raise ValueError("native epoch metadata membership format unknown") from None
            ids.add(value)
        record = {
            "kind": "native-epoch-membership-v1",
            "binding": binding,
            "native_root": str(root),
            "preexisting_child_ids": sorted(ids),
        }
        durable_file(path, canonical(record).encode())
    else:
        record = json.loads(raw)
        if (
            mode != 0o600
            or record.get("kind") != "native-epoch-membership-v1"
            or record.get("binding") != binding
            or record.get("native_root") != str(root)
        ):
            raise ValueError("native epoch membership binding changed")
    return dict(selector, preexisting_child_ids=record["preexisting_child_ids"])
