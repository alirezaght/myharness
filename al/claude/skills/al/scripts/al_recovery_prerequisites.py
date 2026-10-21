"""Dependency-first recovery uses only an existing exact evidence plan."""

import al_ledger as L
from al_recovery_types import action, supply, issue


def prerequisites(st):
    plan = L.ledger(st)["plan"]
    visited = set()

    def walk(name, active):
        if name in active:
            return issue(
                "PREREQUISITE_UNRESOLVED",
                "area",
                name,
                "The prerequisite graph contains a cycle.",
                supply(
                    "acyclic-verification-plan",
                    "Supply a valid exact prerequisite graph; no check may be skipped.",
                ),
                {"cycle": active + [name]},
            )
        if name in visited:
            return None
        visited.add(name)
        spec = plan.get(name)
        if spec is None:
            return issue(
                "PREREQUISITE_UNRESOLVED",
                "area",
                name,
                "The required prerequisite has no current executable plan.",
                supply(
                    "verification-setup-plan",
                    "Supply the exact sourced setup/preflight command and its dependency scope.",
                ),
                {"missing_area": name},
            )
        for dependency in ([spec["preflight"]] if spec.get("preflight") else []) + list(
            spec.get("needs", [])
        ):
            found = walk(dependency, active + [name])
            if found:
                return found
        entry, verdict, reason = L.planned_verdict(st, name, spec)
        if verdict != "VALID":
            arguments = {
                "area": name,
                "kind": "test",
                "tier": None,
                "cwd": None,
                "scope": None,
                "sources_file": None,
                "env": None,
                "preflight": None,
                "needs": None,
                "fresh": False,
                "timeout": None,
                "tail": 40,
            }
            return issue(
                "PREREQUISITE_RUN_REQUIRED",
                "area",
                name,
                "An exact planned prerequisite is not current and passing.",
                action("evidence.run", arguments),
                {
                    "spec": spec,
                    "entry": entry,
                    "verdict": verdict,
                    "environment": L.scope_fingerprint([], spec.get("env", [])),
                },
            )
        return None

    for spec in plan.values():
        for dependency in ([spec["preflight"]] if spec.get("preflight") else []) + list(
            spec.get("needs", [])
        ):
            found = walk(dependency, [])
            if found:
                return [found]
    return []
