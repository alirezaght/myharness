"""One format contract serves advisory feedback and completed-report ingestion."""

import json

import pytest

from harness.knowledge.contribution_contract import ContributionFormatError, extract, feedback


def raw(value):
    return json.dumps(value).encode()


@pytest.mark.parametrize(
    "document",
    [
        {"summary": "Sourced observation"},
        {"answer": "Sourced observation"},
        {"finding": "Sourced observation"},
        {"observations": ["Sourced observation"]},
        {"facts": [{"claim": "Sourced observation", "sources": ["service.py:1"]}]},
        [{"observation": "Existing non-object JSON narrative"}],
    ],
)
def test_existing_report_forms_share_feedback_and_extraction(document):
    content = raw(document)
    assert feedback(content)["status"] == "ready"
    assertions = extract(content, "al-scout", "scout-report")
    assert len(assertions) == 1
    assert assertions[0].origin == "attributed-inference"
    assert assertions[0].verification == "unverified"
    assert assertions[0].applicability["producer_role"] == "al-scout"


@pytest.mark.parametrize(
    "claim,code",
    [
        (
            {"subject": "x", "predicate": "observes", "text": "safe", "status": "STATIC_CANARY"},
            "unsupported-assertion-field",
        ),
        (
            {
                "subject": "x",
                "predicate": "observes",
                "text": "safe",
                "parents": ["service.py:1 CANARY"],
            },
            "invalid-parent-reference",
        ),
        (
            {"subject": "x", "predicate": "observes", "text": "safe", "parents": ["a" * 64] * 41},
            "parent-count",
        ),
        ({"predicate": "observes", "text": "safe"}, "required-field"),
        ({"subject": 4, "predicate": "observes", "text": "safe"}, "invalid-field-type"),
    ],
)
def test_closed_failures_do_not_echo_values_or_invent_parent_identity(claim, code):
    content = raw({"assertions": [claim]})
    result = feedback(content)
    assert result["status"] == "invalid"
    assert code in {issue["code"] for issue in result["issues"]}
    assert "CANARY" not in json.dumps(result)
    with pytest.raises(ContributionFormatError) as raised:
        extract(content, "al-scout", "scout-report")
    assert raised.value.feedback == result
    assert "CANARY" not in str(raised.value)


def test_exact_ids_and_existing_extraction_caps_are_explicit():
    content = raw(
        {
            "assertions": [
                {"subject": "x", "predicate": "observes", "text": "x" * 9000, "parents": ["a" * 64]}
            ]
            * 41
        }
    )
    result = feedback(content)
    assert result["status"] == "ready" and result["truncated"]
    assert {"assertion-limit", "text-limit"} <= {i["code"] for i in result["issues"]}
    assertions = extract(content, "al-scout", "scout-report")
    assert len(assertions) == 40 and all(len(a.text) == 8192 for a in assertions)
    assert all(a.derivation_parents == ("a" * 64,) for a in assertions)


def test_sensitive_and_unsupported_shapes_have_content_free_categories():
    for content, code in [
        (b"AKIA0000000000000000", "sensitive-content"),
        (raw({"other": "PRIVATE_CANARY"}), "unsupported-report-shape"),
    ]:
        result = feedback(content)
        assert result["status"] == "invalid"
        assert code in {issue["code"] for issue in result["issues"]}
        assert "PRIVATE_CANARY" not in json.dumps(result)
    assert feedback(b"Plain authored Markdown observation")["status"] == "ready"
    assert feedback(raw({"assertions": []}))["status"] == "not_applicable"
