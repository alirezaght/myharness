"""Allowlisted own Codex token metadata; generation counts are never answer-text tokens."""

from harness.native_identity import read_native_identity

from .method import host_context, participant_profile, profile_for_role
from .observer import records

FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
NOTICE = (
    "Host generation usage may include tool arguments and other generated material. Cached input "
    "is a subset of input; reasoning output is a subset of output. These are not answer.md tokens. "
    "Synthetic parser contracts require separate installed-host public qualification."
)


def _counts(value):
    if not isinstance(value, dict) or not set(FIELDS) <= value.keys():
        raise ValueError("complete allowlisted token metadata unavailable")
    result = {k: value[k] for k in FIELDS}
    if any(type(v) is not int or v < 0 for v in result.values()):
        raise ValueError("nonnegative integer usage required")
    if (
        result["cached_input_tokens"] > result["input_tokens"]
        or result["reasoning_output_tokens"] > result["output_tokens"]
        or result["total_tokens"] != result["input_tokens"] + result["output_tokens"]
    ):
        raise ValueError("inconsistent host usage fields")
    return result


def observe_usage(native_root, root_id, identity, role, *, expected_profile=None):
    """One fresh own turn only. Repeated cumulative snapshots are not summed."""
    root = read_native_identity(native_root, root_id)
    actual = read_native_identity(native_root, identity)
    if (
        root.parent_thread_id is not None
        or (identity == root_id and (actual.parent_thread_id is not None or role != "orchestrator"))
        or (identity != root_id and (actual.parent_thread_id != root_id or actual.role != role))
    ):
        raise ValueError("usage identity/parent/role differs")
    if identity == root_id:
        if expected_profile is not None:
            raise ValueError("root usage must be independently scoped")
    elif expected_profile != profile_for_role(role):
        raise ValueError("exact declared M2 child profile required")
    started, context, terminal, observations = None, None, None, []
    previous = dict.fromkeys(FIELDS, 0)
    active_turn = None
    for number, record, digest in records(native_root, identity, require_complete=True):
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if record["type"] == "turn_context":
            candidate = host_context(payload, expected_profile)
            turn = candidate["turn_id"]
            if not isinstance(turn, str) or (active_turn and active_turn != turn):
                raise ValueError("usage multiple/unknown turns unavailable")
            active_turn = turn
            if context and context != candidate:
                raise ValueError("usage turn context changed")
            context = candidate
        if record["type"] != "event_msg":
            continue
        kind = payload.get("type")
        if kind == "task_started":
            if started is not None:
                raise ValueError("usage resumed/multiple turns unavailable")
            started = {
                "turn_id": payload.get("turn_id"),
                "record_number": number,
                "record_sha256": digest,
            }
        elif kind == "task_complete":
            if terminal or not started or payload.get("turn_id") != started["turn_id"]:
                raise ValueError("usage terminal scope unavailable")
            terminal = {"record_number": number, "record_sha256": digest}
        elif kind == "token_count":
            if (
                terminal
                or not started
                or not context
                or context.get("turn_id") != started["turn_id"]
            ):
                raise ValueError("usage outside established own turn")
            if payload.get("turn_id", active_turn) != active_turn:
                raise ValueError("foreign token turn refused")
            info = payload.get("info")
            if not isinstance(info, dict):
                raise ValueError("host token_count info unavailable")
            total, last = (
                _counts(info.get("total_token_usage")),
                _counts(info.get("last_token_usage")),
            )
            if observations and total == previous:
                if last != observations[-1]["last"]:
                    raise ValueError("inconsistent duplicate usage")
                observations.append(
                    {
                        "record_number": number,
                        "record_sha256": digest,
                        "total": total,
                        "last": last,
                        "duplicate": True,
                    }
                )
                continue
            delta = {k: total[k] - previous[k] for k in FIELDS}
            if any(v < 0 for v in delta.values()) or delta != last:
                raise ValueError("usage reset or unestablished cumulative baseline")
            previous = total
            observations.append(
                {
                    "record_number": number,
                    "record_sha256": digest,
                    "total": total,
                    "last": last,
                    "duplicate": False,
                }
            )
    if (
        not started
        or not terminal
        or not observations
        or not context
        or set(context) != {"turn_id", "model", "effort"}
        or context["turn_id"] != started["turn_id"]
        or observations[-1]["record_number"] >= terminal["record_number"]
    ):
        raise ValueError("complete own terminal usage unavailable")
    return {
        "kind": "own-codex-token-metadata-v1",
        "status": "observed",
        "identity": identity,
        "root_id": root_id,
        "role": role,
        "host_context": context,
        "baseline": {
            "kind": "fresh-single-turn-first-total-equals-last",
            "counts": dict.fromkeys(FIELDS, 0),
        },
        "turn_generation_delta": previous,
        "observations": observations,
        "started": started,
        "terminal": terminal,
        "notice": NOTICE,
        "answer_tokens": None,
        "answer_token_compliance": "UNKNOWN",
    }


def usage_receipt(adapter):
    if not adapter:
        return {"status": "unavailable", "reason": "no authenticated adapter"}
    operation = adapter.operation
    root = operation.registry.root
    result = {
        "root": None,
        "children": [],
        "answer_tokens": None,
        "answer_token_compliance": "UNKNOWN",
        "notice": NOTICE,
    }
    for identity, role in [
        (root, "orchestrator"),
        *[(i, p["role"]) for i, p in adapter.principals.items() if i != root],
    ]:
        try:
            expected = participant_profile(adapter.package) if identity != root else None
            value = observe_usage(
                operation.registry.native_root, root, identity, role, expected_profile=expected
            )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            value = {
                "identity": identity,
                "role": role,
                "status": "unavailable",
                "reason": str(exc)[:256],
            }
        if identity == root:
            result["root"] = value
        else:
            result["children"].append(value)
    return result
