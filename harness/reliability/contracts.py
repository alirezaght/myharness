"""Closed metadata contracts for the operation boundary sensor."""

COVERAGE = {
    "source": "operation-dispatch",
    "early_transport_rejections": False,
    "epoch_rejections": False,
    "assignment_wait_observation": False,
    "historical_backfill": False,
    "async_completion_classification": False,
}
TOOLS = frozenset(
    {
        "read_file",
        "write_artifact",
        "submit_output",
        "apply_patch",
        "run_command",
        "poll_process",
        "cancel_process",
        "al_command",
        "diagnostic",
        "delivery_operation",
        "environment_operation",
        "diagram_operation",
        "knowledge_query",
        "knowledge_explain",
    }
)
SYMPTOMS = {
    "AUTHORITY_REJECTED": "Operation authority was rejected",
    "DISPATCH_REJECTED": "Operation dispatch was rejected",
    "DISPATCH_OS_ERROR": "Operating system operation failed",
    "DISPATCH_PROCESS_ERROR": "Subprocess operation failed",
    "DISPATCH_INTERNAL_ERROR": "Internal dispatch failed",
    "RETURNED": "Dispatch returned",
}
TERMINALS = {
    "AUTHORITY_REJECTED": "denied",
    "DISPATCH_REJECTED": "denied",
    "DISPATCH_OS_ERROR": "failed",
    "DISPATCH_PROCESS_ERROR": "failed",
    "DISPATCH_INTERNAL_ERROR": "failed",
    "RETURNED": "succeeded",
}
ROLES = frozenset(
    {
        "orchestrator",
        "al-architect",
        "al-architect-fallback",
        "al-designer",
        "al-designer-fallback",
        "al-engineer",
        "al-builder",
        "al-scout",
    }
)
