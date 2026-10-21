"""Ordinary terminal states require fresh workspace operational capabilities."""

from .activity_observation import validate_fresh_observation
from .activity_retirement import seal, validate_seal
from .guards import require_inactive


def assess(inventory, observation, *, source):
    from .workspace_source import revalidate_source

    revalidate_source(source)
    validate_seal(inventory, "workspace-activity-inventory")
    validate_seal(observation, "workspace-quiescence-observation")
    validate_fresh_observation(observation, inventory)
    if (
        not inventory["stable"]
        or not observation["stable"]
        or observation["errors"]
        or observation["inventory_digest"] != inventory["digest"]
        or observation.get("invoking_run") != inventory["invoking_run"]
    ):
        raise ValueError("incomplete ordinary workspace observation")
    strict = require_inactive(
        [(root["runtime_root"], root["repository"]["root"]) for root in inventory["roots"]]
    )
    from .workspace_activity import invoking, select_profile

    if select_profile(inventory) != "ordinary-terminal-v1" or inventory["invoking_run"] != invoking(
        source
    ):
        raise ValueError("ordinary workspace profile/source differs")
    blockers = []
    for root in inventory["roots"]:
        for record in root["records"]:
            blockers.extend(record["errors"] + root["errors"])
            if (
                record["identity"] == inventory["invoking_run"]
                and record["sha256"] != source["approval"]["state_sha256"]
            ):
                blockers.append("invoking state differs from fresh terminal source approval")
            from .activity_operations import operation_reasons

            blockers.extend(operation_reasons(record, root, observation))
    if any(
        row["result"] not in {"absent", "different-process"} for row in observation["processes"]
    ):
        blockers.append("live or unknown relevant process")
    if any(row["result"] != "held" for row in observation["locks"]):
        blockers.append("required runtime lock is not held")
    if any(row["result"] != "absent" for row in observation["leases"]):
        blockers.append("live or unknown lease")
    if blockers:
        raise ValueError("ordinary workspace activity blocked: " + "; ".join(sorted(set(blockers))))
    return seal(
        {
            "schema_version": 1,
            "kind": "ordinary-workspace-quiescence-assessment-v1",
            "profile": "ordinary-terminal-v1",
            "historical_facts_used": False,
            "eligible": True,
            "source_digest": source["digest"],
            "inventory_digest": inventory["digest"],
            "observation_digest": observation["digest"],
            "strict_activity": strict,
            "limitations": observation["limitations"],
            "blockers": [],
        }
    )
