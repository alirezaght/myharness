"""Independent semantic assessment coverage; no automatic correctness inference."""

import al_behavior_observations as O
import al_proof_json as J
from al_schema import refuse


def validate_counterexample_search(st, report, obligations=None):
    import al_traceability as T

    if obligations is None:
        _, _, obligations = O.current(st)
    groups = {
        group["id"]: group
        for group in obligations["obligations"]
        if group["applicability"]["status"] == "required"
    }
    rows = report.get("counterexample_search")
    if not isinstance(rows, list) or not rows:
        refuse("fresh review requires independent counterexample search")
    covered = set()
    for row in rows:
        J.closed(
            row,
            {
                "obligation_ids",
                "scenario",
                "inspected_locations",
                "evidence",
                "result",
                "rationale",
            },
            "independent counterexample assessment",
        )
        identifiers = J.strings(row["obligation_ids"], "counterexample obligations", nonempty=True)
        if not set(identifiers) <= groups.keys():
            refuse("counterexample assessment references unknown obligation")
        covered.update(identifiers)
        J.text(row["scenario"], "concrete counterexample scenario")
        J.text(row["rationale"], "independent counterexample rationale")
        T.locations(row["inspected_locations"])
        J.strings(row["evidence"], "counterexample evidence")
        if row["evidence"]:
            T.evidence(st, row["evidence"])
        if row["result"] not in {"found", "none_found", "blocked"}:
            refuse("unknown counterexample result")
        if row["result"] in {"found", "blocked"}:
            anchors = {
                criterion
                for identifier in identifiers
                for criterion in groups[identifier]["criterion_ids"]
            }
            if not any(
                finding.get("source") in anchors
                and finding.get("severity") == "BLOCKING"
                and "resolution" not in finding
                for finding in report.get("findings", [])
            ):
                refuse("found/blocked counterexample needs an ordinary linked blocking finding")
            if report.get("status") == "APPROVED":
                refuse("found/blocked counterexample cannot approve")
    if covered != groups.keys():
        refuse("counterexample assessment omits applicable task-owned obligations")


def require_unblocked_comparison(st, report):
    fresh = J.read(st["review"]["fresh"])
    blocked = [row for row in fresh["counterexample_search"] if row["result"] == "blocked"]
    if not blocked:
        return
    _, _, obligations = O.current(st)
    groups = {group["id"]: group for group in obligations["obligations"]}
    if report.get("status") == "APPROVED":
        refuse("blocked independent fresh proof cannot become comparison approval")
    for row in blocked:
        sources = {
            criterion
            for name in row["obligation_ids"]
            for criterion in groups[name]["criterion_ids"]
        }
        if any(
            value.get("source") in sources and value.get("status") == "fixed"
            for value in report.get("prior_resolutions", [])
        ):
            refuse("blocked fresh proof requires a new independent assessment after correction")
        if not any(
            finding.get("source") in sources
            and finding.get("severity") == "BLOCKING"
            and "resolution" not in finding
            for finding in report.get("findings", [])
        ):
            refuse("comparison must retain blocked independent proof as a criterion-linked blocker")
