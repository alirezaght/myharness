"""Exact browser operation schemas shared with domain adapters."""

from harness.authoring_contract import exact, text

SCHEMAS = {
    "jira-handoff": {"job"},
    "investigate": {"host", "site"},
    "reconnect-jira": {"host", "site"},
    "investigation-review": set(),
    "accept-investigation": {"approved_sha256", "provenance"},
    "start": {"host"},
    "answer": {"question", "mode", "answer", "source", "ref"},
    "revise": {"target", "target_revision", "body"},
    "revise-record": {"target", "target_revision", "value"},
    "decide": {"proposal", "status", "response"},
    "followup": {"body"},
    "save-spec": {"body"},
    "regenerate": {"host"},
    "reconcile-preview": {"revision"},
    "reconcile": {"revision", "token", "confirm_graph", "acknowledge", "actions"},
    "cancel-job": {"job"},
    "retry-job": {"job", "acknowledge_unknown"},
    "planning": {"operation"},
}
PLANNING = {
    "revision-apply": {"target", "target_revision"},
    "question-ask": {"topic", "text", "decision"},
    "question-wording": {"question_id", "text"},
    "question-answer": {"question_id", "text"},
    "question-unknown": {"question_id"},
    "question-defer": {"question_id", "source", "ref"},
    "interview-propose": {"question_id", "title", "recommendation", "rationale", "impact"},
    "advance": set(),
    "back": {"phase", "reason"},
    "design-add": {"section", "text", "delegated"},
    "design-none": {"section", "reason"},
    "design-decide": {"entry_id", "status", "response"},
    "scenario-add": {"category", "text", "covers", "mock", "mock_reason"},
    "scenario-decide": {"scenario_id", "status", "response"},
    "test-plan-approve": set(),
}


def validate_planning(operation):
    if not isinstance(operation, dict) or operation.get("op") not in PLANNING:
        raise ValueError("unknown planning action")
    exact(operation, {"op"} | PLANNING[operation["op"]])
    for name in ("text", "reason", "target", "target_revision"):
        if name in operation:
            text(operation[name], name)
    for name in ("entry_id", "scenario_id", "question_id"):
        if name in operation and (type(operation[name]) is not int or operation[name] < 1):
            raise ValueError("invalid planning record identity")
    if "delegated" in operation and type(operation["delegated"]) is not bool:
        raise ValueError("delegated must be boolean")
