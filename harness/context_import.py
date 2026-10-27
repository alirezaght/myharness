"""Import supplied reviewed context as source-bound proposals, never silent approval."""

import hashlib
import re

from harness.classification import INTERVIEW_TOPICS
from harness.db import transaction
from harness.design_models import Binding, DesignSection
from harness.execution_contract import digest
from harness.interview_catalogue import catalogue_for
from harness.state_models import QuestionKind
from harness.testplan_models import TestCategory


def import_context(controller, task_id, value):
    required = {"schema_version", "sources", "answers", "design", "tests", "requirements"}
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1:
        raise ValueError(
            "context import requires closed sources, answers, design, tests and requirements"
        )
    task = controller.get_task(task_id)
    if task.phase == "approval":
        raise ValueError(
            "approved tasks require an explicit amendment; import cannot rewrite approval"
        )
    ws = controller.workspace(task_id)
    identifier = digest(value)
    prior = ws.latest_checkpoint("context_import")
    if prior and prior.payload["sha256"] == identifier:
        return prior.payload
    sources = {}
    for row in value["sources"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "kind", "content", "sha256", "provenance"}
            or not isinstance(row["id"], str)
            or not re.fullmatch("[A-Za-z0-9_-]+", row["id"])
            or row["id"] in sources
            or row["kind"] not in {"ticket", "audit", "user", "spec", "planning"}
            or not isinstance(row["content"], str)
            or len(row["content"].encode()) > 1024 * 1024
            or hashlib.sha256(row["content"].encode()).hexdigest() != row["sha256"]
            or not isinstance(row["provenance"], str)
            or not row["provenance"].strip()
        ):
            raise ValueError(
                "context source requires unique identity, supplied bytes, hash and provenance"
            )
        sources[row["id"]] = row
    if not sources:
        raise ValueError("context import needs supplied source context")

    def provenance(row):
        source = sources.get(row.get("source"))
        quote = row.get("quote")
        if (
            not source
            or not isinstance(quote, str)
            or not quote.strip()
            or quote not in source["content"]
        ):
            raise ValueError(
                "context item must cite an actual quote in a hash-bound supplied source"
            )
        return (
            "Imported proposal from "
            + source["id"]
            + " sha256:"
            + source["sha256"]
            + "; quote: "
            + quote
        )

    requirements = value["requirements"]
    if (
        not isinstance(requirements, dict)
        or set(requirements) - {"ticket", "criteria", "invariants"}
        or not requirements.get("criteria")
    ):
        raise ValueError("context requires typed accepted criterion identities")
    for kind in ["criteria", "invariants"]:
        rows = requirements.get(kind, [])
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"id", "text", "source", "quote"}
                or not row["text"]
                or row["id"] in seen
            ):
                raise ValueError("invalid or duplicate requirement identity")
            provenance(row)
            seen.add(row["id"])
    ticket = requirements.get("ticket")
    if ticket:
        if (
            not isinstance(ticket, dict)
            or ticket.get("present") is not True
            or not ticket.get("id")
            or not ticket.get("content")
        ):
            raise ValueError("supplied ticket must preserve its identity and content")
        provenance(ticket)
    catalogue = {row.key: row for row in catalogue_for(INTERVIEW_TOPICS)}
    refs = {}
    entries = {"questions": [], "design": [], "tests": []}
    with transaction(controller.conn):
        for row in value["answers"]:
            if set(row) != {
                "id",
                "topic",
                "question",
                "answer",
                "catalogue_key",
                "source",
                "quote",
            } or row["topic"] not in {*INTERVIEW_TOPICS, "scope", "decisions"}:
                raise ValueError("invalid imported answer")
            why = provenance(row)
            known = catalogue.get(row["catalogue_key"]) if row["catalogue_key"] else None
            if row["catalogue_key"] and (not known or known.topic != row["topic"]):
                raise ValueError("context catalogue identity differs")
            matches = [
                q
                for q in ws.questions()
                if known
                and q.catalogue_key == known.key
                or not known
                and q.text == row["question"]
                and q.topic == row["topic"]
            ]
            question = (
                matches[0]
                if matches
                else ws.ask(
                    row["topic"],
                    known.text if known else row["question"],
                    kind=known.kind if known else QuestionKind.QUESTION,
                    catalogue_key=known.key if known else None,
                )
            )
            if question.answer and question.answer != row["answer"]:
                raise ValueError("context conflicts with an existing developer answer")
            if not question.answer:
                proposal = ws.propose(
                    "Imported " + row["id"], row["answer"], why, question_id=question.id
                )
                entries["questions"].append(
                    {"question": question.id, "proposal": proposal.id, "source": row["source"]}
                )
            refs["Q:" + row["id"]] = "Q" + str(question.id)
        for row in value["design"]:
            if (
                set(row) != {"id", "section", "statement", "delegated", "source", "quote"}
                or type(row["delegated"]) is not bool
            ):
                raise ValueError("invalid imported design")
            why = provenance(row)
            section = DesignSection(row["section"])
            matching = [
                e
                for e in ws.design_entries()
                if e.section == section and e.statement == row["statement"]
            ]
            entry = (
                matching[0]
                if matching
                else ws.propose_design(
                    section,
                    row["statement"],
                    why,
                    Binding.DELEGATED if row["delegated"] else Binding.FIXED,
                )
            )
            refs["E:" + row["id"]] = "E" + str(entry.id)
            entries["design"].append(entry.id)
        for row in value["tests"]:
            if set(row) != {
                "id",
                "category",
                "statement",
                "covers",
                "mock",
                "mock_reason",
                "source",
                "quote",
            }:
                raise ValueError("invalid imported scenario")
            why = provenance(row)
            covers = [refs.get(ref, ref) for ref in row["covers"]]
            for ref in covers:
                if re.fullmatch("Q[1-9][0-9]*", ref):
                    ws.question(int(ref[1:]))
                elif re.fullmatch("E[1-9][0-9]*", ref):
                    ws.design_entry(int(ref[1:]))
                else:
                    raise ValueError(
                        "scenario covers must resolve to this task actual Q/E references"
                    )
            scenario = ws.propose_scenario(
                TestCategory(row["category"]),
                row["statement"],
                why,
                covers,
                row["mock"],
                row["mock_reason"],
            )
            entries["tests"].append(scenario.id)
        payload = {
            "schema_version": 1,
            "sha256": identifier,
            "sources": value["sources"],
            "entries": entries,
            "requirements": requirements,
            "status": "PROPOSED",
            "approval": "not granted; review the complete hash-bound package",
        }
        ws.checkpoint("context_import", payload)
        ws.record_event("context.imported", payload)
    return payload
