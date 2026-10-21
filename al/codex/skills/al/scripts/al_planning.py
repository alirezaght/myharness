"""Issued planning packet, immutable report and separate obligation admission."""

import copy
from pathlib import Path

import al_artifacts as A
import al_behavior_obligations as B
import al_new_origin as N
import al_planning_attempts as P
import al_proof_json as J
import al_requirement_bindings as Q
import al_store
from al_schema import refuse


REPORT = {
    "schema_version",
    "kind",
    "attempt_id",
    "invocation_id",
    "model",
    "effort",
    "policy_digest",
    "packet_sha256",
    "binding",
    "specification",
    "routing",
    "authored_obligations",
}


def binding(st, source_sha256, planning_occurrence=None, document=None):
    from al_task_editorial import document_binding, require_document
    document = document_binding(st)["hash"] if document is None else require_document(st, document)
    return {
        "repository_identity": st["identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
        "requirement_lineage_hash": Q.lineage(st),
        "planning_occurrence": N.occurrence(st)
        if planning_occurrence is None
        else planning_occurrence,
        "source_observation_sha256": source_sha256,
        "document_hash": document,
        "routing": routing(st),
        "runtime_pin": st["proof_admission"]["runtime_pin"],
        "verifier_policy": N.policy(),
    }


def ready(st):
    N.validate(st)
    if st["proof_admission"]["status"] != "pending":
        refuse("planning provenance is already admitted; use a current substantive revision")
    if (
        st["status"] != "RUNNING"
        or st["spec_status"] != "SPEC_READY"
        or not st.get("classification")
    ):
        refuse("planning provenance requires running SPEC_READY and classification")
    scoped = bool(
        st.get("amendments")
        and st["amendments"][-1].get("scoped")
        and any(
            value.get("amendment") == st["amendments"][-1]["artifact"]["sha256"]
            and value.get("revision") == Q.lineage(st)
            and value.get("state") in {"Required", "Validated"}
            for value in st.get("integration", {}).get("revalidations", {}).values()
        )
    )
    if st["phase"] not in {"PLANNING", "PSEUDOCODE"} and not scoped:
        refuse("planning provenance requires current planning or acknowledged scoped revalidation")
    from al_writers import operation_gaps
    from al_gate_continuation import _uncertain as cleanup_uncertain

    missing = operation_gaps(st)
    missing += [
        "owned cleanup unresolved: " + entry["id"]
        for entry in st["evidence"]["entries"]
        if cleanup_uncertain(entry)
    ]
    if missing:
        refuse("; ".join(missing))


def packet(st):
    current = st.get("planning_authorship")
    if not isinstance(current, dict) or not {"packet", "attempt"} <= current.keys():
        refuse("no issued Architect planning packet")
    value = J.read(current["packet"])
    J.closed(
        value,
        {
            "schema_version",
            "kind",
            "binding",
            "attempt",
            "task_package",
            "accepted_amendments",
            "routing",
            "source_observation",
        },
        "Architect planning packet",
    )
    if value["schema_version"] != 1 or value["kind"] != "architect-planning-packet-v1":
        refuse("unsupported Architect planning packet")
    observed = J.read(value["source_observation"])
    historical = st["proof_admission"]["status"] == "admitted"
    if not historical:
        from al_task_attribution import observe

        if observed != observe(st):
            refuse("planning source observation changed; issue a new bound packet")
    occurrence = value["binding"]["planning_occurrence"] if historical else N.occurrence(st)
    expected = binding(st, J.digest(observed), occurrence,
                       value["binding"]["document_hash"] if historical else None)
    attempt = P.validate(st, current["attempt"], expected)
    if (
        value["attempt"] != attempt
        or value["binding"] != expected
        or value["task_package"] != st["task_package"]
        or value["accepted_amendments"] != [entry["artifact"] for entry in st["amendments"]]
        or value["routing"] != routing(st)
    ):
        refuse("planning packet no longer matches current task/run/occurrence")
    return current, value, attempt


def routing(st):
    return {"classification": st["classification"], "designer_required": st["designer_required"]}


def validate_report(st, report, current, contents, attempt, report_ref):
    J.closed(report, REPORT, "Architect planning report")
    if (
        type(report["schema_version"]) is not int
        or report["schema_version"] != 1
        or report["kind"] != "architect-planning-report-v1"
        or report["attempt_id"] != attempt["attempt_id"]
        or report["model"] not in {attempt["model"], attempt["slug"]}
        or report["effort"] != attempt["effort"]
        or report["policy_digest"] != attempt["policy_digest"]
        or report["packet_sha256"] != current["packet"]["sha256"]
        or J.raw(report["binding"]) != J.raw(contents["binding"])
    ):
        refuse("Architect report does not match the issued immutable attempt/packet")
    if "report" not in current and attempt["model"] in st.get("unavailable_models", []):
        refuse("unsaved unavailable Architect attempt requires recorded replacement")
    J.text(report["invocation_id"], "actual Architect invocation ID")
    decision = J.closed(
        report["specification"], {"status", "reason"}, "planning specification verdict"
    )
    J.text(decision["reason"], "Architect specification rationale")
    if decision["status"] != "SPEC_READY":
        refuse("blocked specification cannot admit observable obligations")
    if J.raw(report["routing"]) != J.raw(contents["routing"]) or J.raw(report["routing"]) != J.raw(
        routing(st)
    ):
        refuse("Architect routing decision differs from admitted classification")
    authored = copy.deepcopy(report["authored_obligations"])
    B.validate_obligations(
        st,
        authored,
        report_ref=None,
        planning_occurrence=contents["binding"]["planning_occurrence"],
        observation_ref=contents["source_observation"],
        document_hash=contents["binding"]["document_hash"],
    )
    if report_ref is not None:
        authored["authorship"]["report_ref"] = report_ref
    return authored


def cmd_packet(a, sid):
    st = al_store.load(sid)
    ready(st)
    from al_task_attribution import observe

    observed = observe(st)
    target = binding(st, J.digest(observed))
    previous = st.get("planning_authorship")
    if previous and not a.replace_unavailable_attempt:
        old = J.read(previous["packet"])
        if old["binding"] == target:
            print(J.raw({"packet": previous["packet"], "content": old}).decode())
            return
    if previous:
        same = J.read(previous["packet"])["binding"] == target
        if same and previous.get("report"):
            refuse("saved Architect planning report is immutable")
        if same and not a.replace_unavailable_attempt:
            refuse("existing planning attempt requires explicit unavailable-model replacement")
    attempt_ref = P.issue(
        st, sid, target, previous, replace=bool(previous and a.replace_unavailable_attempt)
    )
    observation = J.snapshot(sid, observed, "planning-source-observation-v1")
    value = {
        "schema_version": 1,
        "kind": "architect-planning-packet-v1",
        "binding": target,
        "attempt": J.read(attempt_ref),
        "task_package": st["task_package"],
        "accepted_amendments": [entry["artifact"] for entry in st["amendments"]],
        "routing": routing(st),
        "source_observation": observation,
    }
    reference = J.snapshot(sid, value, "architect-planning-packet-v1")
    if previous:
        st.setdefault("planning_history", []).append(previous)
    st["planning_authorship"] = {"attempt": attempt_ref, "packet": reference}
    st["proof_admission"]["planning_occurrence"] = N.occurrence(st)
    if observe(st) != observed:
        refuse("planning source moved before packet publication")
    al_store.save(sid, st)
    print(J.raw({"packet": reference, "content": value}).decode())


def cmd_report(a, sid):
    st = al_store.load(sid)
    ready(st)
    current, contents, attempt = packet(st)
    if "report" in current:
        refuse("saved Architect planning report cannot be replaced")
    report = J.loads(Path(a.file).read_bytes())
    validate_report(st, report, current, contents, attempt, None)
    reference = J.snapshot(sid, report, "architect-planning-report-v1", a.file)
    materialized = validate_report(st, report, current, contents, attempt, reference)
    obligations = J.snapshot(
        sid, materialized, "behavior-obligations-v1", "admitted Architect report materialization"
    )
    current.update(report=reference, obligations=obligations)
    al_store.save(sid, st)
    print(J.raw({"report": reference, "materialized_obligations": obligations}).decode())


def validate_admitted(st):
    current, contents, attempt = packet(st)
    if not {"report", "obligations"} <= current.keys():
        refuse("admission lacks immutable Architect report and obligations")
    report = J.read(current["report"])
    expected = validate_report(st, report, current, contents, attempt, current["report"])
    actual = J.read(current["obligations"])
    if J.raw(actual) != J.raw(expected):
        refuse("materialized obligations differ beyond sole noncircular report_ref substitution")
    B.validate_obligations(
        st,
        actual,
        report_ref=current["report"],
        planning_occurrence=contents["binding"]["planning_occurrence"],
        observation_ref=contents["source_observation"],
        document_hash=contents["binding"]["document_hash"],
    )
    return current


def cmd_admit(a, sid):
    st = al_store.load(sid)
    ready(st)
    if st["proof_admission"]["status"] != "pending":
        refuse("observable obligations already admitted")
    current = validate_admitted(st)
    if a.file and Path(a.file).read_bytes() != J.reference(current["obligations"]):
        refuse("supplied materialized obligation bytes differ from retained report")
    from al_verification_authority import publish

    authority = publish(st, sid, current["obligations"])
    packet(st)
    st["proof_admission"].update(
        status="admitted",
        authority=authority,
        packet=current["packet"],
        report=current["report"],
        obligations=current["obligations"],
    )
    N.validate(st)
    al_store.save(sid, st)
    print(J.raw({"status": "admitted", "authority": authority}).decode())


def register(sub):
    children = sub.add_parser("planning").add_subparsers(dest="action", required=True)
    p = children.add_parser("packet")
    p.add_argument("--replace-unavailable-attempt", action="store_true")
    p.add_argument("--recovery-file", dest="operation_recovery_file")
    p.set_defaults(fn=cmd_packet)
    p = children.add_parser("report")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_report)
    p = children.add_parser("admit")
    p.add_argument("--file")
    p.add_argument("--recovery-file", dest="operation_recovery_file")
    p.set_defaults(fn=cmd_admit)
