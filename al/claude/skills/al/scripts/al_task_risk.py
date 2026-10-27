"""Shared residual implementation-risk rubric for Harness and standalone AL.

Task wording and inherited subsystem complexity do not establish design risk.
The caller binds evidence and checks unresolved product decisions separately.
"""

import hashlib
from pathlib import Path

KINDS = {
    "algorithm",
    "concurrency",
    "transaction",
    "state-machine",
    "security",
    "distributed",
    "domain",
}


def assess(value, root):
    required = {
        "schema_version",
        "scope",
        "existing_pattern",
        "behavior_clear",
        "hard_parts",
        "pseudocode_benefit",
        "evidence",
        "reason",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("invalid closed implementation-risk assessment")
    if (
        not isinstance(value["scope"], str)
        or value["scope"] not in {"local", "feature", "cross_component"}
        or type(value["existing_pattern"]) is not bool
        or type(value["behavior_clear"]) is not bool
    ):
        raise ValueError("invalid implementation scope or uncertainty")
    if not value["behavior_clear"]:
        raise ValueError("clarify material behavior choices before final implementation routing")
    if not isinstance(value["reason"], str) or len(value["reason"].strip()) < 20:
        raise ValueError("routing requires a concrete implementation reason")
    root = Path(root).resolve()
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("routing requires inspected repository evidence")
    for row in evidence:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "reason"}:
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
            or set(part) != {"kind", "new_design", "description"}
            or not isinstance(part["kind"], str)
            or part["kind"] not in KINDS
            or type(part["new_design"]) is not bool
            or not isinstance(part["description"], str)
            or len(part["description"].strip()) < 20
        ):
            raise ValueError("hard parts require a specific design problem, not a topic keyword")
    benefit = value["pseudocode_benefit"]
    if not isinstance(benefit, str):
        raise ValueError("pseudocode benefit must be text, empty when unnecessary")
    if any(p["new_design"] for p in parts) and len(benefit.strip()) >= 20:
        return "COMPLEX"
    if (
        value["scope"] == "local"
        and value["existing_pattern"]
        and not any(p["new_design"] for p in parts)
    ):
        return "SMALL"
    return "STANDARD"
