"""Complete task-owned revision targets and effective authoring input; no UI pagination."""

import json
from dataclasses import asdict

from harness.authoring_contract import AuthoringConflict, digest, exact, text


def contributions(service):
    result = []
    for row in service.conn.execute(
        "SELECT * FROM authoring_contributions WHERE task_id=? ORDER BY rowid", (service.task_id,)
    ):
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        result.append(value)
    return result


def response_value(question):
    return {
        "mode": "defer" if question.skip_source else "answer" if question.answer else "unknown",
        "answer": question.answer or "",
        "source": question.skip_source,
        "ref": question.skip_reason if question.skip_source else None,
    }


def projection(service):
    records = contributions(service)
    from harness.effective_records import effective_records

    semantic = effective_records(service.controller.workspace(service.task_id))
    from harness.investigation_lineage import source_lineage

    source = source_lineage(service.controller, service.task_id, semantic)
    retired = set(source["retired_ids"])
    replacements = source["replacements"]
    superseded = {row["supersedes"] for row in records if row["supersedes"]}
    targets = []
    ws = service.controller.workspace(service.task_id)
    for question in ws.questions():
        original = asdict(question)
        targets.extend(
            [
                _target(
                    f"question:{question.id}",
                    "question_wording",
                    original,
                    question.text,
                    "questions",
                    records,
                ),
                _target(
                    f"question-response:{question.id}",
                    "question_response",
                    original,
                    response_value(question),
                    "questions",
                    records,
                ),
            ]
        )
    for proposal in ws.proposals():
        original = asdict(proposal)
        targets.append(
            _target(
                f"recommendation:{proposal.id}",
                "recommendation_decision",
                original,
                {"status": proposal.status, "response": proposal.response or ""},
                "proposals",
                records,
            )
        )
    by_id = {row["id"]: row for row in records}
    for row in records:
        if (
            row["actor"] != "developer"
            or row["supersedes"]
            or row["kind"]
            in {
                "question_revision",
                "record_revision",
                "record_application",
                "wording_applied",
                "reconciliation",
            }
        ):
            continue
        head = row
        lineage = [row["id"]]
        while head["id"] in superseded:
            head = next(item for item in records if item["supersedes"] == head["id"])
            lineage.append(head["id"])
        targets.append(
            {
                "target": row["id"],
                "kind": "contribution",
                "revision": head["id"],
                "original": row["payload"],
                "effective": head["payload"],
                "provenance": {
                    "kind": "contribution",
                    "id": row["id"],
                    "actor": row["actor"],
                    "created_at": row["created_at"],
                },
                "contribution": head["id"],
                "status": "pending",
                "applied": None,
                "lineage": lineage,
            }
        )
    effective = []
    for row in records:
        if row["id"] in superseded:
            continue
        item = dict(row)
        lineage = []
        parent = row["supersedes"]
        while parent:
            lineage.append(parent)
            parent = by_id[parent]["supersedes"]
        item["lineage"] = list(reversed(lineage))
        effective.append(item)
    for target in targets:
        prefix, _, raw_id = target["target"].partition(":")
        if prefix in {"question", "question-response", "recommendation"}:
            ident = int(raw_id)
            kind = "proposal" if prefix == "recommendation" else "question"
            current = semantic.current(kind, ident)
            links = (
                semantic.proposal_successors if kind == "proposal" else semantic.question_successors
            )
            roots = semantic.proposal_roots if kind == "proposal" else semantic.question_roots
            target.update(
                current_target=f"{prefix}:{current}",
                superseded_by=f"{prefix}:{links[ident]}" if ident in links else None,
                editable=current == ident,
                root_target=f"{prefix}:{roots.get(ident, ident)}",
            )
            retired_question = semantic.proposals if kind == "proposal" else None
            qid = (
                next((p.question_id for p in retired_question if p.id == ident), None)
                if retired_question is not None
                else ident
            )
            if qid in retired:
                replacement = replacements[str(qid)]
                target.update(
                    editable=False,
                    current_target=f"question-response:{replacement}",
                    source_superseded_by=f"question-response:{replacement}",
                    source_reason="source_premise_changed",
                )
        else:
            # Legacy contribution revisions remain readable, but semantic answers/decisions
            # must follow their current structured target after application.
            original = target["original"]
            qid, pid = original.get("question"), original.get("proposal")
            if qid is None and pid is not None:
                qid = next(
                    (p.question_id for p in semantic.historical_proposals if p.id == pid), None
                )
            current = (
                f"question-response:{semantic.current('question', qid)}"
                if qid in semantic.question_successors
                else f"recommendation:{semantic.current('proposal', pid)}"
                if pid in semantic.proposal_successors
                else target["target"]
            )
            target.update(
                current_target=current,
                superseded_by=current if current != target["target"] else None,
                editable=current == target["target"],
                root_target=target["target"],
            )
            if qid in retired:
                target.update(
                    editable=False,
                    current_target=f"question-response:{replacements[str(qid)]}",
                    source_reason="source_premise_changed",
                )
    return {
        "revision_targets": targets,
        "contributions": effective,
        "lineage": semantic.metadata(),
        "source_lineage": source,
    }


