"""Data-only closed validation for versioned publication delivery projections."""

import math
import re
from pathlib import Path

FIELDS = set(
    (
        "schema_version kind repository_identity session_id run_id source_revision status"
        " provenance current_source record_ref authorization ready_to_publish code implementation"
        " review release"
    ).split()
)
STATUSES = {"not_requested", "pending", "published_review_pending", "published", "failed"}


def closed(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("unknown publication projection fields")


def errors(value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("invalid publication readiness gaps")


def reference(value):
    closed(value, {"id", "kind", "sha256", "stored_path", "source", "captured_at"})
    if (
        value["kind"] != "publication-record-v1"
        or not isinstance(value["sha256"], str)
        or len(value["sha256"]) != 64
        or value["id"] != value["sha256"]
        or any(c not in "0123456789abcdef" for c in value["sha256"])
    ):
        raise ValueError("invalid publication record reference")
    if (
        not isinstance(value["stored_path"], str)
        or not Path(value["stored_path"]).is_absolute()
        or not isinstance(value["source"], str)
    ):
        raise ValueError("invalid publication reference path/provenance")
    if type(value["captured_at"]) not in {int, float} or not math.isfinite(value["captured_at"]):
        raise ValueError("invalid publication observation timestamp")


def validate_delivery(payload, binding):
    closed(payload, {"projection_version", "status", "publication"})
    if type(payload["projection_version"]) is not int or payload["projection_version"] != 2:
        raise ValueError("unsupported publication delivery version")
    value = payload["publication"]
    closed(value, FIELDS)
    if (
        value["schema_version"] != 1
        or type(value["schema_version"]) is not int
        or value["kind"] != "publication-readiness-v1"
        or value["status"] not in STATUSES
        or value["status"] != payload["status"]
    ):
        raise ValueError("invalid publication delivery status")
    if value["session_id"] != binding["al_session_id"] or value["run_id"] != binding["al_run_id"]:
        raise ValueError("foreign publication delivery run")
    if not isinstance(value["repository_identity"], dict) or not value["repository_identity"].get(
        "root"
    ):
        raise ValueError("missing publication repository identity")
    closed(value["source_revision"], {"commit_oid", "candidate_fingerprint"})
    revision = value["source_revision"]
    if (
        not isinstance(revision["commit_oid"], str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision["commit_oid"])
        or not isinstance(revision["candidate_fingerprint"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", revision["candidate_fingerprint"])
    ):
        raise ValueError("invalid publication source identity")
    if value["provenance"] not in {None, "reported", "independently_verified"}:
        raise ValueError("unknown publication observation provenance")
    if type(value["current_source"]) is not bool or type(value["ready_to_publish"]) is not bool:
        raise ValueError("invalid publication readiness flags")
    if value["record_ref"] is not None:
        reference(value["record_ref"])
    for key, fields in (
        ("authorization", {"ready", "gaps"}),
        ("code", {"ready", "gaps", "bindings"}),
    ):
        row = value[key]
        closed(row, fields)
        if type(row["ready"]) is not bool:
            raise ValueError("invalid publication check readiness")
        errors(row["gaps"])
        if row["ready"] != (not row["gaps"]):
            raise ValueError("incoherent publication check readiness")
    if not isinstance(value["code"]["bindings"], list):
        raise ValueError("invalid publication check bindings")
    closed(value["implementation"], {"status", "phase"})
    closed(value["review"], {"ready", "status", "review_id"})
    closed(value["release"], {"ready", "status", "reason"})
    if (
        type(value["review"]["ready"]) is not bool
        or value["review"]["status"] not in {"approved", "pending"}
        or value["review"]["ready"] != (value["review"]["status"] == "approved")
    ):
        raise ValueError("invalid independent publication review readiness")
    if value["release"]["ready"] is not False or value["release"]["status"] != "not_assessed":
        raise ValueError("publication cannot grant release authority")
    if value["ready_to_publish"] != (value["authorization"]["ready"] and value["code"]["ready"]):
        raise ValueError("publication readiness cannot waive authorization or checks")
    if value["status"] == "published_review_pending" and value["review"]["ready"]:
        raise ValueError("incoherent pending publication review")
    if value["status"] in {"published", "published_review_pending"} and (
        not value["current_source"] or value["record_ref"] is None or value["provenance"] is None
    ):
        raise ValueError("published status requires a current bound observation")
    if value["status"] == "published" and not value["review"]["ready"]:
        raise ValueError("unreviewed publication must remain pending review")
