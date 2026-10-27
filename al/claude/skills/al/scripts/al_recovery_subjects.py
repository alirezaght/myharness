"""Observed recovery subjects; guidance names no unsupported operation."""

import al_ledger as L
from al_schema import refuse


from al_recovery_types import action, supply, issue
from al_recovery_gates import gates
from al_recovery_prerequisites import prerequisites


def operations(st):
    result = []
    for writer in st["writers"].values():
        if writer["state"] in {"ACTIVE", "UNRESOLVED"}:
            result.append(
                issue(
                    "WRITER_UNRESOLVED",
                    "writer",
                    writer["id"],
                    "The owned writer lifetime is not resolved.",
                    (
                        supply(
                            "owned-writer-completion",
                            "Allow the actual admitted writer to finish; never reconcile an active owner.",
                        )
                        if writer["state"] == "ACTIVE"
                        else supply(
                            "writer-reconciliation-evidence",
                            "Supply the inspected writer termination artifact and rationale.",
                            "writer.reconcile",
                            {"id": writer["id"]},
                        )
                    ),
                    writer,
                )
            )
    for entry in st["evidence"]["entries"]:
        cleanup = entry.get("cleanup") or {}
        uncertain = (
            cleanup.get("errors") or cleanup.get("unresolved") or cleanup.get("complete") is False
        )
        unresolved = (
            entry["status"] == "RUNNING"
            or entry.get("interrupted")
            and not entry.get("reconciliation")
        )
        if not uncertain and not unresolved:
            continue
        observed = {
            "entry": entry,
            "owner_alive": L._alive(entry.get("owner_pid")),
            "child_alive": L._alive(entry.get("pid")),
        }
        if uncertain:
            recovery = supply(
                "owned-cleanup-resolution",
                "Provide positive owned-process cleanup proof; reconciliation alone cannot waive uncertainty.",
            )
        elif observed["owner_alive"] or observed["child_alive"]:
            recovery = supply(
                "owned-operation-completion",
                "Wait for the actual owned operation to finish; never reconcile a live process.",
            )
        else:
            recovery = supply(
                "check-reconciliation-evidence",
                "Supply the inspected interruption/reconciliation artifact and rationale.",
                "check.reconcile",
                {"id": entry["id"]},
            )
        result.append(
            issue(
                "CHECK_UNRESOLVED",
                "check",
                entry["id"],
                "The owned check is unresolved.",
                recovery,
                observed,
            )
        )
    return result


def planning(st):
    admission = st.get("proof_admission")
    if not admission or admission["status"] != "pending":
        return []
    recovery = supply(
        "planning-prerequisites",
        "Supply a ready specification and classification before requesting Architect planning provenance.",
    )
    try:
        from al_planning import ready

        ready(st)
    except SystemExit:
        pass
    else:
        current = st.get("planning_authorship")
        if not current:
            recovery = action("planning.packet", {"replace_unavailable_attempt": False})
        else:
            from al_planning import packet

            try:
                packet(st)
            except SystemExit as exc:
                if "planning source observation changed" in str(exc):
                    recovery = action("planning.packet", {"replace_unavailable_attempt": False})
                else:
                    recovery = supply(
                        "planning-provenance-inspection",
                        "Inspect the immutable planning provenance; guidance cannot replace corrupt proof.",
                    )
            else:
                if "report" in current:
                    recovery = action("planning.admit", {"file": None})
                else:
                    recovery = supply(
                        "architect-planning-report-v1",
                        "Supply the external Architect report matching the issued immutable planning packet.",
                        "planning.report",
                        {},
                    )
    return [
        issue(
            "PROOF_ADMISSION_PENDING",
            "planning",
            str(admission["planning_occurrence"]),
            "Current task-owned Architect obligations are not admitted.",
            recovery,
            {
                "admission": admission,
                "provenance": st.get("planning_authorship"),
                "spec_status": st["spec_status"],
                "classification": st.get("classification"),
                "unavailable_models": st.get("unavailable_models", []),
            },
        )
    ]


