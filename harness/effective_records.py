"""Current semantic records derived only from explicit committed application relationships."""

import json
from dataclasses import asdict, dataclass, replace

from harness.authoring_contract import AuthoringConflict
from harness.state_models import ProposalStatus, QuestionStatus


@dataclass
class EffectiveRecords:
    questions: list
    proposals: list
    historical_questions: list
    historical_proposals: list
    question_successors: dict
    proposal_successors: dict
    question_roots: dict
    proposal_roots: dict
    applications: list
    registry: dict

    def current(self, kind, ident):
        links = self.proposal_successors if kind == "proposal" else self.question_successors
        visited = set()
        while ident in links:
            if ident in visited:
                raise ValueError("Cyclic effective record lineage")
            visited.add(ident)
            ident = links[ident]
        return ident

    def require_current(self, kind, ident):
        current = self.current(kind, ident)
        if current != ident:
            prefix = "recommendation" if kind == "proposal" else "question-response"
            raise AuthoringConflict(
                "Retired target; use the current target", current_target=f"{prefix}:{current}"
            )
        return current

    def metadata(self):
        return {
            "question_successors": {str(k): v for k, v in self.question_successors.items()},
            "proposal_successors": {str(k): v for k, v in self.proposal_successors.items()},
            "question_roots": {str(k): v for k, v in self.question_roots.items()},
            "proposal_roots": {str(k): v for k, v in self.proposal_roots.items()},
            "applications": self.applications,
            "registry": {str(k): v for k, v in self.registry.items()},
        }


def effective_records(ws):
    questions, proposals = ws.questions(), ws.proposals()
    qrows, prows = {q.id: q for q in questions}, {p.id: p for p in proposals}
    result = EffectiveRecords([], [], questions, proposals, {}, {}, {}, {}, [], {})
    events = ws.events()
    for event in events:
        payload = event.payload
        if event.kind in {"decision.recorded", "decision.superseded"} and payload.get("proposal"):
            pid = payload["proposal"]
            if pid not in prows:
                raise ValueError("Registry binding references a foreign proposal")
            result.registry[pid] = {key: payload[key] for key in ("id", "version", "path")}
            result.registry[pid]["status"] = payload.get("status", "approved")
        if event.kind != "authoring.record_applied":
            continue
        allowed = {"target", "contribution", "original", "question", "proposal", "registry"}
        if (
            not isinstance(payload, dict)
            or not {"target", "contribution", "original"} <= set(payload)
            or set(payload) - allowed
        ):
            raise ValueError("Malformed effective application relationship")
        target, original = payload["target"], payload["original"]
        if (
            not isinstance(target, str)
            or not isinstance(original, dict)
            or original.get("task_id") != ws.task_id
        ):
            raise ValueError("Foreign effective application relationship")
        try:
            prefix, raw_id = target.split(":")
            prior = int(raw_id)
        except (ValueError, TypeError) as error:
            raise ValueError("Malformed effective target") from error
        if original.get("id") != prior or type(prior) is not int:
            raise ValueError("Application original does not match target")
        contribution = ws.conn.execute(
            "SELECT kind,actor,payload_json FROM authoring_contributions WHERE task_id=? AND id=?",
            (ws.task_id, payload["contribution"]),
        ).fetchone()
        if (
            not contribution
            or contribution["actor"] != "developer"
            or contribution["kind"] not in {"record_revision", "question_revision"}
            or json.loads(contribution["payload_json"]).get("target") != target
        ):
            raise ValueError("Application contribution is not a task-owned matching correction")
        rows = prows if prefix == "recommendation" else qrows
        if prior not in rows or set(original) != set(asdict(rows[prior])):
            raise ValueError("Malformed application original record")
        if prefix == "question":
            if set(payload) != {"target", "contribution", "original"}:
                raise ValueError("Malformed wording application")
        elif prefix == "question-response":
            if set(payload) != {"target", "contribution", "original", "question"}:
                raise ValueError("Malformed response application")
            _link(result, "question", prior, payload.get("question"), qrows)
        elif prefix == "recommendation":
            new = payload.get("proposal")
            _link(result, "proposal", prior, new, prows)
            if payload.get("registry") is not None:
                binding = payload["registry"]
                if not isinstance(binding, dict) or set(binding) != {
                    "id",
                    "version",
                    "path",
                    "status",
                }:
                    raise ValueError("Malformed registry application binding")
                result.registry[new] = dict(binding)
            before, after = result.registry.get(prior), result.registry.get(new)
            if (before is None) != (after is None):
                raise ValueError("Application cannot create or lose independent registry authority")
            if before and (
                before["id"] != after["id"] or after["version"] != before["version"] + 1
            ):
                raise ValueError(
                    "Registry application lineage must advance the same decision version"
                )
            # Earlier application records created an unnecessary duplicate question.
            # It is historical; the recommendation remains linked to its original semantic question.
            duplicate = payload.get("question")
            if duplicate is not None:
                original_q = prows[prior].question_id
                if original_q is None:
                    raise ValueError("Unexpected application question")
                _link(result, "question", duplicate, result.current("question", original_q), qrows)
        else:
            raise ValueError("Unsupported effective application target")
        result.applications.append(dict(payload))
    for ident in result.question_successors:
        result.current("question", ident)
    for ident in result.proposal_successors:
        result.current("proposal", ident)
    current_proposals = [p for p in proposals if p.id not in result.proposal_successors]
    for proposal in current_proposals:
        qid = result.current("question", proposal.question_id) if proposal.question_id else None
        result.proposals.append(replace(proposal, question_id=qid))
    for question in questions:
        if question.id in result.question_successors:
            continue
        ancestors = [q for q in questions if result.current("question", q.id) == question.id]
        keys = {q.catalogue_key for q in ancestors if q.catalogue_key}
        if len(keys) > 1:
            raise ValueError("Conflicting effective catalogue identities")
        current = replace(question, catalogue_key=next(iter(keys), None))
        linked = [p for p in result.proposals if p.question_id == current.id]
        if (
            current.status is QuestionStatus.ANSWERED
            and current.answer is None
            and not any(
                p.status in {ProposalStatus.ACCEPTED, ProposalStatus.MODIFIED} for p in linked
            )
        ):
            current = replace(current, status=QuestionStatus.OPEN)
        result.questions.append(current)
    return result


def _link(result, kind, prior, successor, rows):
    if (
        type(prior) is not int
        or type(successor) is not int
        or prior not in rows
        or successor not in rows
    ):
        raise ValueError("Effective lineage must reference task-owned records")
    links = result.proposal_successors if kind == "proposal" else result.question_successors
    roots = result.proposal_roots if kind == "proposal" else result.question_roots
    if prior == successor or prior in links or successor in links:
        raise ValueError("Branching or invalid effective lineage")
    links[prior] = successor
    roots[successor] = roots.get(prior, prior)
    result.current(kind, prior)
