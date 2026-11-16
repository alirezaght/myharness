"""Shared, conservative task guidance for list and selected-task views."""

ACTIVE = {"Running", "Launching", "WorkerOwned"}


def task_guidance(
    execution, launch, *, pending=False, question=False, phase=None, has_approval=None
):
    def shown(status, activity, next_action, target):
        return {
            "status": status,
            "activity": activity,
            "next_action": next_action,
            "target": target,
        }

    if not execution:
        if launch.get("binding"):
            return shown(
                "Ready to start a Harness run",
                "No run is attached to this task; external-session work isn't tracked here.",
                "Start implementation",
                "start",
            )
        if pending or question:
            return shown(
                "Your decision needed",
                "Requirements need your input before a Harness run can start.",
                "Review requirements and approvals",
                "approvals",
            )
        if has_approval is False and phase is not None:
            return shown(
                "Specification in progress",
                f"Requirements are in the {phase} phase; no implementation approval is recorded.",
                "Review requirements and next steps",
                "approvals",
            )
        return shown(
            "Status unavailable",
            launch.get("diagnostic") or "Current approval could not be validated.",
            "Review requirements and approval diagnostics",
            "approvals",
        )
    blockers = {item["id"]: item for item in execution.get("blockers", [])}
    decisions = [
        blockers.get(item) if isinstance(item, str) else item
        for item in execution.get("developer_decisions", list(blockers.values()))
    ]
    if any(item is not None and item.get("state") != "Resolved" for item in decisions):
        return shown(
            "Your decision needed",
            "A developer decision is recorded for this run.",
            "Review the decision",
            "execution-decisions",
        )
    status = execution["status"]
    if status == "Completed" and execution.get("verification", {}).get("status") == "complete":
        return shown(
            "Verified complete",
            "The attached run records completed verification.",
            "Review results",
            "execution",
        )
    if status in ACTIVE:
        return shown(
            "Starting implementation" if status != "Running" else "Work in progress",
            "The attached Harness run is active.",
            "View activity and task controls",
            "execution",
        )
    if status == "Reserved":
        return shown(
            "Starting implementation",
            "A Harness run is reserved. Inspect its launch result before retrying.",
            "Inspect task controls",
            "execution",
        )
    if status == "Unavailable":
        return shown(
            "Status unavailable",
            execution.get("status_explanation")
            or execution.get("error")
            or "The run's prerequisites are unavailable.",
            "Inspect diagnostics and recovery controls",
            "execution",
        )
    if status in {"Interrupted", "RecoveryRequired", "Failed"}:
        return shown(
            "Work stopped · action needed",
            execution.get("status_explanation")
            or execution.get("error")
            or (
                "The run is inactive. Recorded agent statuses describe past activity; "
                "checks remain unverified."
            ),
            "Review task controls",
            "execution",
        )
    if execution.get("verification", {}).get("status") == "incomplete":
        return shown(
            "Checks incomplete",
            "The run is inactive; remaining checks are not verified.",
            "Review checks and recovery controls",
            "execution",
        )
    return shown(
        "Work stopped · action needed",
        execution.get("status_explanation")
        or "The run is inactive. Recorded agent statuses describe past activity.",
        "Review task controls",
        "execution",
    )
