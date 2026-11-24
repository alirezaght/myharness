"""Shared residual implementation-risk rubric for Harness and standalone AL.

Task wording and inherited subsystem complexity do not establish design risk.
The caller binds evidence and checks unresolved product decisions separately.
"""

import hashlib
import re
from pathlib import Path

# `harness classify --assessment-schema` prints a JSON Schema built from these
# same constants (harness.input_formats).
ASSESSMENT_FIELDS = (
    "schema_version",
    "scope",
    "existing_pattern",
    "behavior_clear",
    "hard_parts",
    "pseudocode_benefit",
    "evidence",
    "reason",
)
SCOPES = ("local", "feature", "cross_component")
EVIDENCE_FIELDS = ("path", "sha256", "reason")
HARD_PART_FIELDS = ("kind", "new_design", "description")
MIN_EXPLANATION = 20
KINDS = {
    "algorithm",
    "concurrency",
    "transaction",
    "state-machine",
    "security",
    "distributed",
    "domain",
    "invariant",
    "protocol",
}


# COMPLEX requires a named hard part (#30): an algorithm, an invariant or a protocol. Breadth (files,
# services, modules), and defect or finding counts are never reasons on their own.
HARD_CLASSES = ("algorithm", "invariant", "protocol")
_BREADTH = re.compile(
    r"\b(\d+|many|several|multiple|numerous|lots of|all)\s+(files?|services?|modules?|packages?|components?|"
    r"repos|repositories|defects?|bugs?|findings?|call[- ]?sites?|places|endpoints?|screens?|tests?)\b"
    r"|\b(breadth|cross[- ]cutting|large (diff|change|refactor)|file count|defect count|many changes)\b",
    re.I,
)
_HARD_WORDS = re.compile(
    r"algorithm|invariant|protocol|order|consisten|atomic|idempot|lock|race|concurren|transaction|"
    r"handshake|coheren|exactly[- ]once|at[- ]least[- ]once|monoton|linearizab|serializab|state machine|"
    r"merge|schedul|recurs|graph|search|sort|hash|encrypt|sign|auth|quorum|leader|retry|backoff",
    re.I,
)


def breadth_only(text):
    """True when the text argues from breadth or defect counts and names no hard part."""
    return bool(_BREADTH.search(text or "")) and not _HARD_WORDS.search(text or "")


def named_hard_part(value):
    """Parse `KIND:NAME[ — detail]` (KIND = algorithm | invariant | protocol); raise ValueError."""
    kind, sep, name = (value or "").partition(":")
    kind, name = kind.strip().lower(), name.strip()
    if not sep or kind not in HARD_CLASSES or len(name) < 3:
        raise ValueError(
            "COMPLEX needs a named hard part: --hard-part algorithm|invariant|protocol:<name> "
            "(for example `invariant: balance never goes negative under concurrent withdrawals`)"
        )
    if breadth_only(name):
        raise ValueError(
            "hard part " + repr(name) + " argues from breadth or a defect count; name the algorithm, "
            "invariant or protocol instead (breadth, services and defect counts are not reasons)"
        )
    return {"kind": kind, "name": name}


def assess(value, root):
    required = set(ASSESSMENT_FIELDS)
    if (
        not isinstance(value, dict)
        or set(value) != required
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("invalid closed implementation-risk assessment")
    if (
        not isinstance(value["scope"], str)
        or value["scope"] not in SCOPES
        or type(value["existing_pattern"]) is not bool
        or type(value["behavior_clear"]) is not bool
    ):
        raise ValueError("invalid implementation scope or uncertainty")
    if not value["behavior_clear"]:
        raise ValueError("clarify material behavior choices before final implementation routing")
    if not isinstance(value["reason"], str) or len(value["reason"].strip()) < MIN_EXPLANATION:
        raise ValueError("routing requires a concrete implementation reason")
    root = Path(root).resolve()
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("routing requires inspected repository evidence")
    for row in evidence:
        if not isinstance(row, dict) or set(row) != set(EVIDENCE_FIELDS):
            raise ValueError("invalid inspected routing evidence")
        name = row["path"]
        if (
            not isinstance(name, str)
            or not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
        ):
            raise ValueError("routing evidence must be repository-relative")
        path = root / name
        if (
            not path.is_file()
            or root not in path.resolve().parents
            or any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents)
        ):
            raise ValueError("unsafe or unavailable routing evidence")
        if (
            hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
            or not isinstance(row["reason"], str)
            or not row["reason"].strip()
        ):
            raise ValueError("routing evidence changed or lacks an explanation")
    parts = value["hard_parts"]
    if not isinstance(parts, list):
        raise ValueError(
            "hard_parts must be an explicit array, empty when no new difficult design applies"
        )
    for part in parts:
        if (
            not isinstance(part, dict)
            or set(part) != set(HARD_PART_FIELDS)
            or not isinstance(part["kind"], str)
            or part["kind"] not in KINDS
            or type(part["new_design"]) is not bool
            or not isinstance(part["description"], str)
            or len(part["description"].strip()) < MIN_EXPLANATION
        ):
            raise ValueError("hard parts require a specific design problem, not a topic keyword")
        if part["new_design"] and breadth_only(part["description"]):
            raise ValueError(
                "hard part argues from breadth or a defect count; name the algorithm, invariant or "
                "protocol (breadth, services and defect counts are not reasons for COMPLEX)"
            )
    benefit = value["pseudocode_benefit"]
    if not isinstance(benefit, str):
        raise ValueError("pseudocode benefit must be text, empty when unnecessary")
    if any(p["new_design"] for p in parts) and len(benefit.strip()) >= MIN_EXPLANATION:
        return "COMPLEX"
    if (
        value["scope"] == "local"
        and value["existing_pattern"]
        and not any(p["new_design"] for p in parts)
    ):
        return "SMALL"
    return "STANDARD"
