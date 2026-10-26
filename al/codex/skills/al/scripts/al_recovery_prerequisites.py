"""Dependency-first recovery uses only an existing exact evidence plan."""

import al_ledger as L
from al_recovery_types import action, supply, issue


def prerequisites(st):
    plan = L.ledger(st)["plan"]
    visited = set()

    def walk(name, active, preflight=False):
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
        if (name, preflight) in visited:
            return None
        visited.add((name, preflight))
        spec = plan.get(name)
        if spec is None:
            recorded = L.latest(st, name, kind='preflight') if preflight else None
            if recorded and recorded.get('run') == st['run_id'] and L.entry_verdict(st, recorded)[0] == 'VALID':
                return None
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
        for dependency, is_preflight in ([(spec['preflight'], True)] if spec.get('preflight') else []) + [(n, False) for n in spec.get('needs', [])]:
            found = walk(dependency, active + [name], is_preflight)
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
        for dependency, is_preflight in ([(spec['preflight'], True)] if spec.get('preflight') else []) + [(n, False) for n in spec.get('needs', [])]:
            found = walk(dependency, [], is_preflight)
            if found:
                return [found]
    return []
