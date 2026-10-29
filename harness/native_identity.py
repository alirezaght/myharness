"""Read only canonical first SessionMeta records, never native transcripts or auth."""

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.execution_contract import digest
from harness.native_tool_policy import ROLES


class IdentityNotReady(ValueError):
    pass


@dataclass(frozen=True)
class NativeIdentity:
    thread_id: str
    session_id: str
    parent_thread_id: str | None
    role: str | None
    version: str
    source: object
    created_at: str | None = None

    def to_dict(self):
        return asdict(self)


def read_native_identity(native_root, thread_id):
    thread_id = str(uuid.UUID(thread_id))
    root = Path(native_root).resolve(strict=True)
    matches = []
    for path in root.glob("sessions/*/*/*/*" + thread_id + ".jsonl"):
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
            raise ValueError("native metadata symlink rejected")
        if path.resolve().is_relative_to(root):
            matches.append(path)
    if not matches:
        raise IdentityNotReady("IDENTITY_NOT_READY: native metadata is not durable")
    if len(matches) != 1:
        raise ValueError("conflicting canonical native metadata")
    if matches[0].stat().st_nlink != 1:
        raise ValueError("native metadata hardlink rejected")
    with matches[0].open("rb") as stream:
        raw = stream.readline(262145)
    if len(raw) > 262144 or not raw.endswith(b"\n"):
        raise IdentityNotReady("IDENTITY_NOT_READY: first native record is incomplete")
    record = json.loads(raw)
    if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
        raise ValueError("canonical first native record is not SessionMeta")
    value = record["payload"]
    if value.get("id") != thread_id or value.get("cli_version") != "0.164.0":
        raise ValueError("native metadata identity/version mismatch")
    session = value.get("session_id")
    if not isinstance(session, str):
        raise ValueError("native metadata requires explicit session_id")
    str(uuid.UUID(session))
    source, parent, role = (
        value.get("source"),
        value.get("parent_thread_id"),
        value.get("agent_role"),
    )
    if isinstance(source, dict) and "subagent" in source:
        spawn = source["subagent"].get("thread_spawn")
        if not isinstance(spawn, dict):
            raise ValueError("unsupported native child provenance")
        actual_parent, actual_role = spawn.get("parent_thread_id"), spawn.get("agent_role")
        if (
            parent is not None
            and parent != actual_parent
            or role is not None
            and role != actual_role
        ):
            raise ValueError("contradictory native parent/role metadata")
        parent, role = actual_parent, actual_role
        if not parent or role not in ROLES:
            raise ValueError("native child lacks exact protected AL role")
        str(uuid.UUID(parent))
    elif (
        not isinstance(source, str)
        or source not in {"exec", "cli"}
        or parent is not None
        or role is not None
    ):
        raise ValueError("unsupported native root provenance")
    return NativeIdentity(
        thread_id, session, parent, role, value["cli_version"], source, value.get("timestamp")
    )


def identity_digest(identity):
    return digest({key: value for key, value in identity.to_dict().items() if key != "created_at"})
