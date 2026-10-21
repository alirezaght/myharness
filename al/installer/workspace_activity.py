"""Planner-selected activity profiles; historical failure never selects ordinary."""

from contextlib import contextmanager
from pathlib import Path

from .activity_inventory import inventory_activity, repository_identity
from .activity_observation import locked_activity, observe_activity, _nonstate
from .activity_retirement import (
    publish_once,
    read_reference,
    require_workspace_quiescent,
    seal,
    validate_seal,
)
from .registry import canonical, digest, namespace
from .workspace_scope import derive_scope, revalidate_scope
from .workspace_source import revalidate_source

FIELDS = {
    "schema_version",
    "kind",
    "profile",
    "source_digest",
    "deployment_intent_digest",
    "scope",
    "inventory",
}


def invoking(source):
    identity = repository_identity(source["identity"]["root"])
    if any(source["identity"][key] != identity[key] for key in ("root", "git_dir", "common_dir")):
        raise ValueError("workspace invoking worktree identity differs")
    approval = source["approval"]
    runtime = Path(identity["root"]) / ("." + source["platform"]) / "al-runtime"
    return {
        "repository": identity,
        "platform": source["platform"],
        "runtime_root": str(runtime),
        "state_path": str(runtime / (approval["session_id"] + ".json")),
        "session_id": approval["session_id"],
        "run_id": approval["run_id"],
    }


def select_profile(inventory):
    validate_seal(inventory, "workspace-activity-inventory")
    if not inventory["stable"]:
        raise ValueError("workspace activity inventory is not stable")
    found, historical = False, False
    for root in inventory["roots"]:
        if root["errors"]:
            raise ValueError("workspace activity inventory has unsupported boundaries")
        if root.get("orphan_locks"):
            historical = True
        for record in root["records"]:
            if record["errors"] or record["classification"] == "unknown":
                raise ValueError("unknown workspace runtime cannot select an activity profile")
            current = record["identity"] == inventory["invoking_run"]
            ordinary = (
                record["classification"] == "current-session"
                and record["historical_status"] == "APPROVED"
                and record["historical_phase"] == "DONE"
            )
            if current:
                found = True
                if not ordinary:
                    raise ValueError("workspace invoking run is not ordinary terminal")
            if not ordinary:
                historical = True
    if not found:
        raise ValueError("workspace invoking record is absent")
    if not historical:
        from .guards import require_inactive

        require_inactive(
            [(root["runtime_root"], root["repository"]["root"]) for root in inventory["roots"]]
        )
    return "acknowledged-history-v1" if historical else "ordinary-terminal-v1"


def plan_activity(
    source,
    home,
    state_root,
    *,
    deployment_intent_digest,
    explicit=(),
    historical=None,
    orphan_review=None,
):
    if (
        not isinstance(deployment_intent_digest, str)
        or len(deployment_intent_digest) != 64
        or any(c not in "0123456789abcdef" for c in deployment_intent_digest)
    ):
        raise ValueError("invalid workspace deployment intent")
    revalidate_source(source)
    scope = derive_scope(source["identity"]["root"], home, state_root, explicit=explicit)
    inventory = inventory_activity(
        scope["roots"], invoking_run=invoking(source), orphan_review=orphan_review
    )
    profile = select_profile(inventory)
    directory = namespace(state_root) / "workspace-activity"
    reference = publish_once(directory / (inventory["digest"] + ".json"), inventory)
    value = {
        "schema_version": 2,
        "kind": "workspace-activity-binding-v2",
        "profile": profile,
        "source_digest": source["digest"],
        "deployment_intent_digest": deployment_intent_digest,
        "scope": scope,
        "inventory": reference,
    }
    if profile == "ordinary-terminal-v1":
        if historical is not None:
            raise ValueError("ordinary activity cannot carry historical authority")
    else:
        if not isinstance(historical, dict) or set(historical) != {
            "reviewed_history",
            "acknowledgements",
        }:
            raise ValueError(
                "workspace requires exact historical acknowledgements for inventory: "
                + reference["path"]
            )
        value["historical"] = historical
    return seal(value)


def _fresh(binding, source, state_root):
    if (
        not isinstance(binding, dict)
        or binding.get("kind") != "workspace-activity-binding-v2"
        or binding.get("digest")
        != digest(canonical({k: v for k, v in binding.items() if k != "digest"}))
    ):
        raise ValueError("invalid workspace activity binding")
    profile = binding.get("profile")
    fields = (
        FIELDS | {"digest"} | ({"historical"} if profile == "acknowledged-history-v1" else set())
    )
    if (
        set(binding) != fields
        or type(binding["schema_version"]) is not int
        or binding["schema_version"] != 2
        or profile not in {"ordinary-terminal-v1", "acknowledged-history-v1"}
        or binding["source_digest"] != source["digest"]
    ):
        raise ValueError("unknown or foreign workspace activity profile")
    revalidate_source(source)
    revalidate_scope(binding["scope"], state_root)
    stored = validate_seal(read_reference(binding["inventory"]), "workspace-activity-inventory")
    if stored["invoking_run"] != invoking(source):
        raise ValueError("foreign workspace invoking run")
    current = inventory_activity(
        binding["scope"]["roots"],
        invoking_run=stored["invoking_run"],
        nonstate=_nonstate(stored),
        orphan_review=stored["orphan_review"],
    )
    if current != stored:
        raise ValueError("workspace activity membership/state changed")
    if select_profile(current) != profile:
        raise ValueError("workspace planner-selected profile changed")
    return current


@contextmanager
def activity_context(binding, source, state_root):
    inventory = _fresh(binding, source, state_root)
    with locked_activity(inventory) as (locks, limitations):

        def check():
            current = _fresh(binding, source, state_root)
            observation = observe_activity(current, locks=locks, limitations=limitations)
            if binding["profile"] == "ordinary-terminal-v1":
                from .workspace_ordinary import assess

                result = assess(current, observation, source=source)
            else:
                historical = binding["historical"]
                if not isinstance(historical, dict) or set(historical) != {
                    "reviewed_history",
                    "acknowledgements",
                }:
                    raise ValueError("unknown historical workspace authority")
                plan = {
                    "source": source,
                    "activity": {
                        "inventory": binding["inventory"],
                        "deployment_intent_digest": binding["deployment_intent_digest"],
                        **historical,
                    },
                }
                result = require_workspace_quiescent(
                    plan, historical["acknowledgements"], observation=observation
                )
            return {"observation": observation, "assessment": result}

        check()
        yield check
