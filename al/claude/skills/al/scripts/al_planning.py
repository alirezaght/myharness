"""Issued planning packet, immutable report and separate obligation admission."""

import copy
from pathlib import Path

import al_artifacts as A
import al_behavior_obligations as B
import al_new_origin as N
import al_planning_attempts as P
import al_planning_template as Template
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


def readiness_gap(st, *, authoring=False):
    """The actual planning guards, classified without inspecting exception prose."""
    N.validate(st)
    if st["proof_admission"]["status"] != "pending":
        return "planning-current-admission", "planning provenance is already admitted; use a current substantive revision"
    if (
        st["status"] != "RUNNING"
        or st["spec_status"] != "SPEC_READY"
        or not st.get("classification")
    ):
        return "planning-specification", "planning provenance requires running SPEC_READY and classification"
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
    from al_transitions import WRITING
    retained = st["phase"] in WRITING | {"VERIFICATION", "REWORK_REASSESSMENT"} and bool(st.get("implementation_readiness"))
    if st["phase"] not in {"PLANNING", "PSEUDOCODE"} and not scoped and not retained:
        return "planning-phase-context", "planning provenance requires current planning or acknowledged scoped revalidation"
    from al_writers import operation_gaps
    from al_gate_continuation import _uncertain as cleanup_uncertain

    missing = operation_gaps(st)
    if authoring:
        # Only normal, registered work may overlap non-authorizing authorship.
        # Every other operation/cleanup gap remains a refusal, including unknown gaps.
        active = {"writer " + writer["id"] + " is ACTIVE"
                  for writer in st.get("writers", {}).values() if writer["state"] == "ACTIVE"}
        missing = [gap for gap in missing if gap not in active]
    missing += [
        "owned cleanup unresolved: " + entry["id"]
        for entry in st["evidence"]["entries"]
        if cleanup_uncertain(entry)
    ]
    if missing:
        return "planning-owned-operations", "; ".join(missing)

    return None


def ready(st, *, authoring=False):
    missing = readiness_gap(st, authoring=authoring)
    if missing is not None:
        refuse(missing[1])

def packet(st, *, current_source=True):
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
    historical = st["proof_admission"]["status"] == "admitted" or not current_source
    if not historical:
        from al_task_attribution import observe

        if observed != observe(st, expected=observed):
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


def authoring_view(st):
    """Response metadata only; the closed immutable proof packet is unchanged."""
    return {
        "authority": "none",
        "current_admission": "pending",
        "source_currency": "issued-observation-only",
        "active_writer_ids": sorted(writer["id"] for writer in st.get("writers", {}).values()
                                    if writer["state"] == "ACTIVE"),
    }


def validate_report(st, report, current, contents, attempt, report_ref, *, current_implementation=True):
    J.closed(report, REPORT, "Architect planning report")
    if Template.placeholders(report):
        refuse("Architect planning report still contains FILL: template placeholders")
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
        current_implementation=current_implementation,
    )
    if report_ref is not None:
        authored["authorship"]["report_ref"] = report_ref
    return authored


def cmd_packet(a, sid):
    st = al_store.load(sid)
    ready(st, authoring=True)
    # This marks begun implementation context, never completed or verified changes.
    partially_built = bool(st.get("writers")) or bool(
        st.get("task_attribution", {}).get("contributions")
    )
    from al_task_attribution import observe

    observed = observe(st)
    target = binding(st, J.digest(observed))
    previous = st.get("planning_authorship")
    if previous and not a.replace_unavailable_attempt:
        old = J.read(previous["packet"])
        if old["binding"] == target:
            print(J.raw({"packet": previous["packet"], "content": old,
                         "authoring": authoring_view(st),
                         "report_template": Template.template(previous["packet"], old, partially_built=partially_built)}).decode())
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
    if observe(st, expected=observed) != observed:
        refuse("planning source moved before packet publication")
    al_store.save(sid, st)
    print(J.raw({"packet": reference, "content": value,
                 "authoring": authoring_view(st),
                 "report_template": Template.template(reference, value, partially_built=partially_built)}).decode())


def cmd_report(a, sid):
    st = al_store.load(sid)
    ready(st, authoring=True)
    # Preserve what the Architect actually authored against its issued observation.
    # Only admission may attest that this observation still matches current source.
    current, contents, attempt = packet(st, current_source=False)
    if "report" in current:
        refuse("saved Architect planning report cannot be replaced")
    report = J.loads(Path(a.file).read_bytes())
    if current.get('rebinds') and not (a.rebind_receipt and a.revalidation_file):
        refuse('issued planning rebind requires --rebind-receipt and --revalidation-file')
    validate_report(st, report, current, contents, attempt, None)
    receipt = None
    if bool(a.rebind_receipt) != bool(a.revalidation_file):
        refuse('supply both --rebind-receipt and --revalidation-file')
    if a.rebind_receipt:
        from al_planning_rebind import revalidation
        packet(st)
        assessment = J.loads(Path(a.revalidation_file).read_bytes())
        receipt = revalidation(st, current, report, a.rebind_receipt, assessment)
    reference = J.snapshot(sid, report, "architect-planning-report-v1", a.file)
    materialized = validate_report(st, report, current, contents, attempt, reference)
    obligations = J.snapshot(
        sid, materialized, "behavior-obligations-v1", "admitted Architect report materialization"
    )
    current.update(report=reference, obligations=obligations)
    if receipt:
        current['rebind_validation'] = {'receipt': receipt, 'revalidation': J.snapshot(
            sid, assessment, 'planning-rebind-revalidation-v1', a.revalidation_file)}
    al_store.save(sid, st)
    print(J.raw({"report": reference, "materialized_obligations": obligations,
                 "authoring": authoring_view(st)}).decode())


def validate_admitted(st):
    current, contents, attempt = packet(st)
    if not {"report", "obligations"} <= current.keys():
        refuse("admission lacks immutable Architect report and obligations")
    report = J.read(current["report"])
    from al_planning_rebind import submitted
    submitted(st, current, report)
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
    for writer in st.get('writers', {}).values():
        if writer.get('degraded'):
            writer['degraded'].update(proof_status='reviewed', report=current['report'],
                                      observation=J.read(current['packet'])['source_observation'])
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
    p.add_argument("--rebind-receipt")
    p.add_argument("--revalidation-file")
    p.set_defaults(fn=cmd_report)
    from al_planning_rebind import cmd_rebind
    p = children.add_parser('rebind')
    p.add_argument('--from-packet', required=True)
    p.add_argument('--from-report', required=True)
    p.set_defaults(fn=cmd_rebind)
    p = children.add_parser("admit")
    p.add_argument("--file")
    p.add_argument("--recovery-file", dest="operation_recovery_file")
    p.set_defaults(fn=cmd_admit)
