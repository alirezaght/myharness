"""Allowlisted own Codex token metadata; generation counts are never answer-text tokens."""

from .observer import UNAVAILABLE

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
    "Scoped native usage is unavailable pending qualified public observation."
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
    """No permitted source currently supplies scoped host-generation measurements."""
    raise ValueError(UNAVAILABLE)


def usage_receipt(adapter):
    if not adapter:
        return {
            "kind": "native-generation-usage-v2",
            "status": "unavailable",
            "reason": "no authenticated adapter",
            "answer_tokens": None,
            "answer_token_compliance": "UNKNOWN",
        }
    operation = adapter.operation
    root = operation.registry.root
    result = {
        "kind": "native-generation-usage-v2",
        "status": "unavailable",
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
        value = {
            "identity": identity,
            "role": role,
            "status": "unavailable",
            "reason": UNAVAILABLE,
            "turn_generation_delta": None,
        }
        if identity == root:
            result["root"] = value
        else:
            result["children"].append(value)
    return result
