"""Durable interpretations and reproducible suggestions across actual source families."""

# ruff: noqa: F811
import json
import sqlite3

import pytest

from harness.db import transaction
from harness.reliability import catalog, grouping, investigations, legacy_projection
from tests.incident_helpers import seed, selected
from tests.test_reliability_store import observed  # noqa: F401


def prepared(observed):
    ids = seed(observed, 3)
    conn, scoped = observed[2].conn, selected(observed)
    legacy_projection.project_legacy_batch(conn, scoped, expected_revision=0, request_id="prepare")
    return conn, scoped, ids


def test_revision_history_hypotheses_corrections_and_exact_replay(observed):
    conn, scoped, ids = prepared(observed)
    iid = ids[0]
    original = [tuple(r) for r in conn.execute("SELECT * FROM reliability_observations")]
    initial = investigations.current(conn, scoped, iid)
    assert initial["revision"] == 0 and initial["document"]["controls"][0]["state"] == "held"
    changes = {
        "status": "investigating",
        "hypotheses": [
            {
                "cause": "missing_context",
                "confidence": "low",
                "supporting": [iid],
                "contradicting": [ids[1]],
            }
        ],
    }
    first = investigations.revise(
        conn, scoped, iid, expected_revision=0, request_id="investigate", changes=changes
    )
    correction = {
        "corrections": [
            {
                "field": "hypotheses",
                "interpretation": "incorrect_tool",
                "rationale": {"code": "interpretation_corrected", "evidence": [ids[1]]},
            }
        ],
        "affected_components": [
            {"reference": "harness/operations.py", "verification": "unverified"}
        ],
    }
    second = investigations.revise(
        conn, scoped, iid, expected_revision=1, request_id="correct", changes=correction
    )
    assert second["parent_revision_id"] == first["revision_id"]
    assert second["actor_kind"] == "local_dashboard_operator"
    assert second["document"]["facts"] == [iid]
    assert (
        investigations.revise(
            conn, scoped, iid, expected_revision=0, request_id="investigate", changes=changes
        )
        == first
    )
    with pytest.raises(catalog.IncidentConflict):
        investigations.revise(
            conn, scoped, iid, expected_revision=0, request_id="new", changes=changes
        )
    with pytest.raises(catalog.IncidentConflict):
        investigations.revise(
            conn,
            scoped,
            iid,
            expected_revision=0,
            request_id="investigate",
            changes={"status": "open"},
        )
    page = investigations.history(conn, scoped, iid, limit=1)
    assert page["items"] == [second] and page["next_revision"] == 2
    assert investigations.history(conn, scoped, iid, before_revision=2)["items"] == [first]
    assert [tuple(r) for r in conn.execute("SELECT * FROM reliability_observations")] == original
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE incident_investigation_revisions SET document='{}'")


def test_historical_resolution_requires_rationale_and_evidence_without_epoch_write(observed):
    conn, scoped, ids = prepared(observed)
    conn.execute("UPDATE executions SET status='Completed'")
    conn.execute("UPDATE execution_epochs SET state='RETIRED'")
    with pytest.raises(ValueError, match="rationale"):
        investigations.revise(
            conn,
            scoped,
            ids[0],
            expected_revision=0,
            request_id="no-rationale",
            changes={"status": "resolved"},
        )
    with pytest.raises(ValueError, match="evidence"):
        investigations.revise(
            conn,
            scoped,
            ids[0],
            expected_revision=0,
            request_id="no-evidence",
            changes={
                "status": "resolved",
                "rationale": {"code": "evidence_sufficient", "evidence": []},
            },
        )
    result = investigations.revise(
        conn,
        scoped,
        ids[0],
        expected_revision=0,
        request_id="resolve",
        changes={
            "status": "resolved",
            "rationale": {"code": "evidence_sufficient", "evidence": [ids[1]]},
        },
    )
    assert result["document"]["status"] == "resolved"
    assert conn.execute("SELECT state FROM execution_epochs").fetchone()[0] == "RETIRED"
    assert conn.execute("SELECT status FROM executions").fetchone()[0] == "Completed"
    assert "approval" not in result["document"]


