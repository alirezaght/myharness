"""Closed evidence metadata plus separately attributed, bounded operator interpretations."""

import re

from harness.execution_logs import redact
from harness.reliability.catalog import detail

CAUSES = {
    "unknown",
    "incorrect_implementation",
    "architecture_violation",
    "specification_drift",
    "unauthorized_operation",
    "repeated_unsuccessful_edits",
    "invalid_handoff",
    "hallucinated_dependency",
    "hallucinated_api",
    "incorrect_tool",
    "missing_context",
    "excessive_retries",
    "budget_exhaustion",
    "failed_verification",
    "human_rejected_deliverable",
}
RATIONALES = {
    "evidence_updated",
    "interpretation_corrected",
    "evidence_sufficient",
    "not_reproduced",
    "duplicate_symptom",
    "unrelated_symptoms",
    "scope_changed",
}
ACTIONS = {
    "investigate",
    "add_regression",
    "review_contract",
    "review_policy",
    "review_context",
    "review_design",
    "evaluate_change",
}


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError("invalid record identity")
    return value


def object_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid investigation fields")


def authored_keys(value, keys):
    if not isinstance(value, dict):
        raise ValueError("invalid investigation fields")
    object_keys({k: v for k, v in value.items() if k != "operator_explanation"}, keys)
    if "operator_explanation" in value:
        text = value["operator_explanation"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 2000
            or any(ord(c) < 32 and c not in "\n\t" for c in text)
            or redact(text) != text
        ):
            raise ValueError("operator explanation must be bounded and omit credentials")


def enum(value, options):
    if not isinstance(value, str) or value not in options:
        raise ValueError("unsupported investigation value")
    return value


def bounded(value, limit=25):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("invalid investigation list")
    return value


def references(conn, selected, values, *, required=False):
    bounded(values)
    if required and not values:
        raise ValueError("supporting evidence required")
    for ref in values:
        identity(ref)
        detail(conn, selected, ref)
    if len(set(values)) != len(values):
        raise ValueError("duplicate evidence reference")
    return values


def rationale(conn, selected, value, *, required=False):
    authored_keys(value, {"code", "evidence"})
    enum(value["code"], RATIONALES)
    references(conn, selected, value["evidence"], required=required)


def validate_changes(conn, selected, changes, *, repository=None):
    allowed = {
        "hypotheses",
        "affected_components",
        "controls",
        "corrective_actions",
        "status",
        "rationale",
        "corrections",
    }
    if not isinstance(changes, dict) or not changes or set(changes) - allowed:
        raise ValueError("invalid investigation changes")
    for value in bounded(changes.get("hypotheses", [])):
        authored_keys(value, {"cause", "confidence", "supporting", "contradicting"})
        enum(value["cause"], CAUSES)
        enum(value["confidence"], {"unassessed", "low", "medium", "high"})
        references(conn, selected, value["supporting"])
        references(conn, selected, value["contradicting"])
        if value["cause"] != "unknown" and not value["supporting"]:
            raise ValueError("supporting evidence required")
    for value in bounded(changes.get("affected_components", [])):
        verified = value.get("verification") == "verified" if isinstance(value, dict) else False
        object_keys(
            value,
            {"reference", "verification"} | ({"source_version", "snapshot"} if verified else set()),
        )
        enum(value["verification"], {"unverified", "verified"})
        if verified:
            identity(value["source_version"])
            identity(value["snapshot"])
            from harness.reliability.component_evidence import require_component

            require_component(conn, repository, value)
        path = value["reference"]
        if (
            not isinstance(path, str)
            or len(path) > 240
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
            or any(p in {"", ".", ".."} for p in path.split("/"))
        ):
            raise ValueError("invalid component reference")
    for value in bounded(changes.get("controls", [])):
        object_keys(value, {"kind", "state", "evidence"})
        enum(
            value["kind"], {"authority", "dispatch", "contract", "budget", "verification", "review"}
        )
        enum(value["state"], {"held", "failed", "unknown"})
        references(conn, selected, value["evidence"], required=value["state"] != "unknown")
    for action in bounded(changes.get("corrective_actions", [])):
        if isinstance(action, dict):
            authored_keys(action, {"kind"})
            enum(action["kind"], ACTIONS)
        else:
            enum(action, ACTIONS)
    for value in bounded(changes.get("corrections", [])):
        authored_keys(value, {"field", "interpretation", "rationale"})
        enum(
            value["field"],
            {"classification", "hypotheses", "affected_components", "controls", "status"},
        )
        enum(value["interpretation"], CAUSES | {"held", "failed", "unknown", "unverified"})
        rationale(conn, selected, value["rationale"], required=True)
    if "status" in changes:
        enum(changes["status"], {"open", "investigating", "resolved", "dismissed"})
    if "rationale" in changes:
        rationale(
            conn,
            selected,
            changes["rationale"],
            required=changes.get("status") in {"resolved", "dismissed"},
        )
    if changes.get("status") in {"resolved", "dismissed"} and "rationale" not in changes:
        raise ValueError("resolution rationale required")
