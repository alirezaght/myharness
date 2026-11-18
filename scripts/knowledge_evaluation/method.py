"""Prospective closed M2 contract. Legacy packages never acquire new interpretations."""

import hashlib
import json
import re

METHOD_ID = "knowledge-native-m2-r2"
PROFILE = "codex-readonly-ordinary-text-exclusions-v2"
PROFILES = {"al-architect": ("gpt-6-astra", "medium"), "al-engineer": ("gpt-6.1-sol", "medium")}
HOST_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
RESERVE = {"completion_bytes": 16384, "error_bytes": 512, "final_calls": 3, "donor_final_calls": 4}
INSTRUCTIONS = (
    "M2 totals remain 30 tools/call attempts, 98304 delivered bytes and 240 seconds. "
    "All root/child attempts, errors and listing bytes count. Completion reserves are inside "
    "these totals: 16384 bytes including 512 error bytes; the final 3 calls (4 for donors) "
    "reserve a transition/refusal, fresh await_assignment and one submit_output, plus root "
    "evaluation:complete-donor for donors. On exhaustion stop retrieval permanently. "
    "Obtain fresh assignment and submit a sourced partial answer identifying unknown gaps. "
    "Do not retry a submission or completion acknowledgment. A stored output, acknowledged "
    "output, returned child and settled execution are separate facts. The 1800 answer tokens "
    "are requested; exact answer-token compliance is unknown without supported measurement. "
    "Only fixed reader-specific exclusions exist; no arbitrary private subtree ACL is promised."
)


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def contract():
    from harness.execution_mcp import SCHEMAS, TOOL_HELP

    help_contract = {
        n: {"description": TOOL_HELP[n], "schema": SCHEMAS[n]} for n in sorted(TOOL_HELP)
    }
    return {
        "id": METHOD_ID,
        "capability_profile": PROFILE,
        "reserve": dict(RESERVE),
        "instructions_sha256": _hash(INSTRUCTIONS.encode()),
        "tool_help_sha256": _hash(
            json.dumps(help_contract, sort_keys=True, separators=(",", ":")).encode()
        ),
    }


def validate_method(package):
    if not isinstance(package, dict) or type(package.get("schema_version")) is not int:
        raise ValueError("explicit package version required")
    version = package["schema_version"]
    if version not in {1, 2}:
        raise ValueError("supported package version 1 or 2 required")
    if version == 1:
        return
    expected = json.dumps(contract(), sort_keys=True, separators=(",", ":"))
    supplied = json.dumps(package.get("method"), sort_keys=True, separators=(",", ":"))
    if supplied != expected:
        raise ValueError("exact supported M2 method/capability/reserve/help contract required")
    profile = profile_for_role(package.get("role"))
    if (
        package.get("host") != "codex"
        or package.get("model") != profile["model"]
        or package.get("effort") != profile["effort"]
    ):
        raise ValueError("M2 requires actual qualified Codex role/model/effort")


def binding(package):
    return package.get("method") if package["schema_version"] == 2 else None


def profile_for_role(role):
    if not isinstance(role, str) or role not in PROFILES:
        raise ValueError("supported M2 participant profile required")
    model, effort = PROFILES[role]
    return {"role": role, "model": model, "effort": effort}


def participant_profile(package):
    validate_method(package)
    if package["schema_version"] != 2:
        raise ValueError("explicit M2 participant profile required")
    return profile_for_role(package["role"])


def host_context(payload, expected_profile=None):
    """Validate before projecting metadata; errors never echo rejected payload values."""
    if not isinstance(payload, dict):
        raise ValueError("typed M2 host context required")
    turn, model, effort = (payload.get(k) for k in ("turn_id", "model", "effort"))
    if not isinstance(turn, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", turn):
        raise ValueError("bounded M2 turn identifier required")
    if (
        not isinstance(model, str)
        or len(model) > 128
        or model not in {p[0] for p in PROFILES.values()}
    ):
        raise ValueError("supported typed M2 host model required")
    if not isinstance(effort, str) or effort not in HOST_EFFORTS:
        raise ValueError("supported typed M2 host effort required")
    if expected_profile is not None and (
        model != expected_profile["model"] or effort != expected_profile["effort"]
    ):
        raise ValueError("observed M2 child profile differs from declared participant")
    return {"turn_id": turn, "model": model, "effort": effort}
