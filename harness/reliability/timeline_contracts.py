"""Closed timeline vocabulary; semantic reports are never independent runtime proof."""

PROCESS_KINDS = frozenset(
    {
        "process.launch_pending",
        "process.started",
        "process.launch_failed",
        "process.cancel_requested",
        "process.timeout_observed",
        "process.exited",
        "process.cleanup_unconfirmed",
    }
)
KINDS = PROCESS_KINDS | {
    "operation.started",
    "operation.returned",
    "operation.denied",
    "operation.failed",
    "semantic.agent",
    "semantic.task",
    "semantic.check",
    "semantic.decision",
    "semantic.output",
    "semantic.run",
    "retrieval.bundle",
    "policy.allowed",
    "policy.denied",
    "policy.started",
    "policy.settled",
    "policy.unresolved",
    "policy.capability_issued",
    "policy.capability_revoked",
}
COVERAGE = {
    "version": 1,
    "operation_dispatch": True,
    "owned_process_lifecycle": True,
    "validated_semantic_links": True,
    "authorized_retrieval_references": True,
    "early_transport_rejections": False,
    "historical_backfill": False,
    "durable_process_recovery": False,
    "native_child_observation": False,
}
MEASUREMENTS = {
    "duration_ms": "monotonic_when_observed",
    "tokens": "unavailable",
    "cost": "unavailable",
    "native_children": "unavailable",
}
SEMANTIC = {
    "agent": "semantic.agent",
    "task": "semantic.task",
    "test": "semantic.check",
    "decision": "semantic.decision",
    "output": "semantic.output",
    "run": "semantic.run",
}
