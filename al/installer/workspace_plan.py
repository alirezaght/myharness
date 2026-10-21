"""Fresh approved-source planning; staging never constitutes global installation."""

from pathlib import Path
import tempfile

from . import operations as O
from .engine import R
from .guards import check_instructions
from .registry import (
    canonical,
    coordination,
    digest,
    load_record,
    namespace,
    read_json,
    records,
    reject_pending,
    write_json,
)
from .smoke import smoke_release
from .targets import resolve_target, validate_target
from .workspace_activity import activity_context, plan_activity
from .workspace_cli import existing_owner, prepare_cli
from .workspace_environment import create_environment
from .workspace_inputs import identity, request, sealed
from .workspace_payload import cache_payload
from .workspace_resources import global_resources, prepare_resources
from .workspace_source import capture_source, revalidate_source
from .workspace_wheel import build_wheel

FIELDS = {
    "schema_version",
    "kind",
    "state_root",
    "source",
    "inputs",
    "intent",
    "target",
    "activity",
    "payload",
    "target_before",
    "workspace_before",
    "instruction_reviews",
    "prepared_identity",
    "wheel",
    "environment",
    "cli",
    "resources",
    "preparation",
    "digest",
}


def prepare_target(target, cache, payload, state_root, inputs):
    validate_target(target, state_root, payload["source_identity"])
    O.check_target_overlap(target, records(state_root))
    row = load_record(state_root, target["target_id"])
    if row and row["target"] != target:
        raise ValueError("registered workspace target identity differs")
    row = row or {
        "schema_version": 1,
        "target_id": target["target_id"],
        "target": target,
        "policy": "pinned",
        "source": None,
        "current": None,
        "previous": None,
        "pending": None,
        "runtime_roots": [],
    }
    reject_pending(row, state_root)
    O.check_current(row, state_root)
    enrolled = row.get("source")
    if enrolled and enrolled != {"identity": payload["source_identity"], "subdir": "al"}:
        raise ValueError("workspace cannot change enrolled source identity")
    reviews, adoption = inputs["reviews"]["value"], inputs["adoption"]["value"]
    reviewed = check_instructions(target, reviews)
    prepared = O._prepare(
        target, cache, payload, dict(row, _state_root=str(state_root)), "pinned", reviews, adoption
    )
    smoke_release(cache, payload, target["platforms"])
    return row, reviewed, prepared


def plan_path(state_root, value):
    key = value.get("digest") if isinstance(value, dict) else None
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("invalid workspace plan identity")
    return namespace(state_root) / "workspace-plans" / (key + ".json")


def require_registered(value, state_root):
    if (
        not isinstance(value, dict)
        or set(value) != FIELDS
        or value.get("kind") != "approved-workspace-global"
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("digest")
        != digest(canonical({k: v for k, v in value.items() if k != "digest"}))
        or value.get("state_root") != str(R.safe_absolute(state_root))
    ):
        raise ValueError("invalid workspace plan")
    path = plan_path(state_root, value)
    if not path.exists() or read_json(path) != value or path.stat().st_mode & 0o777 != 0o600:
        raise ValueError("workspace plan is not the immutable registered preparation")
    return value


def plan_workspace(source, options, state_root):
    inputs = request(options)
    state_root = R.safe_absolute(state_root)
    target = resolve_target(home=inputs["home"], platforms=("codex", "claude"))
    validate_target(target, state_root, source)
    with coordination(state_root):
        source, contents = capture_source(
            source, session=inputs["session"], run=inputs["run"], platform=inputs["platform"]
        )
        intent = sealed(
            {
                "schema_version": 1,
                "kind": "workspace-deployment-intent-v1",
                "source": source["digest"],
                "target": target,
                "state_root": str(state_root),
                "inputs": inputs,
            }
        )
        # Complete scope and source authority are required before any expensive build.
        activity = plan_activity(
            source,
            inputs["home"],
            state_root,
            deployment_intent_digest=intent["digest"],
            explicit=inputs["runtime_roots"],
            historical=inputs["historical"],
            orphan_review=inputs["orphan_review"],
        )
        with activity_context(activity, source, state_root) as check:
            parent = namespace(state_root) / "workspace-preparations"
            parent.mkdir(parents=True, exist_ok=True)
            for old in parent.glob("*/operation.json"):
                if read_json(old).get("status") != "COMPLETE":
                    raise ValueError("unresolved workspace preparation: " + str(old))
            directory = Path(tempfile.mkdtemp(prefix="prepare-", dir=parent))
            journal = directory / "operation.json"
            write_json(journal, {"status": "PREPARING", "intent": intent["digest"]})
            try:
                cache, payload = cache_payload(source, state_root)
                row, reviewed, prepared = prepare_target(target, cache, payload, state_root, inputs)
                wheel = build_wheel(source, state_root)
                environment = create_environment(wheel, directory / "harness")
                check()
                cli = prepare_cli(
                    Path(source["identity"]["root"]) / "al",
                    inputs["home"],
                    state_root,
                    directory / "cli",
                    adoption=inputs["adoption"]["value"],
                )
                resources = global_resources(contents, inputs["home"], environment["directory"])
                from .workspace_record import check as check_workspace, load as load_workspace

                previous = load_workspace(target, state_root)
                if previous:
                    check_workspace(previous, state_root)
                hosts = prepare_resources(
                    resources,
                    target,
                    inputs["reviews"]["value"],
                    inputs["adoption"]["value"],
                    previous=previous["current"]["resources"] if previous else None,
                )
                revalidate_source(source)
                check()
                O._recheck(prepared)
                if (
                    existing_owner(cli["destination"], state_root, inputs["adoption"]["value"])
                    != cli["before"]
                ):
                    raise ValueError("global CLI changed during workspace planning")
                value = sealed(
                    {
                        "schema_version": 1,
                        "kind": "approved-workspace-global",
                        "state_root": str(state_root),
                        "source": source,
                        "inputs": inputs,
                        "intent": intent,
                        "target": target,
                        "activity": activity,
                        "payload": {"cache": str(cache), "manifest": payload},
                        "target_before": row,
                        "workspace_before": previous,
                        "instruction_reviews": reviewed,
                        "prepared_identity": identity(prepared),
                        "wheel": wheel,
                        "environment": environment,
                        "cli": identity(cli),
                        "resources": hosts,
                        "preparation": str(directory),
                    }
                )
                path = plan_path(state_root, value)
                if path.exists():
                    raise ValueError("workspace immutable plan collision")
                write_json(path, value)
                write_json(
                    journal,
                    {
                        "status": "COMPLETE",
                        "intent": intent["digest"],
                        "plan": value["digest"],
                        "path": str(path),
                    },
                )
                return value
            except BaseException as exc:
                write_json(
                    journal,
                    {
                        "status": "PENDING",
                        "intent": intent["digest"],
                        "error": type(exc).__name__ + ": " + str(exc),
                    },
                )
                raise
