"""Sealed criterion inventory and fixed browser language validation."""

import pytest

from harness.browser_models import in_scope, url_parts
from harness.browser_plan import extract_criteria, propose_browser
from harness.delivery import publish_delivery
from tests import test_delivery_monitor

delivery = test_delivery_monitor.delivery


def test_exact_multiline_inventory_and_duplicate_q_identity():
    text = (
        "## Acceptance criteria\n\n- Q1: first\n  continued\n"
        "- Q1: second\n- Q2: third\n\n## Test plan\n- ignored\n"
    )
    result = extract_criteria(text)
    assert result[0]["text"] == "Q1: first\n  continued"
    assert result[0]["id"] != result[1]["id"]
    assert result[2]["id"] == "Q2"
    with pytest.raises(ValueError):
        extract_criteria(text + "## Acceptance criteria\n")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com/",
        "file:///tmp/a",
        "https://example.com/?token=abc",
        "https://example.com/\\evil",
    ],
)
def test_unsafe_url_rejected(url):
    with pytest.raises(ValueError):
        url_parts(url)


def test_segment_and_exact_origin_scope():
    scope = [{"origin": "https://example.com/", "path_prefix": "/app"}]
    assert in_scope("https://example.com/app/ok", scope)
    assert not in_scope("https://example.com/application", scope)
    assert not in_scope("https://example.com.attacker/app", scope)


def test_proposal_keeps_unmapped_criteria_unresolved(delivery):
    controller, eid, api = delivery
    publish_delivery(controller, eid, client=api)
    api.environment["external_url"] = "https://staging.example/"
    proposed = propose_browser(controller, eid, client=api)
    assert proposed["plan"]["criteria"][0]["classification"] == "unresolved"
    assert proposed["plan"]["criteria"][0]["text"] != "AC-01"
    assert proposed["plan"]["target"]["url"] == "https://staging.example/"