def test_reference_only_closed_values_refuse_raw_secrets_and_fact_rewrite(observed):
    conn, scoped, ids = prepared(observed)
    for changes in (
        {"facts": []},
        {"root_cause": "secret"},
        {
            "hypotheses": [
                {
                    "cause": "SECRET_SENTINEL",
                    "confidence": "high",
                    "supporting": [ids[0]],
                    "contradicting": [],
                }
            ]
        },
        {"status": "resolved", "rationale": {"code": "SECRET_SENTINEL", "evidence": [ids[0]]}},
        {"affected_components": [{"reference": "../secret", "verification": "unverified"}]},
    ):
        with pytest.raises(ValueError):
            investigations.revise(
                conn, scoped, ids[0], expected_revision=0, request_id="bad", changes=changes
            )
    assert not conn.execute("SELECT 1 FROM incident_investigation_revisions").fetchone()
    with pytest.raises(LookupError):
        investigations.current(conn, scoped | {"task": "other"}, ids[0])


def test_budget_and_operation_investigations_keep_fact_distinctions(observed):
    conn, scoped, ids = prepared(observed)
    with transaction(conn):
        conn.execute(
            (
                "INSERT INTO policy_decisions VALUES ('budget',?,'legacy-0',NULL,'"
                "deny','BUDGET_EXHAUSTED','p','then')"
            ),
            (scoped["execution"],),
        )
        budget = catalog.record_budget_decision(conn, "budget")
    result = investigations.revise(
        conn,
        scoped,
        budget,
        expected_revision=0,
        request_id="cross-source",
        changes={
            "hypotheses": [
                {
                    "cause": "excessive_retries",
                    "confidence": "low",
                    "supporting": [budget, ids[0]],
                    "contradicting": [],
                }
            ]
        },
    )
    assert result["document"]["classification"]["category"] == "budget_exhaustion"
    assert catalog.detail(conn, scoped, ids[0])["category"] == "operation_denied"


def test_rejected_split_and_superseded_group_decisions_are_durable(observed):
    conn, scoped, ids = prepared(observed)
    suggestion = grouping.suggest(conn, scoped)
    group = suggestion["items"][0]
    assert len(group["members"]) == 4 and "common cause not established" in group["meaning"]
    reject = {"kind": "reject", "rationale": {"code": "unrelated_symptoms", "evidence": [ids[0]]}}
    first = grouping.decide(
        conn, scoped, group["group_id"], expected_revision=0, request_id="reject", decision=reject
    )
    assert grouping.suggest(conn, scoped)["items"][0]["decision"] == reject
    assert (
        grouping.decide(
            conn,
            scoped,
            group["group_id"],
            expected_revision=0,
            request_id="reject",
            decision=reject,
        )
        == first
    )
    split = {
        "kind": "split",
        "partitions": [group["members"][:2], group["members"][2:]],
        "rationale": {"code": "unrelated_symptoms", "evidence": [ids[1]]},
    }
    grouping.decide(
        conn, scoped, group["group_id"], expected_revision=1, request_id="split", decision=split
    )
    with pytest.raises(ValueError, match="exactly once"):
        grouping.decide(
            conn,
            scoped,
            group["group_id"],
            expected_revision=2,
            request_id="bad-split",
            decision=split | {"partitions": [group["members"], group["members"]]},
        )
    seed(observed)
    legacy_projection.project_legacy_batch(
        conn, scoped, expected_revision=1, request_id="new-member"
    )
    new = grouping.suggest(conn, scoped)["items"][0]
    assert new["group_id"] != group["group_id"]
    grouping.decide(
        conn,
        scoped,
        group["group_id"],
        expected_revision=2,
        request_id="supersede",
        decision={
            "kind": "supersede",
            "target_group": new["group_id"],
            "rationale": {"code": "evidence_updated", "evidence": [ids[0]]},
        },
    )
    page = grouping.decisions(conn, scoped, group["group_id"], limit=2)
    assert page["next_revision"] == 2
    assert (
        grouping.decisions(conn, scoped, group["group_id"], before_revision=2)["items"][0][
            "document"
        ]
        == reject
    )
    assert conn.execute("SELECT count(*) FROM reliability_incidents").fetchone()[0] == 5
    assert "SECRET_SENTINEL" not in json.dumps(grouping.get(conn, scoped, group["group_id"]))


