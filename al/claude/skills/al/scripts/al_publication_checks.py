"""Publication code readiness never substitutes for review or release approval."""

import al_gates as G
import al_gate_currency as Currency
import al_ledger as L
import al_scope as S
import al_writers as W
import al_proof_json as J


def source_revision(st):
    return {
        "commit_oid": S.git_bytes("rev-parse", "--verify", "HEAD^{commit}").decode().strip(),
        "candidate_fingerprint": S.capture_candidate(st)["fingerprint"],
    }


def binding(entry):
    return {
        "id": entry["id"],
        "reservation": entry.get("reservation"),
        "area": entry["area"],
        "command_digest": J.digest(
            {
                key: entry.get(key)
                for key in ("argv", "cwd", "scope", "sources", "env", "preflight", "needs")
            }
        ),
        "entry_digest": J.digest(entry),
        "log_sha256": entry.get("log_sha256"),
        "execution_receipt": entry.get("execution_receipt"),
        "execution_association": entry.get("execution_association"),
    }


def checks(st, areas):
    gaps = list(W.operation_gaps(st))
    currency = Currency.check(st)
    if not currency["current"]:
        gaps.append("publication requires current gate inventory")
    gaps += G.base_gaps(st) + G.candidate_gaps(st)
    gaps += [
        "required broad check " + area + ": " + verdict for area, verdict in L.gaps(st, "broad")
    ]
    ledger = L.ledger(st)
    required = set(areas) | {
        name for name, spec in ledger["plan"].items() if spec["tier"] == "broad"
    }
    selected = {}
    for area in sorted(required):
        spec = ledger["plan"].get(area)
        if spec is None:
            gaps.append("publication check has no current plan: " + area)
            continue
        entry, verdict, _ = L.planned_verdict(st, area, spec)
        if verdict != "VALID":
            gaps.append("publication check " + area + ": " + verdict)
        elif entry is not None:
            selected[entry["id"]] = binding(entry)
    manifest = G.manifest(st)
    if manifest:
        reference = st["gate_manifests"][-1]["sha256"]
        for stage in ("base", "candidate"):
            for gate in manifest["gates"]:
                if gate["applicability"] != "APPLICABLE":
                    continue
                attempts = [
                    row
                    for row in st.get("gate_runs", [])
                    if row["manifest"] == reference
                    and row["stage"] == stage
                    and row["gate"] == gate["id"]
                ]
                entry = L.find(st, attempts[-1]["evidence"]) if attempts else None
                if entry:
                    selected[entry["id"]] = binding(entry)
    return {
        "ready": not gaps,
        "gaps": sorted(set(gaps)),
        "bindings": [selected[key] for key in sorted(selected)],
    }


def independent_readiness(st):
    review = st.get("review") or {}
    approved = (
        st.get("status") == "APPROVED" and st.get("phase") == "DONE" and bool(st.get("approval"))
    )
    return {
        "implementation": {"status": st["status"], "phase": st["phase"]},
        "review": {
            "ready": approved,
            "status": "approved" if approved else "pending",
            "review_id": review.get("id"),
        },
        "release": {
            "ready": False,
            "status": "not_assessed",
            "reason": "Publication facts grant no merge, deployment or release authority.",
        },
    }
