"""Read-only source-premise succession layered over validated developer corrections."""

import json


def source_lineage(controller, task_id, effective=None, *, rows=None):
    from harness.effective_records import effective_records
    from harness.investigation_store import schema_available

    ws = controller.workspace(task_id)
    effective = effective if effective is not None else effective_records(ws)
    physical = {q.id: q for q in ws.questions()}
    if rows is None:
        rows = (
            controller.conn.execute(
                "SELECT * FROM investigation_decisions WHERE task_id=? ORDER BY revision",
                (task_id,),
            ).fetchall()
            if schema_available(controller.conn)
            else []
        )
    groups = {}
    seen_questions = set()
    for item in rows:
        row = dict(item)
        if (
            row["task_id"] != task_id
            or type(row["question_id"]) is not int
            or row["question_id"] not in physical
            or row["question_id"] in seen_questions
        ):
            raise ValueError("Invalid or foreign source decision lineage")
        if type(row["revision"]) is not int or row["revision"] < 1:
            raise ValueError("Invalid source decision revision")
        seen_questions.add(row["question_id"])
        groups.setdefault(row["concern"], []).append(row)

    def chain(ident):
        result = []
        while True:
            if ident in result or ident not in physical:
                raise ValueError("Cyclic or foreign source correction lineage")
            result.append(ident)
            if ident not in effective.question_successors:
                return result
            ident = effective.question_successors[ident]

    retired = set()
    replacements = {}
    relationships = []
    decisions = []
    for concern, history in sorted(groups.items()):
        history.sort(key=lambda r: r["revision"])
        prior = None
        old_ids = set()
        for index, row in enumerate(history):
            if (
                row["revision"] != index + 1
                or (prior is None and row["supersedes"] is not None)
                or (prior is not None and row["supersedes"] != chain(prior["question_id"])[-1])
            ):
                raise ValueError("Contradictory source decision lineage")
            if prior is not None:
                old_ids.update(chain(prior["question_id"]))
            prior = row
        head = history[-1]
        current = chain(head["question_id"])[-1]
        if current in old_ids:
            raise ValueError("Source decision successor is also retired")
        retired.update(old_ids)
        for ident in sorted(old_ids):
            if ident in replacements and replacements[ident] != current:
                raise ValueError("Contradictory source replacement")
            replacements[ident] = current
            relationships.append(
                {
                    "concern": concern,
                    "question": ident,
                    "current_question": current,
                    "reason": "source_premise_changed",
                }
            )
        decisions.append(
            head
            | {
                "original_question_id": head["question_id"],
                "question_id": current,
                "premise": json.loads(head["premise_json"]),
                "retired_questions": sorted(old_ids),
            }
        )
    if len({row["question_id"] for row in decisions}) != len(decisions):
        raise ValueError("Source concerns share a contradictory current decision")
    if any(row["question_id"] in retired for row in decisions):
        raise ValueError("Current source decision is retired by another concern")
    return {
        "decisions": decisions,
        "retired_ids": sorted(retired),
        "replacements": {str(k): v for k, v in replacements.items()},
        "relationships": relationships,
    }
