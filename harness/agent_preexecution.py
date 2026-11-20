"""Typed companions of developer-controlled pre-execution records and checkpoints."""

from dataclasses import asdict

from harness import agent_contracts as C


def binding(controller, task_id):
    task = controller.get_task(task_id)
    values = dict(
        al_run=None,
        al_session=None,
        task=task.id,
        execution=None,
        subtask=None,
        graph_revision=None,
        task_revision=None,
        producer=None,
        epoch=None,
        authority_generation=None,
    )
    values["applicability"] = {k: "pre-execution" for k, v in values.items() if v is None}
    specification = {
        "reference": {"kind": "task-request", "id": task.id, "sha256": C.fingerprint(task.request)},
        "amendments": [],
    }
    return values, specification


def produce(controller, task_id, kind, record_id, record):
    context, spec = binding(controller, task_id)
    return C.make(kind, context, spec, record_id, record)


def consume(controller, task_id, kind, record, value):
    context, spec = binding(controller, task_id)
    C.validate(value, context=context, specification=spec)
    if value["kind"] != kind or value["payload"]["record_digest"] != C.fingerprint(record):
        raise C.ContractError("/payload", kind=kind)
    return value


def records(rows):
    return [asdict(row) for row in rows]


def checked_view(controller, task_id, kind, record_id, record):
    """Construct from current authoritative rows and consume before exposing the view."""
    value = produce(controller, task_id, kind, record_id, record)
    return consume(controller, task_id, kind, record, value)


def decision_data(record):
    from harness.investigation import _decision_dict

    return _decision_dict(record) | {
        "decision": record.decision,
        "rationale": record.rationale,
        "impact": record.impact,
        "superseded_by": record.superseded_by,
    }
