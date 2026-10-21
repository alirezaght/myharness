"""Immutable Architect planning attempts, using the existing model policy."""

import secrets

import al_models as M
import al_proof_json as J
import al_review_attempts as R
from al_schema import refuse


FIELDS = {
    "schema_version",
    "kind",
    "attempt_id",
    "role",
    "model",
    "slug",
    "effort",
    "policy_digest",
    "fallback_event_reference",
    "binding",
}


def issue(st, sid, binding, previous=None, *, replace=False):
    slot = M.resolve(st, R.ROLE)
    if slot["model"] in st.get("unavailable_models", []):
        refuse("Architect model unavailable")
    fallback = R.event(st, slot)
    if replace:
        if not previous or previous.get("report"):
            refuse("only an issued unsaved planning attempt may be replaced")
        old = J.read(previous["attempt"])
        if (
            old["model"] not in st.get("unavailable_models", [])
            or R.slot_fields(old) == R.slot_fields(slot)
            or not fallback
        ):
            refuse("planning replacement requires newly recorded approved unavailability")
    attempt = {
        "schema_version": 1,
        "kind": "architect-planning-attempt-v1",
        "attempt_id": secrets.token_hex(12),
        "role": R.ROLE,
        **R.slot_fields(slot),
        "policy_digest": R.policy_digest(),
        "binding": binding,
        "fallback_event_reference": J.snapshot(sid, fallback, "model-fallback-event")
        if fallback
        else None,
    }
    return J.snapshot(sid, attempt, "architect-planning-attempt-v1")


def validate(st, reference, binding):
    attempt = J.read(reference)
    J.closed(attempt, FIELDS, "Architect planning attempt")
    if (
        type(attempt["schema_version"]) is not int
        or attempt["schema_version"] != 1
        or attempt["kind"] != "architect-planning-attempt-v1"
        or attempt["role"] != R.ROLE
        or attempt["binding"] != binding
        or attempt["policy_digest"] != R.policy_digest()
    ):
        refuse("foreign or stale Architect planning attempt")
    slots = [slot for slot in (M.POLICY[R.ROLE]["primary"], M.POLICY[R.ROLE]["fallback"]) if slot]
    if not any(R.slot_fields(attempt) == R.slot_fields(slot) for slot in slots):
        refuse("planning attempt uses an unapproved model")
    fallback = attempt["fallback_event_reference"]
    if R.slot_fields(attempt) == R.slot_fields(M.POLICY[R.ROLE]["primary"]):
        if fallback is not None:
            refuse("primary planning attempt cannot claim fallback provenance")
    else:
        event = J.read(fallback) if fallback else None
        if (
            not event
            or event not in st.get("model_fallbacks", [])
            or event["role"] != R.ROLE
            or event["to"] != M.label(attempt)
            or not event["reason"]
        ):
            refuse("planning fallback provenance is missing or foreign")
    return attempt
