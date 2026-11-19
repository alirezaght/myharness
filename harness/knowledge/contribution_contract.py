"""Shared, content-free format feedback and extraction for authored contributions."""

import json
import re

from harness.knowledge.models import Assertion, digest
from harness.knowledge.sources import _SECRET

CONTRACT_VERSION = 1
ASSERTION_LIMIT = 40
PARENT_LIMIT = 40
TEXT_LIMIT = 8192
_KEYS = ("subject", "predicate", "object", "value", "text", "parents")
_NOTICE = (
    "Format readiness does not establish completion provenance, current parent eligibility, "
    "source visibility or final ingestion. Origin and verification are server-assigned."
)


def description():
    return {
        "contract_version": CONTRACT_VERSION,
        "assertion_keys": list(_KEYS),
        "parents": "Exact full assertion IDs; source locators are explanatory text, not parents.",
        "limits": {"assertions": ASSERTION_LIMIT, "parents": PARENT_LIMIT, "text": TEXT_LIMIT},
        "notice": _NOTICE,
    }


class ContributionFormatError(ValueError):
    def __init__(self, feedback):
        super().__init__("knowledge contribution format invalid")
        self.feedback = feedback


def parse(raw):
    """Keep existing report forms and extraction caps; never repair authored content."""
    issues, invalid, truncated = [], False, False

    def issue(code, *, error=True, **location):
        nonlocal invalid, truncated
        invalid |= error
        if len(issues) < 64:
            issues.append({"code": code, **location})
        else:
            truncated = True

    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        text = ""
        issue("invalid-encoding")
    if _SECRET.search(text):
        issue("sensitive-content")
    try:
        document = json.loads(text)
    except ValueError:
        document = None
    claims = document.get("assertions") if isinstance(document, dict) else None
    summary = None
    if isinstance(claims, list):
        if len(claims) > ASSERTION_LIMIT:
            truncated = True
            issue("assertion-limit", error=False, field="assertions")
        claims = claims[:ASSERTION_LIMIT]
        for index, claim in enumerate(claims):
            location = {"assertion_index": index}
            if not isinstance(claim, dict):
                issue("invalid-field-type", field="assertions", **location)
                continue
            if set(claim) - set(_KEYS):
                issue("unsupported-assertion-field", **location)
            for field in ("subject", "predicate", "text"):
                if field not in claim:
                    issue("required-field", field=field, **location)
                elif not isinstance(claim[field], str):
                    issue("invalid-field-type", field=field, **location)
            if claim.get("object") is not None and not isinstance(claim["object"], str):
                issue("invalid-field-type", field="object", **location)
            parents = claim.get("parents", [])
            if not isinstance(parents, list):
                issue("invalid-field-type", field="parents", **location)
            elif len(parents) > PARENT_LIMIT:
                issue("parent-count", field="parents", **location)
            else:
                for parent_index, parent in enumerate(parents):
                    if not isinstance(parent, str) or not re.fullmatch(r"[0-9a-f]{64}", parent):
                        issue(
                            "invalid-parent-reference",
                            field="parents",
                            parent_index=parent_index,
                            **location,
                        )
            if isinstance(claim.get("text"), str) and len(claim["text"]) > TEXT_LIMIT:
                truncated = True
                issue("text-limit", error=False, field="text", **location)
    else:
        if isinstance(document, dict):
            parts = [
                str(document[k])
                for k in ("finding", "summary", "conclusion", "observations")
                if k in document
            ]
            answer = document.get("answer")
            if isinstance(answer, str) and answer.strip():
                parts.append(answer)
            facts = document.get("facts")
            if (
                isinstance(facts, list)
                and facts
                and all(
                    isinstance(fact, dict)
                    and not set(fact) - {"claim", "sources"}
                    and isinstance(fact.get("claim"), str)
                    and fact["claim"].strip()
                    and isinstance(fact.get("sources", []), list)
                    and all(isinstance(ref, str) and ref.strip() for ref in fact.get("sources", []))
                    for fact in facts
                )
            ):
                parts.append(json.dumps(facts, sort_keys=True, ensure_ascii=False))
            summary = "\n".join(parts)
        else:
            summary = text
        if not summary.strip():
            issue("unsupported-report-shape")
        if len(summary) > TEXT_LIMIT:
            truncated = True
            issue("text-limit", error=False, field="text")
    feedback = {
        "contract_version": CONTRACT_VERSION,
        "status": "invalid" if invalid else "not_applicable" if claims == [] else "ready",
        "issues": issues,
        "truncated": truncated,
    }
    if invalid:
        raise ContributionFormatError(feedback)
    return claims, summary, feedback


def feedback(raw):
    try:
        return parse(raw)[2]
    except ContributionFormatError as error:
        return error.feedback
    except (RecursionError, TypeError):
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "unavailable",
            "issues": [{"code": "validation-unavailable"}],
            "truncated": False,
        }


def extract(raw, role, kind):
    claims, summary, _ = parse(raw)
    if claims is None or not isinstance(claims, list):
        claims = [{"subject": "report:" + digest(raw), "predicate": "observes", "text": summary}]
    return tuple(
        Assertion(
            claim["subject"],
            claim["predicate"],
            claim.get("object"),
            claim.get("value"),
            claim["text"][:TEXT_LIMIT],
            0,
            len(raw),
            "attributed-inference",
            "unverified",
            applicability={"producer_role": role, "report_kind": kind},
            derivation_parents=tuple(claim.get("parents", [])),
        )
        for claim in claims
    )
