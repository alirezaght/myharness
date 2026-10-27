"""Versioned factual fresh context and independent counterexample assessment."""

import al_artifacts as A
import al_behavior_observations as O
import al_proof_json as J
import al_scope as S
import al_task
from al_schema import refuse
from al_counterexample_search import validate_counterexample_search, require_unblocked_comparison


def enabled(st):
    return "proof_admission" in st


def initialize(st, review):
    if not enabled(st):
        return
    from al_verification_authority import authority_binding, document_binding

    authority = authority_binding(st)
    if authority is None:
        refuse("new review requires admitted verification authority")
    review.update(
        schema_version=2,
        kind="review-session-v2",
        verification_authority={"hash": authority["hash"], "artifact": authority["artifact"]},
        document_hash=document_binding(st)["hash"],
    )


@S.decision
def validate_review_lineage(st, review, packet=None):
    from al_verification_authority import authority_binding, document_binding
    from al_task_editorial import editorial_bridge

    if not enabled(st):
        if review["task_hash"] != al_task.task_hash(st):
            refuse("legacy review task changed")
        return None
    if review.get("schema_version") != 2 or review.get("kind") != "review-session-v2":
        refuse("new-origin review cannot downgrade its versioned authority binding")
    authority = authority_binding(st)
    expected = {"hash": authority["hash"], "artifact": authority["artifact"]} if authority else None
    if review.get("verification_authority") != expected or expected is None:
        refuse("review verification authority changed")
    document = review["document_hash"]
    if (
        review["task_hash"] != document
        or review["candidate"]["fingerprint"] != S.capture_candidate(st)["fingerprint"]
    ):
        refuse("review candidate/original document differs")
    editorial_bridge(st, document, document_binding(st)["hash"], authority["hash"])
    if packet is not None:
        if (
            packet.get("schema_version") != 2
            or packet.get("kind") != "review-packet-v2"
            or packet.get("verification_authority") != expected
            or packet.get("review_id") != review["id"]
            or packet.get("candidate_hash") != review["candidate"]["fingerprint"]
            or packet.get("task_hash") != packet.get("document_hash")
        ):
            refuse("review packet authority/candidate/document differs")
        editorial_bridge(st, document, packet["document_hash"], authority["hash"])
        document = packet["document_hash"]
    return editorial_bridge(st, document, document_binding(st)["hash"], authority["hash"])


@S.decision
def factual_evidence(st):
    import al_ledger as L

    fields = {
        "id",
        "area",
        "kind",
        "tier",
        "run",
        "argv",
        "cwd",
        "scope",
        "sources",
        "env",
        "status",
        "exit",
        "counts",
        "log",
        "log_sha256",
        "reservation",
        "pid",
        "started_at",
        "ended_at",
        "fp_before",
        "fp_after",
        "execution_receipt",
        "execution_association",
        "execution_projection_digest",
        "setup",
        "execution_stage",
    }
    return [
        dict(
            {key: value for key, value in entry.items() if key in fields},
            validity={"verdict": L.entry_verdict(st, entry)[0]},
        )
        for entry in L.ledger(st)["entries"]
    ]


def packet(st, data):
    if not enabled(st):
        return data
    review = st["review"]
    validate_review_lineage(st, review)
    from al_rework_recovery_contract import lineage
    if 'rework_recovery' in st and ('rework_recovery_lineage' not in data or data['rework_recovery_lineage']!=lineage(st)):
        refuse('fresh/comparison recovery lineage missing or changed')
    from al_verification_authority import document_binding

    data.update(
        schema_version=2,
        kind="review-packet-v2",
        document_hash=document_binding(st)["hash"],
        task_hash=document_binding(st)["hash"],
        verification_authority=review["verification_authority"],
        behavior=O.behavior_packet(st),
    )
    if data["stage"] == "fresh":
        # Never expose notes, arbitrary ledger metadata, implementation plans or
        # earlier findings to the independently invoked fresh reviewer.
        data["evidence"] = factual_evidence(st)
        data["amendments"] = [
            {"artifact": entry["artifact"], "content": J.read(entry["artifact"])}
            for entry in st["amendments"]
        ]
        data.pop("escapes", None)
        allowed = {
            "attempt",
            "schema_version",
            "kind",
            "review_id",
            "stage",
            "candidate_hash",
            "task_hash",
            "task_package",
            "amendments",
            "candidate",
            "evidence",
            "gates",
            "document_hash",
            "verification_authority",
            "behavior",
            "rework_recovery_lineage",
        }
        if set(data) != allowed:
            refuse("fresh review context contains unsupported inputs")
    return data


def validate_report(st, report, stage, packet_value):
    if not enabled(st):
        return
    required = {
        "schema_version",
        "kind",
        "review_id",
        "stage",
        "reviewer_role",
        "model",
        "invocation_id",
        "candidate_hash",
        "task_hash",
        "packet_hash",
        "inspected_artifacts",
        "criteria",
        "findings",
        "status",
        "verification_authority_hash",
        "document_hash",
        "attempt_id",
        "effort",
        "policy_digest",
    }
    optional = {"blocking", "nonblocking", "core", "tests", "both", "assessments", "plan_defects"}
    if stage == "fresh":
        required.add("counterexample_search")
    else:
        required.update({"prior_resolutions", "plan_defects", "assessments"})
    if not isinstance(report,dict):
        refuse("version-two Architect report must be an object")
    if type(report.get("schema_version")) is not int or report.get("schema_version")!=2 or report.get("kind")!="architect-review-report-v2":
        refuse("modern Architect report requires schema_version 2 and kind architect-review-report-v2; cannot downgrade its schema")
    missing=required-report.keys();extra=set(report)-required-optional
    if missing or extra:
        refuse("invalid closed version-two Architect report: missing="+str(sorted(missing))+" extra="+str(sorted(extra)))
    validate_review_lineage(st, st["review"], packet_value)
    if (
        report["document_hash"] != packet_value["document_hash"]
        or report["verification_authority_hash"] != packet_value["verification_authority"]["hash"]
    ):
        refuse("report does not retain issued document/authority provenance")
    if stage == "fresh":
        if "prior_resolutions" in report or any(
            "resolution" in finding for finding in report["findings"]
        ):
            refuse("fresh report cannot contain any resolution keys")
        if report.get("plan_defects"):
            refuse("fresh reviewer has no implementation-plan comparison context")
        validate_counterexample_search(st, report)
    else:
        require_unblocked_comparison(st, report)


def retain_lineage(st, sid, report_ref, stage):
    packet_ref = st["review"]["packets"][stage]
    bridge = validate_review_lineage(st, st["review"], A.read_json(packet_ref))
    if bridge and bridge["overlay_refs"]:
        value = {
            "schema_version": 1,
            "kind": "review-editorial-validation-v1",
            "review_id": st["review"]["id"],
            "stage": stage,
            "packet": packet_ref,
            "report": report_ref,
            "candidate_fingerprint": st["review"]["candidate"]["fingerprint"],
            "lineage": bridge,
        }
        ref = J.snapshot(sid, value, value["kind"])
        st["review"].setdefault("editorial_validations", []).append(ref)
