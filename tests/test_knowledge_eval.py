import json

import pytest

from scripts.knowledge_eval import build_report, read_events


def _event(pair, arm, phase, participant, text, reads, **extra):
    return {
        "pair_id": pair,
        "arm": arm,
        "phase": phase,
        "participant": participant,
        "visible_text": text,
        "service_read_bytes": reads,
        **extra,
    }


def test_report_keeps_visible_service_unique_and_continuity_costs_separate():
    events = [
        _event(
            "p1",
            "B",
            "warm-query",
            "single",
            "baseline context",
            120,
            query_id="q1",
            query_number=1,
            sources=[{"snapshot": "s1", "path": "a.py", "span": "0:10", "bytes": 10}],
            input_tokens=4,
            output_tokens=3,
            elapsed_ms=100,
        ),
        _event(
            "p1",
            "B",
            "warm-query",
            "single",
            "baseline context",
            80,
            query_id="q1",
            query_number=1,
            sources=[{"snapshot": "s1", "path": "a.py", "span": "0:10", "bytes": 10}],
            input_tokens=4,
            output_tokens=3,
            elapsed_ms=50,
        ),
        _event(
            "p1",
            "C",
            "warm-query",
            "single",
            "short",
            70,
            query_id="q1",
            query_number=1,
            sources=[{"snapshot": "s1", "path": "a.py", "span": "0:10", "bytes": 10}],
            input_tokens=None,
            output_tokens=None,
            elapsed_ms=80,
        ),
        _event("p1", "B", "continuity", "donor", "donor", 20),
        _event("p1", "B", "continuity", "recipient", "recipient", 30),
        _event("p1", "C", "continuity", "donor", "d", 10),
        _event("p1", "C", "continuity", "recipient", "r", 10),
    ]
    report = build_report(events)
    assert report["arms"]["B"]["agent_visible_bytes"] == len(
        "baseline contextbaseline contextdonorrecipient"
    )
    assert report["arms"]["B"]["service_read_bytes"] == 250
    assert report["arms"]["B"]["unique_source_bytes"] == 10
    assert report["arms"]["C"]["input_tokens"] is None
    assert report["continuity"]["B"]["donor"]["agent_visible_bytes"] == 5
    assert report["continuity"]["B"]["recipient"]["agent_visible_bytes"] == 9
    assert report["pairs"][0][
        "combined_donor_recipient_visible_reduction_fraction"
    ] == pytest.approx(6 / 7)
    assert (
        report["acceptance"]
        == "not_assessed_without_independent_quality_labels_and_native_workflow_evidence"
    )


def test_event_reader_rejects_missing_or_invalid_measurements(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            _event(
                "p1",
                "B",
                "warm-query",
                "single",
                "x",
                -1,
                query_id="q1",
                query_number=1,
            )
        )
    )
    with pytest.raises(ValueError, match="service_read_bytes"):
        read_events(path)


def test_cold_plus_first_query_and_amortized_costs_are_reported_separately():
    events = [
        _event("p1", "B", "cold-index", "single", "idx", 100),
        _event("p1", "B", "warm-query", "single", "first", 20, query_id="q1", query_number=1),
        _event("p1", "B", "warm-query", "single", "second", 40, query_id="q2", query_number=2),
        _event("p1", "C", "cold-index", "single", "idx", 50),
        _event("p1", "C", "warm-query", "single", "first", 10, query_id="q1", query_number=1),
    ]
    costs = build_report(events)["cold_and_amortized_query_costs"]
    assert costs["B"]["cold_index_plus_one_query"] == {
        "agent_visible_bytes": 8,
        "service_read_bytes": 120,
    }
    assert costs["B"]["amortized_cold_index_cost_per_warm_query"] == {
        "agent_visible_bytes": 7,
        "service_read_bytes": 80,
    }
    assert costs["C"]["amortized_cold_index_cost_per_warm_query"]["service_read_bytes"] == 60
