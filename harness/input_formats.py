"""Closed input formats printed from the installed package.

The agent runs in the target repository, where the Harness docs are absent, so
`harness context import --schema` and `harness classify --assessment-schema`
print the formats. Both are built from the validators' own constants.
"""

import argparse
import hashlib
import json

from harness import context_import as ctx
from harness import task_risk as risk
from harness.design_models import DesignSection
from harness.testplan_models import TestCategory

TEXT = {"type": "string", "minLength": 1}
SHA256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _closed(fields, properties):
    assert set(properties) == set(fields), "schema drifted from validator fields"
    return {
        "type": "object",
        "required": list(fields),
        "properties": properties,
        "additionalProperties": False,
    }


CITATION = {"source": TEXT, "quote": TEXT}


def context_schema():
    row = _closed(ctx.REQUIREMENT_ROW_FIELDS, {"id": TEXT, "text": TEXT, **CITATION})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "harness context import (schema_version 1)",
        **_closed(
            ctx.CONTEXT_FIELDS,
            {
                "schema_version": {"const": 1},
                "sources": {
                    "type": "array",
                    "minItems": 1,
                    "items": _closed(
                        ctx.SOURCE_FIELDS,
                        {
                            "id": {"type": "string", "pattern": f"^{ctx.SOURCE_ID_PATTERN}$"},
                            "kind": {"enum": list(ctx.SOURCE_KINDS)},
                            "content": {"type": "string"},
                            "sha256": SHA256,
                            "provenance": TEXT,
                        },
                    ),
                },
                "answers": {
                    "type": "array",
                    "items": _closed(
                        ctx.ANSWER_FIELDS,
                        {
                            "id": TEXT,
                            "topic": {"enum": list(ctx.ANSWER_TOPICS)},
                            "question": TEXT,
                            "answer": TEXT,
                            "catalogue_key": {"type": ["string", "null"]},
                            **CITATION,
                        },
                    ),
                },
                "design": {
                    "type": "array",
                    "items": _closed(
                        ctx.DESIGN_FIELDS,
                        {
                            "id": TEXT,
                            "section": {"enum": [s.value for s in DesignSection]},
                            "statement": TEXT,
                            "delegated": {"type": "boolean"},
                            **CITATION,
                        },
                    ),
                },
                "tests": {
                    "type": "array",
                    "items": _closed(
                        ctx.TEST_FIELDS,
                        {
                            "id": TEXT,
                            "category": {"enum": [c.value for c in TestCategory]},
                            "statement": TEXT,
                            "covers": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "pattern": f"^({ctx.COVERS_PATTERN})$",
                                },
                            },
                            "mock": {"type": ["string", "null"]},
                            "mock_reason": {"type": ["string", "null"]},
                            **CITATION,
                        },
                    ),
                },
                "requirements": {
                    "type": "object",
                    "required": ["criteria"],
                    "properties": {
                        "ticket": {
                            "type": "object",
                            "required": list(ctx.TICKET_FIELDS),
                            "properties": {
                                "present": {"const": True},
                                "id": TEXT,
                                "content": TEXT,
                                **CITATION,
                            },
                        },
                        "criteria": {"type": "array", "minItems": 1, "items": row},
                        "invariants": {"type": "array", "items": row},
                    },
                    "additionalProperties": False,
                },
            },
        ),
    }


def context_example():
    text = "TICKET-1: Export keeps the column order. AC1: columns keep their order."
    cite = {"source": "ticket", "quote": "AC1: columns keep their order."}
    return {
        "schema_version": 1,
        "sources": [
            {
                "id": "ticket",
                "kind": "ticket",
                "content": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "provenance": "Ticket TICKET-1 as pasted by the developer.",
            }
        ],
        "answers": [
            {
                "id": "order",
                "topic": "functional_behavior",
                "question": "Must the export keep the column order?",
                "answer": "Yes, columns keep their order.",
                "catalogue_key": None,
                **cite,
            }
        ],
        "design": [
            {
                "id": "writer",
                "section": "components",
                "statement": "The existing export writer keeps the configured column order.",
                "delegated": False,
                **cite,
            }
        ],
        "tests": [
            {
                "id": "order",
                "category": "regression",
                "statement": "Export a table and compare the column order with the source.",
                "covers": ["Q:order", "E:writer"],
                "mock": None,
                "mock_reason": None,
                **cite,
            }
        ],
        "requirements": {
            "ticket": {"present": True, "id": "TICKET-1", "content": text, **cite},
            "criteria": [{"id": "AC1", "text": "Columns keep their order.", **cite}],
            "invariants": [],
        },
    }