def remaining(st):
    from al_task_attribution import classify_changes

    result = []
    if "task_attribution" in st:
        classification = classify_changes(st)
        if classification["ambiguous"]:
            result.append(
                issue(
                    "ATTRIBUTION_AMBIGUOUS",
                    "attribution",
                    "candidate",
                    "Concurrent content attribution requires explicit reconciliation.",
                    supply(
                        "task-attribution-reconciliation-request-v1",
                        "Supply a current content-bound overlap decision; never narrow unknown ownership.",
                        "task.reconcile-attribution",
                        {},
                    ),
                    classification,
                )
            )
    for blocker in st.get("integration", {}).get("blockers", {}).values():
        if blocker.get("state") != "Resolved":
            identifier = blocker.get("id")
            if not isinstance(identifier, str):
                refuse("unknown developer blocker identity")
            result.append(
                issue(
                    "DEVELOPER_DECISION_REQUIRED",
                    "decision",
                    identifier,
                    "An acknowledged scoped developer decision is required.",
                    supply(
                        "acknowledged-developer-decision",
                        "Use the existing Harness decision delivery and acknowledgement path for this exact blocker.",
                    ),
                    blocker,
                )
            )
    review = st.get("review")
    if review and st["phase"] == "REVIEW" and review["stage"] in {"fresh", "comparison"}:
        stage = "fresh" if review["stage"] == "fresh" else "compare"
        if stage not in review["packets"]:
            recovery = action(
                "review.packet",
                {"stage": stage, "replace_unavailable_attempt": False, "output": None},
            )
        else:
            recovery = supply(
                "architect-review-report",
                "Supply the external independent report matching the current issued packet.",
                "review.fresh" if stage == "fresh" else "review.result",
                {},
            )
        result.append(
            issue(
                "REVIEW_INPUT_REQUIRED",
                "review",
                review["id"] + ":" + stage,
                "The independent " + stage + " review stage is incomplete.",
                recovery,
                review,
            )
        )
    return result


def issues(st):
    unresolved = operations(st)
    if unresolved:
        return unresolved
    recovery=st.get('rework_recovery')
    if recovery and recovery['stop'] and st['status']=='STOPPED_WITH_BLOCKERS':
        if recovery['stop']['kind']=='model':
            return [issue('REWORK_MODEL_BLOCKED','rework-recovery',recovery['stop']['occurrence_id'],
                          recovery['stop']['reason'],supply('approved-model-availability',
                          'No approved model remains. Budget continuation cannot clear a model-availability blocker.'),recovery['stop'])]
        return [issue('REWORK_RECOVERY_STOPPED','rework-recovery',recovery['stop']['occurrence_id'],
                      recovery['stop']['reason'],supply('rework-continuation-input',
                      'Inspect the latest stopping checkpoint; use rework-recovery continue --file PATH with explicit user authorization.'),recovery['stop'])]
    if st["status"] != "RUNNING":
        return [
            issue(
                "RUN_NOT_RUNNING",
                "run",
                st["run_id"],
                "The run requires its ordinary explicit recovery edge.",
                supply(
                    "run-recovery-input",
                    "Inspect the stopping checkpoint and agent-failure obligations before using guarded resume/retry.",
                ),
                {
                    "status": st["status"],
                    "checkpoint": st.get("stopping_checkpoint"),
                    "failure": st.get("failure"),
                },
            )
        ]
    recovery=st.get('rework_recovery')
    if recovery and recovery['pending']:
        return [issue('REWORK_REASSESSMENT_REQUIRED','rework-recovery',recovery['pending'],
                      'The exhausted batch requires a bound Architect decision.',
                      supply('rework-recovery-input','Use rework-recovery packet, then rework-recovery decide --file PATH.'),recovery)]
    prerequisites_missing = prerequisites(st)
    if prerequisites_missing:
        return prerequisites_missing
    pending = planning(st)
    if pending:
        return pending
    return remaining(st) + gates(st)
