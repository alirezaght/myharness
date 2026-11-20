"""Closed provenance identities only. No report resolution or publication authority."""

import json

from harness.reliability.catalog import detail


def source_relation(conn, selected, incident_id):
    item = detail(conn, selected, incident_id)
    kind = item["source_kind"]
    if kind == "operation_dispatch":
        row = conn.execute(
            "SELECT i.incident_id AS legacy_incident_id,i.observation_id,o.source_id "
            "FROM reliability_incidents i JOIN reliability_observations o USING(observation_id) "
            "WHERE i.incident_id=?",
            (incident_id,),
        ).fetchone()
    elif kind == "budget_decision":
        row = conn.execute(
            "SELECT s.policy_decision_id,d.attempt_id FROM incident_policy_sources s "
            "JOIN policy_decisions d ON d.id=s.policy_decision_id WHERE s.incident_id=?",
            (incident_id,),
        ).fetchone()
    elif kind in {"al_test_result", "al_review_finding"}:
        row = conn.execute(
            "SELECT s.event_id,s.item_id,s.source_revision,e.record FROM incident_event_sources s "
            "JOIN execution_events e ON e.execution_id=s.execution_id AND e.epoch_id=s.epoch_id "
            "AND e.event_id=s.event_id WHERE s.incident_id=?",
            (incident_id,),
        ).fetchone()
    elif kind == "contract_rejection":
        row = conn.execute(
            "SELECT s.rejection_id,r.observation_id,r.boundary,r.contract_fami"
            "ly,r.error_code,r.field_pointer "
            "FROM incident_contract_sources s "
            "JOIN contract_rejection_receipts r ON r.rejection_id=s.rejection_id "
            "WHERE s.incident_id=?",
            (incident_id,),
        ).fetchone()
    elif kind == "operator_verdict":
        row = conn.execute(
            "SELECT operator_request_id,verdict_revision,document FROM incident_operator_sources "
            "WHERE incident_id=?",
            (incident_id,),
        ).fetchone()
    else:
        raise ValueError("unsupported incident source")
    if row is None:
        raise LookupError("incident source unavailable")
    reference = dict(row)
    result = {"source_kind": kind, "reference": reference}
    if "record" in reference:
        event = json.loads(reference.pop("record"))
        payload = event["payload"]
        result["accepted_source"] = payload.get("source_receipt", payload.get("reference"))
        if kind == "al_test_result":
            result["intended_purpose"] = payload.get("purpose", "verification")
            result["remote_runner_provenance"] = "not established by intended purpose"
            result["captured_validity"] = payload["validity"]
            current = conn.execute(
                "SELECT data FROM execution_entities WHERE execution_id=? AND "
                "epoch_id=? AND family='test' AND id=?",
                (item["execution_id"], item["epoch_id"], payload["id"]),
            ).fetchone()
            result["latest_observed_validity"] = (
                json.loads(current[0]).get("validity") if current else None
            )
    if "document" in reference:
        authored = json.loads(reference.pop("document"))
        result["operator_report"] = {k: authored[k] for k in ("target", "reason", "evidence")}
        result["operator_report"]["actor_kind"] = "local_dashboard_operator"
    return result
