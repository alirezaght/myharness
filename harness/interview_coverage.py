"""Pure source coverage: prompt eligibility is separate from explicit review satisfaction."""

import hashlib
from dataclasses import dataclass

from harness.authoring_contract import digest, exact, text
from harness.classification import INTERVIEW_TOPICS
from harness.interview_catalogue import catalogue_for

DISPOSITIONS = {
    "fact",
    "interpretation",
    "not_applicable",
    "decision",
    "operational_gap",
    "conflict",
}


def validate_sources(sources):
    if not isinstance(sources, list) or not 1 <= len(sources) <= 80:
        raise ValueError("bounded acquired sources required")
    seen = {}
    for source in sources:
        exact(source, {"id", "kind", "locator", "content", "sha256", "metadata"})
        for name in ("id", "locator", "sha256"):
            text(source[name], name, 1000)
        text(source["content"], "content", 262144, empty=source["kind"] == "repository")
        if source["id"] in seen or source["kind"] not in {"ticket", "repository"}:
            raise ValueError("unique acquired source identity required")
        from harness.investigation_repository import source_record

        if (
            source_record(source["kind"], source["locator"], source["content"], source["metadata"])[
                "id"
            ]
            != source["id"]
        ):
            raise ValueError("source identity differs from its immutable observation")
        if hashlib.sha256(source["content"].encode()).hexdigest() != source["sha256"]:
            raise ValueError("source hash differs")
        if not isinstance(source["metadata"], dict):
            raise ValueError("invalid source observation")
        seen[source["id"]] = source
    return seen


def validate_citations(citations, sources):
    if not isinstance(citations, list) or not 1 <= len(citations) <= 8:
        raise ValueError("bounded exact source citations required")
    for citation in citations:
        exact(citation, {"source", "sha256", "start", "end", "quote"})
        source = sources.get(citation["source"])
        start, end = citation["start"], citation["end"]
        if (
            not source
            or source["sha256"] != citation["sha256"]
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(source["content"])
            or citation["quote"] != source["content"][start:end]
            or not citation["quote"].strip()
        ):
            raise ValueError("citation must match an actual stored source range and hash")


def validate_evidence(value, sources):
    exact(value, {"coverage", "decisions", "reads"})
    source_map = validate_sources(sources)
    from harness.investigation_concerns import DEPENDENCIES, TESTS_SETUP, definitions

    catalogue = definitions()
    if not isinstance(value["coverage"], list) or len(value["coverage"]) > len(catalogue):
        raise ValueError("bounded catalogue coverage required")
    seen = set()
    for row in value["coverage"]:
        exact(row, {"key", "disposition", "statement", "rationale", "citations"})
        if (
            row["key"] not in catalogue
            or row["key"] in seen
            or row["disposition"] not in DISPOSITIONS
        ):
            raise ValueError("invalid or duplicate coverage concern")
        seen.add(row["key"])
        if row["key"] in {TESTS_SETUP, DEPENDENCIES} and row["disposition"] == "not_applicable":
            raise ValueError(
                "absence of detected tests or manifests does not establish non-applicability"
            )
        text(row["statement"], "coverage statement", 4000)
        text(row["rationale"], "coverage rationale", 4000)
        validate_citations(row["citations"], source_map)
    if not isinstance(value["decisions"], list) or len(value["decisions"]) > 20:
        raise ValueError("bounded genuine decisions required")
    seen = set()
    for row in value["decisions"]:
        exact(row, {"concern", "text", "citations", "supersedes", "reason"})
        if row["concern"] not in catalogue or row["concern"] in seen:
            raise ValueError("decisions must identify distinct known concerns")
        seen.add(row["concern"])
        text(row["text"], "decision", 4000)
        text(row["reason"], "decision rationale", 4000)
        if row["supersedes"] is not None and (
            type(row["supersedes"]) is not int or row["supersedes"] < 1
        ):
            raise ValueError("invalid prior decision identity")
        validate_citations(row["citations"], source_map)
        mapped = next((r for r in value["coverage"] if r["key"] == row["concern"]), None)
        if not mapped or mapped["disposition"] != "decision":
            raise ValueError("genuine decision requires matching coverage disposition")
    if not isinstance(value["reads"], list) or len(value["reads"]) > 8:
        raise ValueError("bounded additional reads required")
    return value


@dataclass(frozen=True)
class EffectiveCoverage:
    enabled: bool = False
    rows: tuple = ()
    decision_ids: tuple = ()
    bundle: str | None = None
    accepted: bool = False
    acceptance: str | None = None
    limitations: tuple = ()
    retired_ids: tuple = ()

    def suppress(self, question):
        if not self.enabled:
            return False
        from harness.interview import scanner_scope
        from harness.investigation_concerns import scanner_concern

        return (
            (scanner_concern(question) in {row["key"] for row in self.rows})
            or question.id in self.retired_ids
            or question.catalogue_key is not None
            or scanner_scope(question)
        )

    @property
    def blockers(self):
        return tuple(r for r in self.rows if r["state"] != "accepted_source_evidence")

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "bundle": self.bundle,
            "accepted": self.accepted,
            "acceptance": self.acceptance,
            "concerns": list(self.rows),
            "decision_question_ids": list(self.decision_ids),
            "limitations": list(self.limitations),
        }


