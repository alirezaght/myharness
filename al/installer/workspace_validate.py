"""Reconstruct current prepared inputs before any workspace mutation."""

from pathlib import Path

from . import operations as O
from .workspace_cli import existing_owner, owner_path
from .workspace_environment import verify_environment
from .workspace_inputs import artifact, identity
from .workspace_payload import build_payload, verify_payload
from .workspace_plan import prepare_target, require_registered
from .workspace_resources import global_resources, prepare_resources
from .workspace_source import revalidate_source
from .workspace_wheel import inspect_wheel
from .validator_origin import capture, verify_snapshot
from .registry import canonical, read_json


def revalidate_plan(value, state_root):
    require_registered(value, state_root)
    inputs = value["inputs"]
    for key in ("reviews", "adoption"):
        if artifact(inputs[key]["reference"]) != inputs[key]:
            raise ValueError("workspace review artifact changed")
    contents = revalidate_source(value["source"])
    payload = value["payload"]
    current = verify_payload(payload["cache"], payload["manifest"]["digest"])
    if current != build_payload(value["source"], contents) or current != payload["manifest"]:
        raise ValueError("workspace approved payload binding changed")
    row, reviewed, prepared = prepare_target(
        value["target"], payload["cache"], current, state_root, inputs
    )
    if (
        row != value["target_before"]
        or reviewed != value["instruction_reviews"]
        or identity(prepared) != value["prepared_identity"]
    ):
        raise ValueError("workspace ownership, instructions or preparation changed")
    wheel = value["wheel"]
    artifact_value = wheel["wheel"]
    if wheel["source_digest"] != value["source"]["digest"] or inspect_wheel(
        Path(artifact_value["path"]), artifact_value["version"]
    ) != {k: v for k, v in artifact_value.items() if k != "path"}:
        raise ValueError("prepared Harness wheel binding changed")
    environment = verify_environment(value["environment"])
    if environment["wheel_digest"] != wheel["digest"]:
        raise ValueError("prepared Harness environment source changed")
    cli = value["cli"]
    verify_snapshot(cli["snapshot"], cli["validator_binding"])
    _, state = capture(cli["launcher"]["source"])
    if (
        state != cli["launcher"]["state"]
        or existing_owner(cli["destination"], state_root, inputs["adoption"]["value"])
        != cli["before"]
    ):
        raise ValueError("prepared CLI launcher or ownership changed")
    staged_owner = read_json(owner_path(cli["launcher"]["source"], state_root))
    desired_owner = dict(staged_owner, destination=cli["destination"], state=state)
    owner_bytes = canonical(desired_owner) + b"\n"
    if (
        identity(owner_bytes) != cli["owner_bytes"]
        or desired_owner["snapshot"] != cli["snapshot"]
        or desired_owner["validator_binding"] != cli["validator_binding"]
    ):
        raise ValueError("prepared CLI metadata changed")
    from .workspace_record import check as check_workspace, load as load_workspace

    previous = load_workspace(value["target"], state_root)
    if previous != value["workspace_before"]:
        raise ValueError("coordinated workspace ownership changed")
    if previous:
        check_workspace(previous, state_root)
    resources = global_resources(contents, inputs["home"], environment["directory"])
    if (
        prepare_resources(
            resources,
            value["target"],
            inputs["reviews"]["value"],
            inputs["adoption"]["value"],
            previous=previous["current"]["resources"] if previous else None,
        )
        != value["resources"]
    ):
        raise ValueError("prepared Harness host resources changed")
    O._recheck(prepared)
    return prepared, resources