CONTEXT_NOTES = [
    "Usage: harness context import TASK --file context.json. Importing proposes content "
    "and never approves it; review it with harness review-package export TASK.",
    "Every source sha256 is the raw lowercase hex SHA-256 of its UTF-8 content "
    f"(at most {ctx.MAX_SOURCE_BYTES} bytes); source ids are unique.",
    "Every answer, design item, test, requirement row and ticket cites a source id and a "
    "quote that occurs verbatim in that source's content.",
    "catalogue_key is null or a catalogue question key whose topic equals the answer topic.",
    "Test covers reference imported items as Q:<answer-id> / E:<design-id>, or existing "
    "task references Q<n> / E<n>.",
    "Use empty arrays for absent answers, design, tests or invariants; criteria needs at "
    "least one row. Never invent prior review or source quotes.",
]


def risk_schema():
    explanation = {"type": "string", "minLength": risk.MIN_EXPLANATION}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "harness classify --assessment (schema_version 1)",
        **_closed(
            risk.ASSESSMENT_FIELDS,
            {
                "schema_version": {"const": 1},
                "scope": {"enum": list(risk.SCOPES)},
                "existing_pattern": {"type": "boolean"},
                "behavior_clear": {"const": True},
                "hard_parts": {
                    "type": "array",
                    "items": _closed(
                        risk.HARD_PART_FIELDS,
                        {
                            "kind": {"enum": sorted(risk.KINDS)},
                            "new_design": {"type": "boolean"},
                            "description": explanation,
                        },
                    ),
                },
                "pseudocode_benefit": {"type": "string"},
                "evidence": {
                    "type": "array",
                    "minItems": 1,
                    "items": _closed(
                        risk.EVIDENCE_FIELDS,
                        {"path": TEXT, "sha256": SHA256, "reason": TEXT},
                    ),
                },
                "reason": explanation,
            },
        ),
    }


def risk_example():
    return {
        "schema_version": 1,
        "scope": "local",
        "existing_pattern": True,
        "behavior_clear": True,
        "hard_parts": [],
        "pseudocode_benefit": "",
        "evidence": [
            {
                "path": "src/export/writer.py",
                "sha256": "0" * 64,
                "reason": "The writer already orders columns through one configuration seam.",
            }
        ],
        "reason": "Change one bounded setting in the existing export writer.",
    }


RISK_NOTES = [
    "Usage: harness classify TASK --assessment risk.json --repo REPOSITORY, after "
    "clarification and code inspection; raised questions and decisions must be resolved.",
    "Evidence paths are repository-relative files (no symlinks, no ..); sha256 is the raw "
    "lowercase hex SHA-256 of each file's current bytes (the example hash is a placeholder).",
    "behavior_clear must be true: clarify material behavior choices first.",
    "Use an empty hard_parts array when no difficult new design remains. Each hard part "
    "names a specific design problem, not a topic keyword.",
    "Routing: COMPLEX when a hard part has new_design true and pseudocode_benefit explains "
    f"(at least {risk.MIN_EXPLANATION} characters) why pseudocode helps; SMALL for local "
    "scope following an existing pattern without new design; STANDARD otherwise.",
    "A developer's explicit classification (harness classify TASK LEVEL) stays authoritative.",
]

FORMATS = {
    "context": lambda: (context_schema(), context_example(), CONTEXT_NOTES),
    "assessment": lambda: (risk_schema(), risk_example(), RISK_NOTES),
}


def render(name):
    schema, example, notes = FORMATS[name]()
    document = {"notes": notes, "json_schema": schema, "example": example}
    return json.dumps(document, indent=2) + "\n"


class PrintFormat(argparse.Action):
    """Print a closed input format and exit, like --help (no task id required)."""

    def __init__(self, option_strings, dest, format_name, **kwargs):
        self.format_name = format_name
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(render(self.format_name), end="")
        parser.exit()
