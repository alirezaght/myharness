"""Reopen admitted planning obligations for a genuine current Architect revision.

`planning revise` archives the admitted proof, reopens admission the way retarget and delivery
correction do, and re-enters PLANNING. The Architect then authors the revision through the
ordinary `planning packet` -> `planning report` -> `planning admit` validation. Nothing here
authors, admits or approves obligations, and no recorded evidence or history is rewritten."""

import copy
import time

import al_artifacts as A
import al_proof_json as J
import al_store
from al_schema import refuse


KIND = "planning-obligation-revision-v1"
RECORD = {"schema_version", "kind", "proof_admission", "phase", "planning_occurrence",
          "reason", "requested_by", "at"}
PHASES = ("PLANNING", "PSEUDOCODE", "IMPLEMENTATION", "REWORK", "FINALIZATION", "VERIFICATION")
REASON_LIMIT = 2000
INVOCATION_LIMIT = 200
FRESH_RUN = ("A run pinned to an AL runtime without `planning revise`, or one whose review has started, "
             "uses a fresh verification-only run that carries the original specification and lists each "
             "old admitted obligation with its disposition (reference/workflow.md)")


def revision_blockers(st):
    """Why admitted planning cannot be reopened now; an empty list when it can."""
    admission = st.get("proof_admission") or {}
    if admission.get("status") != "admitted":
        return ["planning obligations are not admitted; author them with planning packet, planning report "
                "and planning admit"]
    out = []
    if st["status"] != "RUNNING":
        out.append("the run is " + st["status"])
    if st.get("spec_status") != "SPEC_READY" or not st.get("classification"):
        out.append("a revision requires SPEC_READY and a classification")
    if st["phase"] not in PHASES:
        out.append("phase " + st["phase"] + " cannot reopen planning (allowed from " + ", ".join(PHASES) + ")")
    if st.get("approval"):
        out.append("the run has an approval")
    if st.get("review") is not None or st.get("review_iteration", 0) > 0:
        out.append("review has started and its packets are bound to the admitted obligations")
    if st.get("gate_recovery"):
        out.append("a gate recovery is active")
    recovery = st.get("rework_recovery") or {}
    if recovery.get("pending") or recovery.get("stop"):
        out.append("a bounded rework recovery is pending or stopped")
    from al_inherited_lineage import require_recovery_complete
    try:
        require_recovery_complete(st)
    except SystemExit as exc:
        out.append("inherited-lineage recovery is incomplete: " + str(exc.code).removeprefix("al_state: "))
    from al_writers import operation_gaps
    from al_gate_continuation import _uncertain as cleanup_uncertain
    out += operation_gaps(st)
    out += ["owned cleanup unresolved: " + entry["id"]
            for entry in st["evidence"]["entries"] if cleanup_uncertain(entry)]
    return out


def records(st):
    """The archived revision records, oldest first."""
    out = []
    for reference in st.get("planning_revisions", []):
        value = J.read(reference)
        J.closed(value, RECORD, "planning obligation revision")
        if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["kind"] != KIND or type(value["planning_occurrence"]) is not int
                or value["proof_admission"].get("status") != "admitted"):
            refuse("unsupported planning obligation revision record")
        out.append(value)
    return out


def lineage(st, planning_occurrence):
    """The revision and supersedes that obligations authored in this planning occurrence must carry.

    None when no revision precedes that occurrence: such obligations keep their original contract."""
    applicable = [value for value in records(st) if value["planning_occurrence"] <= planning_occurrence]
    if not applicable:
        return None
    previous = applicable[-1]["proof_admission"]["obligations"]
    return {"revision": J.read(previous)["revision"] + 1, "supersedes": previous}


def _bounded(value, label, limit):
    value = (value or "").strip()
    if not value:
        refuse("planning revise requires " + label)
    if len(value) > limit:
        refuse(label + " is limited to " + str(limit) + " characters")
    return value


def _stale_observations(st, authority):
    """How many recorded behavior observations are bound to the archived authority."""
    try:
        hash_ = A.digest(J.raw(J.read(authority)))
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return 0
    count = 0
    for reference in st.get("behavior_observations", []):
        try:
            if J.read(reference)["binding"]["verification_authority_hash"] == hash_:
                count += 1
        except (OSError, ValueError, KeyError, TypeError, SystemExit):
            continue
    return count


def cmd_revise(a, sid):
    st = al_store.load(sid)
    reason = _bounded(a.reason, "--reason TEXT (why the Architect revises the admitted obligations)", REASON_LIMIT)
    requested_by = None
    if a.invocation_id is not None:
        import al_planning_template as Template
        requested_by = _bounded(a.invocation_id, "a non-empty --invocation-id", INVOCATION_LIMIT)
        if Template.placeholders(requested_by):
            refuse("--invocation-id needs the requesting invocation id, not a FILL: placeholder")
    blockers = revision_blockers(st)
    if blockers:
        refuse("planning revise refused: " + "; ".join(blockers) + ". " + FRESH_RUN + ".")
    import al_new_origin as N
    admission = st["proof_admission"]
    stale = _stale_observations(st, admission["authority"])
    phase = st["phase"]
    # Always a new PLANNING occurrence, so packets issued before the revision are stale.
    al_store.set_phase(st, "PLANNING", ["al-architect"])
    reentered = N.occurrence(st)
    archive = J.snapshot(sid, {
        "schema_version": 1, "kind": KIND, "proof_admission": copy.deepcopy(admission),
        "phase": phase, "planning_occurrence": reentered, "reason": reason,
        "requested_by": requested_by, "at": time.time(),
    }, KIND)
    st.setdefault("planning_revisions", []).append(archive)
    st["proof_admission"] = {key: admission[key] for key in N.FIELDS}
    st["proof_admission"].update(status="pending", planning_occurrence=reentered,
                                 invalidation_generation=admission["invalidation_generation"] + 1)
    if st.get("implementation_readiness") is not None:
        st["implementation_readiness"]["obligations"] = sorted(set(
            st["implementation_readiness"]["obligations"] + ["planning obligations revised; revalidate proof"]))
    N.validate(st)
    al_store.save(sid, st)
    following = lineage(st, reentered)
    print("AL · planning revise · " + phase + " -> PLANNING · admission generation "
          + str(admission["invalidation_generation"]) + " -> " + str(st["proof_admission"]["invalidation_generation"]))
    print("  archived: " + archive["sha256"] + " (" + archive["stored_path"] + ")")
    print("  kept: evidence, gate plans, completed phases, review history, counters, original report and obligations")
    print("  no longer counting: " + str(stale) + " behavior observation(s) bound to the previous authority")
    print("  next: planning packet (its report_template carries revision " + str(following["revision"])
          + " superseding " + following["supersedes"]["sha256"][:12] + "); the actual Architect authors the "
          "revision; planning report --file FILE --invocation-id ID; planning admit")