def _target(ident, kind, original, initial, table, records):
    history = []
    relevant = set()
    for row in records:
        payload = row["payload"]
        direct = (
            row["kind"] in {"record_revision", "question_revision"}
            and payload.get("target") == ident
        )
        response = (
            kind == "question_response"
            and row["kind"] == "answer"
            and payload.get("question") == original["id"]
        )
        decision = (
            kind == "recommendation_decision"
            and row["kind"] == "decision"
            and payload.get("proposal") == original["id"]
        )
        if direct or response or decision or row["supersedes"] in relevant:
            relevant.add(row["id"])
            history.append(row)
    head = history[-1] if history else None
    applied = next(
        (
            row
            for row in reversed(records)
            if row["kind"] == "record_application"
            and head
            and row["payload"].get("contribution") == head["id"]
        ),
        None,
    )
    effective = initial
    for item in history:
        payload = item["payload"]
        if kind == "question_wording":
            effective = payload["body"]
        elif "value" in payload:
            effective = payload["value"]
        elif item["kind"] == "answer":
            effective = {key: payload[key] for key in ("mode", "answer", "source", "ref")}
        elif item["kind"] == "decision":
            effective = {key: payload[key] for key in ("status", "response")}
        elif "body" in payload:
            effective = dict(effective)
            if kind == "recommendation_decision":
                effective["response"] = payload["body"]
            elif effective["mode"] == "defer":
                effective["ref"] = payload["body"]
            else:
                effective.update(mode="answer", answer=payload["body"], source=None, ref=None)
    if applied is None and head and kind == "question_wording" and effective == initial:
        after_head = records[records.index(head) + 1 :]
        legacy_application = next(
            (
                row
                for row in reversed(after_head)
                if row["kind"] == "wording_applied"
                and row["payload"].get("original", {}).get("id") == original["id"]
                and row["payload"].get("text") == effective
            ),
            None,
        )
        if legacy_application:
            applied = {
                "payload": {
                    "target": ident,
                    "contribution": head["id"],
                    "original": legacy_application["payload"]["original"],
                }
            }
    # Contributions that exactly match committed records are already applied through the domain API.
    initial_applied = head and head["kind"] in {"answer", "decision"} and effective == initial
    return {
        "target": ident,
        "kind": kind,
        "revision": head["id"] if head else digest(original),
        "original": original,
        "effective": effective,
        "provenance": {
            "kind": "structured_record",
            "table": table,
            "task": original["task_id"],
            "id": original["id"],
            "created_at": original["created_at"],
        },
        "contribution": head["id"] if head else None,
        "status": "applied" if applied or initial_applied else "pending" if head else "recorded",
        "applied": applied["payload"] if applied else None,
        "lineage": [row["id"] for row in history],
    }


def resolve_target(service, target, target_revision):
    text(target, "target", 100)
    text(target_revision, "target_revision", 200)
    found = next(
        (row for row in projection(service)["revision_targets"] if row["target"] == target), None
    )
    if found is None:
        raise LookupError("unknown task revision target")
    if not found["editable"]:
        raise AuthoringConflict(
            "Retired target; use the current target", current_target=found["current_target"]
        )
    if found["revision"] != target_revision:
        raise AuthoringConflict("target revision changed")
    return found


def validate_value(service, kind, value):
    if kind == "question_response":
        exact(value, {"mode", "answer", "source", "ref"})
        mode = value["mode"]
        if not isinstance(mode, str) or mode not in {"answer", "unknown", "defer"}:
            raise ValueError("invalid response mode")
        text(value["answer"], "answer", 16000, empty=mode != "answer")
        from harness.interview import _is_non_answer

        if mode == "answer" and _is_non_answer(value["answer"]):
            raise ValueError("use unknown for non-answer")
        if mode != "answer" and value["answer"]:
            raise ValueError("non-answer mode cannot carry answer")
        if mode != "defer":
            if value["source"] is not None or value["ref"] is not None:
                raise ValueError("non-deferral cannot carry skip fields")
        else:
            if not isinstance(value["source"], str) or value["source"] not in {
                "decision",
                "codebase",
                "not_relevant",
            }:
                raise ValueError("unsupported deferral source")
            text(value["ref"], "ref", 16000)
            if value["source"] == "decision":
                from harness.interview import InterviewEngine

                engine = InterviewEngine(service.controller, service.task_id)
                if value["ref"] not in engine._approved_decision_ids():
                    raise ValueError("deferral requires an approved decision")
    elif kind == "recommendation_decision":
        exact(value, {"status", "response"})
        if not isinstance(value["status"], str) or value["status"] not in {
            "accepted",
            "rejected",
            "modified",
        }:
            raise ValueError("invalid decision status")
        text(value["response"], "response", 16000, empty=value["status"] != "modified")
    else:
        raise ValueError("target requires the existing revise action")


def question_presentation(question, request):
    from harness.interview import scanner_scope

    technical_scope = scanner_scope(question)
    legacy_scope = technical_scope and not question.text.startswith("For ")
    return {
        "display_text": (
            f"For {request[:160]!r}, what outcome should users get, "
            "and which product area is affected? "
            "Exact code paths are optional. A ticket ID alone does not include its requirements."
            if legacy_scope
            else question.text
        ),
        "discovery_note": (
            "Technical discovery: the scanner has not proven code ownership. "
            "You can describe the behavior you want or choose I don’t know yet; "
            "this does not waive unresolved requirements."
            if technical_scope
            else None
        ),
    }


def model_recovery(ws, jobs):
    jobs = [job for job in jobs if job.get("kind") != "attached_publication"]
    if not jobs:
        return None
    latest = jobs[-1]
    if latest["kind"] in {"source_investigation", "jira_reconnect"}:
        return None
    if latest["state"] in {"failed", "interrupted", "stale", "cancelled"}:
        return {
            "host": latest["host"],
            "state": latest["state"],
            "message": latest.get("error") or "The interview model stopped.",
        }
    if latest["kind"] == "investigate" and latest["state"] == "succeeded":
        notice = next(
            (e for e in reversed(ws.events()) if e.kind == "authoring.model_unavailable"), None
        )
        if notice:
            return dict(notice.payload, state="unavailable")
    return None
