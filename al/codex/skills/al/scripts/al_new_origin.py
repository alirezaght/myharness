"""Mandatory new-run proof admission bound to independent bootstrap origin."""

import json
import sys

import al_artifacts as A
import al_models as M
import al_runtime_resources as R
from al_schema import refuse


ADMITTED_FIELDS = {"authority", "packet", "report", "obligations"}

FIELDS = {
    "schema_version",
    "kind",
    "status",
    "initialization",
    "original_task_hash",
    "requirement_lineage_hash",
    "runtime_pin",
    "verifier_policy",
    "planning_occurrence",
    "invalidation_generation",
}


def context():
    value = sys.modules.get("_al_run_pin_context")
    if value is None:
        refuse("new candidate commands require the protected managed alctl run launcher")
    return value


def require_dispatch():
    context()


def policy():
    value = {
        "id": "al-proof-policy",
        "version": 1,
        "runtime_implementation": R.implementation_digest(),
        "models": M.POLICY,
    }
    return {
        "id": value["id"],
        "version": value["version"],
        "digest": A.digest(json.dumps(value, sort_keys=True, allow_nan=False).encode()),
    }


def occurrence(st):
    values = [i for i, value in enumerate(st["phase_occurrences"]) if value["phase"] == "PLANNING"]
    if not values:
        refuse("proof admission requires an actual planning occurrence")
    return values[-1]


def initialize(st):
    from installer.runtime_pin_initialization import initialize as publish
    from al_task import revision_hashes

    origin = context()
    transaction = origin.transaction
    supplied = {
        "schema_version": transaction["schema_version"],
        "kind": "proof-admission-initialization-v" + str(transaction["schema_version"]),
        "repository_identity": transaction["repository_identity"],
        "session_id": st["session_id"],
        "run_id": st["run_id"],
        "original_task_package": st["task_package"],
        "requirement_lineage_hash": revision_hashes(st)[-1],
        "runtime_pin": transaction["pin"],
        "verifier_policy": policy(),
        "planning_occurrence": occurrence(st),
    }
    if transaction["schema_version"] == 2:
        supplied.update(runtime_layout=transaction["runtime_layout"], runtime_directory=transaction["runtime_directory"])
    try:
        transaction, initial = publish(transaction, supplied)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        refuse("new-run proof initialization failed: " + str(exc))
    origin.transaction = transaction
    st["task_package"] = initial["original_task_package"]
    A.read(st["task_package"])
    st["runtime_origin"] = {
        key: transaction[key]
        for key in (
            "kind",
            "schema_version",
            "run_id",
            "publication_transaction",
            "pin",
            "initialization",
        )
    }
    if transaction["schema_version"] == 2:
        st["runtime_origin"].update(runtime_layout=transaction["runtime_layout"], runtime_directory=transaction["runtime_directory"])
    st["proof_admission"] = {
        "schema_version": 1,
        "kind": "proof-admission-v1",
        "status": "pending",
        "initialization": transaction["initialization"],
        "original_task_hash": initial["original_task_package"]["sha256"],
        "requirement_lineage_hash": initial["requirement_lineage_hash"],
        "runtime_pin": transaction["pin"],
        "verifier_policy": initial["verifier_policy"],
        "planning_occurrence": occurrence(st),
        "invalidation_generation": 0,
    }


def validate(st):
    if "runtime_origin" not in st and "proof_admission" not in st:
        # Existing original issuers and fixed read-only legacy validators retain
        # their old contract. No new candidate public command selects it.
        return
    from installer.runtime_pin_initialization import read_initialization
    from al_task import revision_hashes

    origin = context()
    try:
        initial = read_initialization(origin.transaction)
        admission = st["proof_admission"]
        if (
            set(admission)
            != (FIELDS | ADMITTED_FIELDS if admission.get("status") == "admitted" else FIELDS)
            or type(admission["schema_version"]) is not int
            or admission["schema_version"] != 1
            or admission["kind"] != "proof-admission-v1"
            or admission["status"] not in {"pending", "admitted"}
            or admission["initialization"] != origin.transaction["initialization"]
            or admission["original_task_hash"] != initial["original_task_package"]["sha256"]
            or st["task_package"] != initial["original_task_package"]
            or admission["runtime_pin"] != initial["runtime_pin"]
            or admission["verifier_policy"] != initial["verifier_policy"]
            or policy() != initial["verifier_policy"]
            or admission["requirement_lineage_hash"] != revision_hashes(st)[-1]
            or type(admission["planning_occurrence"]) is not int
            or not 0 <= admission["planning_occurrence"] < len(st["phase_occurrences"])
            or st["phase_occurrences"][admission["planning_occurrence"]]["phase"] != "PLANNING"
            or type(admission["invalidation_generation"]) is not int
            or admission["invalidation_generation"] < 0
        ):
            raise ValueError(
                "proof admission differs from immutable initialization/current lineage"
            )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        refuse("invalid mandatory proof admission: " + str(exc))
    if admission["status"] == "admitted":
        from al_verification_authority import authority_binding

        current = st.get("planning_authorship", {})
        if any(admission[key] != current.get(key) for key in ("packet", "report", "obligations")):
            refuse("admission differs from retained Architect planning provenance")
        authority_binding(st)


def require_admitted(st):
    validate(st)
    if st.get("proof_admission", {}).get("status") == "pending":
        from al_recovery_guidance import refuse_guided
        refuse_guided(st, "proof admission pending; admitted Architect planning obligations are required. "
                      "If proof repair is blocked, use writer start --id ID --role ROLE --files PATHS "
                      "--degraded --reason TEXT for scoped tracked work after SPEC_READY/classification "
                      "and gate planning. This records writers/evidence; it never admits proof or approval.")

    # Refresh only already planned rule changes before reconstructing coverage.
    # Unplanned sources and incomplete discovery still refuse admission.
    from al_gate_refresh import refresh
    currency = refresh(st)
    if not currency["current"]:
        refuse("current gate obligations require replanning: " + str(currency["reasons"]))
    from al_service_coverage import require_ready
    require_ready(st,current=True)


def prepare_save(st, previous=None):
    if "proof_admission" not in st:
        return
    from al_task import revision_hashes

    admission = st["proof_admission"]
    if admission.get("status")=="admitted" and previous and previous.get("proof_admission",{}).get("status")=="pending":
        from al_service_coverage import require_ready
        require_ready(st,current=True)
    lineage = revision_hashes(st)[-1]
    rerouted = bool(
        previous
        and previous.get("proof_admission", {}).get("status") == "admitted"
        and any(previous.get(key) != st.get(key) for key in ("classification", "designer_required"))
    )
    if lineage != admission["requirement_lineage_hash"] or rerouted:
        st["proof_admission"] = {key: admission[key] for key in FIELDS}
        st["proof_admission"].update(
            status="pending",
            requirement_lineage_hash=lineage,
            planning_occurrence=occurrence(st),
            invalidation_generation=admission["invalidation_generation"] + 1,
        )