def derive_coverage(
    *,
    binding=None,
    bundle=None,
    entries=(),
    questions=(),
    decisions=(),
    acceptance=None,
    basis=None,
):
    if not binding:
        return EffectiveCoverage()
    content = bundle["content"] if bundle else {}
    mappings = {row["key"]: row for row in content.get("evidence", {}).get("coverage", [])}
    accepted = bool(
        bundle
        and acceptance
        and not binding.get("recovery")
        and acceptance["bundle_id"] == bundle["id"]
        and acceptance["coverage_hash"] == digest(content.get("evidence", {}))
        and acceptance["basis"] == basis
        and content.get("complete")
    )
    rows = []
    by_id = {q.id: q for q in questions}
    decision_ids = []
    for decision in decisions:
        question = by_id.get(decision["question_id"])
        if question:
            decision_ids.append(question.id)
    for entry in entries:
        mapped = mappings.get(entry.key)
        state = "operational_gap"
        if mapped:
            state = mapped["disposition"]
            if state in {"fact", "interpretation", "not_applicable"}:
                state = "accepted_source_evidence" if accepted else "pending_source_review"
            elif state == "decision":
                related = [d for d in decisions if d["concern"] == entry.key]
                question = by_id.get(related[-1]["question_id"]) if related else None
                if question and (question.status == "answered" or question.skip_source):
                    state = "accepted_source_evidence" if accepted else "pending_source_review"
        if binding.get("recovery") or not content.get("complete", False):
            state = "stale_evidence" if bundle else "operational_gap"
        rows.append(
            {
                "key": entry.key,
                "topic": entry.topic,
                "text": entry.text,
                "state": state,
                "disposition": mapped["disposition"] if mapped else "operational_gap",
                "statement": mapped["statement"] if mapped else "No source mapping supplied",
                "rationale": mapped["rationale"] if mapped else "Investigation remains incomplete",
                "citations": mapped["citations"] if mapped else [],
            }
        )
    return EffectiveCoverage(
        True,
        tuple(rows),
        tuple(decision_ids),
        bundle["id"] if bundle else None,
        accepted,
        acceptance["id"] if accepted else None,
        tuple(content.get("limitations", [])),
        tuple(q for d in decisions for q in d.get("retired_questions", [])),
    )


def coverage_entries(controller, task_id):
    from harness.classification import interview_depth
    from harness.effective_records import effective_records
    from harness.interview import scanner_scope

    task = controller.get_task(task_id)
    entries = list(catalogue_for(interview_depth(task.complexity).topics))
    if any(scanner_scope(q) for q in effective_records(controller.workspace(task_id)).questions):
        owner = next(
            e
            for e in catalogue_for(INTERVIEW_TOPICS)
            if e.key == "component_responsibilities.owner"
        )
        if owner not in entries:
            entries.append(owner)
    from harness.investigation_concerns import definitions, scanner_concern

    concerns = definitions()
    for question in effective_records(controller.workspace(task_id)).questions:
        key = scanner_concern(question)
        if key and concerns[key] not in entries:
            entries.append(concerns[key])
    return tuple(entries)


def current_coverage(controller, task_id, *, managed_policy=None):
    from harness.effective_records import effective_records
    from harness.investigation_store import InvestigationStore, review_basis

    store = InvestigationStore(controller, None, task_id)
    binding = store.binding()
    if not binding:
        return EffectiveCoverage()
    bundle = store.read_active()
    if bundle:
        from harness.investigation_artifacts import reject_source, stored

        policy = stored(controller.conn, task_id)
        captured = bundle["content"]["repository"].get("managed_policy")
        valid_policy = bool(
            policy and captured == policy and (managed_policy is None or managed_policy == policy)
        )
        if valid_policy:
            try:
                for source in bundle["content"]["sources"]:
                    if source["kind"] == "repository":
                        reject_source(policy, source["locator"])
            except ValueError:
                valid_policy = False
        if not valid_policy:
            binding = dict(binding, recovery={"reason": "managed_artifact_policy_changed"})
        from harness.investigation_repository import observation_current

        if not observation_current(
            bundle["content"]["request"]["repository"], bundle["content"]["repository"]
        ):
            binding = dict(binding, recovery={"reason": "source_changed"})
    effective = effective_records(controller.workspace(task_id))
    return derive_coverage(
        binding=binding,
        bundle=bundle,
        entries=coverage_entries(controller, task_id),
        questions=effective.questions,
        decisions=store.decisions(effective),
        acceptance=store.acceptance(),
        basis=review_basis(controller, task_id),
    )
