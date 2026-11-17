"""Own Codex lifecycle metadata only; never retain native prose or reasoning."""

import json
import os
import stat
from datetime import datetime
from itertools import count
from pathlib import Path

from harness.native_identity import read_native_identity

from .package import sha

MAX_RECORD = 4 * 1024 * 1024
MAX_METADATA = 64 * 1024 * 1024


def _time(value):
    if isinstance(value, str):
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("native lifecycle timezone unavailable")
        return moment.timestamp()
    if type(value) in {int, float} and value >= 0:
        return float(value)
    raise ValueError("native lifecycle timestamp unavailable")


def records(native_root, identity):
    """Select exactly one own canonical file; bound each read and reject aliases/foreign owners."""
    root = Path(native_root).resolve(strict=True)
    matches = list(root.glob("sessions/*/*/*/*" + identity + ".jsonl"))
    if len(matches) != 1:
        raise ValueError("exact own canonical native metadata unavailable")
    path = matches[0]
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(
        root
    ):
        raise ValueError("native metadata alias refused")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or before.st_size > MAX_METADATA
        ):
            raise ValueError("bounded own regular native metadata required")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            size = 0
            for number in count():
                raw = stream.readline(MAX_RECORD + 1)
                if not raw:
                    break
                size += len(raw)
                if len(raw) > MAX_RECORD or size > MAX_METADATA:
                    raise ValueError("native metadata exceeds observation bound")
                if not raw.endswith(b"\n"):
                    break  # A partially flushed final event grants no completion.
                value = json.loads(raw)
                # Non-lifecycle record bodies are never interpreted or returned.
                if value.get("type") in {
                    "session_meta",
                    "turn_context",
                    "event_msg",
                    "response_item",
                }:
                    yield number, value, sha(raw)
    finally:
        os.close(fd)


def observe_return(native_root, root_id, child_id, role, *, expected_wait=None):
    root = read_native_identity(native_root, root_id)
    child = read_native_identity(native_root, child_id)
    if root.parent_thread_id is not None or child.parent_thread_id != root_id or child.role != role:
        raise ValueError("native return parent/role binding differs")
    if root_id == child_id:
        raise ValueError("specialized child return required")
    turns, started, completed = {}, {}, {}
    starts = 0
    for number, record, digest in records(native_root, child_id):
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if record["type"] == "turn_context" and isinstance(payload.get("turn_id"), str):
            turns[payload["turn_id"]] = {
                k: payload[k] for k in ("model", "effort", "turn_id") if k in payload
            }
        if record["type"] == "event_msg" and payload.get("type") == "task_started":
            starts += 1
            started[payload.get("turn_id")] = number
        if record["type"] == "event_msg" and payload.get("type") == "task_complete":
            turn = payload.get("turn_id")
            if turn in turns and turn in started and started[turn] < number:
                completed[turn] = {
                    "turn_id": turn,
                    "completed_at": _time(payload.get("completed_at")),
                    "record_sha256": digest,
                    "record_number": number,
                    "host_context": turns[turn],
                }
    if starts != 1 or len(started) != 1 or len(completed) != 1:
        raise ValueError("one actual returned child turn required; missing/resumed terminal")
    terminal = next(iter(completed.values()))
    calls, waits = {}, []
    for number, record, digest in records(native_root, root_id):
        payload = record.get("payload", {})
        if not isinstance(payload, dict) or record["type"] != "response_item":
            continue
        call = payload.get("call_id")
        if (
            payload.get("type") == "function_call"
            and payload.get("name")
            in {
                "wait_agent",
                "collaboration.wait_agent",
            }
            and isinstance(call, str)
        ):
            calls[call] = number
        if payload.get("type") == "function_call_output" and call in calls:
            try:
                result = json.loads(payload.get("output", ""))
            except (ValueError, TypeError):
                continue
            if (
                isinstance(result, dict)
                and set(result) == {"message", "timed_out"}
                and result["timed_out"] is False
                and calls[call] < number
                and _time(record.get("timestamp")) >= terminal["completed_at"]
            ):
                waits.append({"call_id": call, "record_number": number, "record_sha256": digest})
    if expected_wait is not None:
        waits = [w for w in waits if w["record_sha256"] == expected_wait]
    if not waits:
        raise ValueError("matched actual root wait after child terminal unavailable")
    return {
        "kind": "own-codex-return-v1",
        "root_id": root_id,
        "child_id": child_id,
        "role": role,
        "terminal": terminal,
        "wait": waits[-1],
        "limits": "native turn return only; no process settlement or clean-review attestation",
    }
