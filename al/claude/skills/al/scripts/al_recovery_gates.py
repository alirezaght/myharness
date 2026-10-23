"""Current gate recovery actions retain inventory and retained-phase guards."""

import al_ledger as L
from al_recovery_types import action, supply, issue


def gates(st):
    import al_gate_currency as Currency
    import al_gates as G
    from al_gate_recovery import EXCEPTION, marker

    currency = Currency.check(st)
    if not currency["current"]:
        legal = st["phase"] in {"PLANNING", "VERIFICATION"}
        if st["phase"] in EXCEPTION:
            if st.get("gate_recovery"):
                marker(st)
                legal = True
            else:
                legal = st.get("spec_status") == "SPEC_READY"
        recovery = supply(
            "gate-discovery-paths",
            "Supply exact selected and anticipated paths for ordinary replacement discovery.",
            "gates.discover" if legal else None,
            {},
        )
        return [
            issue(
                "GATE_INVENTORY_STALE",
                "inventory",
                "gates",
                "Current gate obligations need supported discovery/planning.",
                recovery,
                currency,
            )
        ]
    manifest = G.manifest(st)
    from al_gate_continuation import _states, _attempt

    result = []
    for stage in ("base", "candidate"):
        states, _ = _states(st, manifest, stage)
        for gate in manifest["gates"]:
            state = states[gate["id"]]
            if state in {"valid", "not-required", "accepted-base-decision", "unverified"}:
                continue
            subject = stage + ":" + gate["id"]
            attempt, association = _attempt(st, stage, gate)
            if state == "unresolved":
                recovery = supply(
                    "gate-obligation-resolution",
                    "Resolve the unsupported declaration or owned reservation through its ordinary guard; no check can be marked N/A by guidance.",
                )
                code = "GATE_UNRESOLVED"
            else:
                recovery = action(
                    "gates.run",
                    {
                        "stage": stage,
                        "id": gate["id"],
                        "resume": True,
                        "retry_failed": state == "failed",
                        "timeout": 300.0,
                    },
                )
                code = "GATE_FAILED" if state == "failed" else "GATE_RUN_REQUIRED"
            result.append(
                issue(
                    code,
                    "gate",
                    subject,
                    "Gate " + subject + " is " + state + ".",
                    recovery,
                    {
                        "gate": gate,
                        "attempt": attempt,
                        "association": association,
                        "state": state,
                        "environment": L.scope_fingerprint([], gate["env_names"]),
                        "manifest": st["gate_manifests"][-1],
                    },
                )
            )
    return result