def test_specific_operator_authored_hypothesis_correction_and_resolution(observed):
    conn, scoped, ids = prepared(observed)
    iid = ids[0]
    changes = {
        "hypotheses": [
            {
                "cause": "missing_context",
                "confidence": "medium",
                "supporting": [iid],
                "contradicting": [ids[1]],
                "operator_explanation": (
                    "The assignment omitted the supported platform version; the second"
                    " incident shows a different selected version."
                ),
            }
        ],
        "corrections": [
            {
                "field": "hypotheses",
                "interpretation": "incorrect_tool",
                "operator_explanation": (
                    "The first interpretation confused a version mismatch with absent context."
                ),
                "rationale": {"code": "interpretation_corrected", "evidence": [ids[1]]},
            }
        ],
        "status": "resolved",
        "rationale": {
            "code": "evidence_sufficient",
            "evidence": [iid, ids[1]],
            "operator_explanation": (
                "The operator has reconciled these two observations; this does not"
                " verify a deployed repair."
            ),
        },
        "corrective_actions": [
            {
                "kind": "add_regression",
                "operator_explanation": (
                    "Compare selected platform version against the assignment before p"
                    "reparing the command."
                ),
            }
        ],
    }
    result = investigations.revise(
        conn,
        scoped,
        iid,
        expected_revision=0,
        request_id="authored-interpretation",
        changes=changes,
    )
    assert result["document"]["hypotheses"] == changes["hypotheses"]
    assert result["document"]["corrections"] == changes["corrections"]
    assert result["document"]["rationale"] == changes["rationale"]
    assert result["actor_kind"] == "local_dashboard_operator"
    assert result["document"]["facts"] == [iid]
    for explanation in ("Authorization: Bearer SECRET_SENTINEL", '{"api_key":"SECRET_SENTINEL"}'):
        with pytest.raises(ValueError, match="credentials"):
            investigations.revise(
                conn,
                scoped,
                iid,
                expected_revision=1,
                request_id="unsafe",
                changes={
                    "rationale": {
                        "code": "evidence_updated",
                        "evidence": [iid],
                        "operator_explanation": explanation,
                    }
                },
            )
    assert "SECRET_SENTINEL" not in str(
        [tuple(r) for r in conn.execute("SELECT * FROM incident_investigation_revisions")]
    )


def test_cross_source_related_symptoms_preserve_kind_and_version_differences(observed):
    conn, scoped, ids = prepared(observed)
    # Source persistence fixture only: actual authenticated ContractError production is next stage.
    with transaction(conn):
        conn.execute(
            "INSERT INTO contract_rejection_receipts VALUES ('typed-rejection',?,'legacy-0',0,"
            "'fixture-principal','dispatch','task-assignment','INVALID_AGENT_CONTRACT',"
            "'/required_inputs',NULL,'2026-01-01')",
            (scoped["execution"],),
        )
        incident = catalog._insert(
            conn,
            "contract_rejection",
            scoped["execution"],
            "legacy-0",
            "invalid_handoff",
            "CONTRACT_REJECTED",
            "runtime_observed",
            "2026-01-01",
        )
        conn.execute(
            (
                "INSERT INTO incident_contract_sources(incident_id,rejection_id) V"
                "ALUES (?,'typed-rejection')"
            ),
            (incident,),
        )
    result = grouping.suggest(conn, scoped)
    group = result["items"][0]
    assert set(ids + [incident]) <= set(group["members"])
    assert group["safe_key"]["symptom_family"] == "boundary_rejected"
    assert group["member_metadata"][incident]["source_kind"] == "contract_rejection"
    assert group["member_metadata"][incident]["spec_version"] is None
    assert group["member_metadata"][ids[0]]["source_kind"] == "operation_dispatch"
    assert group["member_metadata"][ids[0]]["spec_version"] is not None
    assert "common cause not established" in group["meaning"]
