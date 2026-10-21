"""Global approved-workspace transactions; tagged installer behavior stays separate."""

from pathlib import Path

from . import journal as J
from . import operations as O
from .engine import D, R, T
from .registry import coordination, load_record, namespace, read_json, write_json
from .workspace_activity import activity_context
from .workspace_actions import prepare as prepare_actions, recheck as recheck_actions
from .workspace_cli import existing_owner, owner_path
from .workspace_plan import plan_workspace, require_registered
from .workspace_record import check, load, record_path
from .workspace_source import revalidate_source
from .workspace_validate import revalidate_plan


def _pending(target, state_root):
    for path in (namespace(state_root) / "operations").glob("*/journal.json"):
        row = read_json(path)
        if row.get("target_id") == target["target_id"] and row.get("status") != "COMPLETE":
            raise ValueError("incomplete workspace/installer operation: " + str(path))


def apply_workspace(value, state_root):
    state_root = R.safe_absolute(state_root)
    require_registered(value, state_root)
    target = value["target"]
    with coordination(state_root):
        _pending(target, state_root)
        previous = load(target, state_root)
        if previous:
            check(previous, state_root)
        prepared, resources = revalidate_plan(value, state_root)
        with activity_context(value["activity"], value["source"], state_root) as activity_check:
            directory, path, journal = J.allocate(
                state_root, target["target_id"], "workspace", previous
            )
            journal.update(plan_digest=value["digest"], plan=value)
            write_json(path, journal)
            try:
                cli = value["cli"]
                staged = read_json(owner_path(cli["launcher"]["source"], state_root))
                from .registry import canonical

                owner = dict(staged, destination=cli["destination"], state=cli["launcher"]["state"])
                desired = {
                    cli["destination"]: (Path(cli["launcher"]["source"]).read_bytes(), 0o755),
                    cli["owner_path"]: (canonical(owner) + b"\n", 0o600),
                    **resources,
                }
                preimages = {
                    cli["destination"]: cli["before"]["launcher"],
                    cli["owner_path"]: cli["before"]["owner"],
                    **{name: row["before"] for name, row in value["resources"].items()},
                }
                journal["ancillary_actions"] = prepare_actions(directory, desired, preimages)

                def mutation_guard():
                    assessment = activity_check()
                    recheck_actions(value, journal, preimages)
                    return assessment

                write_json(path, journal)
                # Backups are declared first; upgrade CLI before publishing workspace AL provenance.
                for item in journal["ancillary_actions"][:2]:
                    mutation_guard()
                    J.ensure_directory(Path(item["target_path"]).parent, journal, path)
                    J.execute_ancillary(item, journal, path)
                mutation_guard()
                payload = value["payload"]
                result = O._execute(
                    {"kind": "workspace", "runtime_roots": []},
                    state_root,
                    value["target_before"],
                    prepared,
                    payload["cache"],
                    payload["manifest"],
                    {"identity": value["source"]["identity"]["root"], "subdir": "al"},
                    "pinned",
                    [],
                    value["instruction_reviews"],
                    value["inputs"]["reviews"]["value"],
                    value["inputs"]["adoption"]["value"],
                    False,
                    workspace_guard=mutation_guard,
                )
                journal["al_operation"] = result
                write_json(path, journal)
                for item in journal["ancillary_actions"][2:]:
                    mutation_guard()
                    J.ensure_directory(Path(item["target_path"]).parent, journal, path)
                    J.execute_ancillary(item, journal, path)
                al_row = load_record(state_root, target["target_id"])
                candidate = {
                    "operation": journal["id"],
                    "journal": str(path),
                    "plan_digest": value["digest"],
                    "al_snapshot": al_row["current"],
                    "environment": value["environment"],
                    "cli": {
                        "destination": cli["destination"],
                        "owner": existing_owner(cli["destination"], state_root),
                    },
                    "resources": {name: {"state": T.state(name)} for name in resources},
                }
                for name, resource in value["resources"].items():
                    if candidate["resources"][name]["state"] != resource["after"]:
                        raise ValueError("workspace resource verification failed")
                O.check_current(al_row, state_root)
                from .workspace_environment import verify_environment

                verify_environment(candidate["environment"])
                mutation_guard()
                journal["candidate"] = candidate
                J.checkpoint(journal, path, "COMPLETE")
                snapshot = dict(candidate, journal_sha256=D.sha(path))
                row = {
                    "schema_version": 1,
                    "target": target,
                    "current": snapshot,
                    "previous": previous["current"] if previous else None,
                }
                write_json(record_path(state_root, target), row)
                return {
                    "status": "updated",
                    "plan_digest": value["digest"],
                    "operation": journal["id"],
                }
            except BaseException as exc:
                # Confirm an atomic final publication before classifying an uncertain write.
                current = load(target, state_root)
                if current and current["current"].get("operation") == journal["id"]:
                    check(current, state_root)
                    return {
                        "status": "updated",
                        "operation": journal["id"],
                        "publication": "confirmed after write uncertainty",
                    }
                journal.update(
                    error=type(exc).__name__ + ": " + str(exc),
                    recovery="Inspect exact outer/AL journals and backups; do not retry.",
                )
                J.checkpoint(journal, path, "PENDING")
                raise


def verify_workspace(value, state_root):
    require_registered(value, state_root)
    with coordination(state_root):
        _pending(value["target"], state_root)
        revalidate_source(value["source"])
        with activity_context(value["activity"], value["source"], state_root) as activity_check:
            row = load(value["target"], state_root)
            if not row or row["current"]["plan_digest"] != value["digest"]:
                raise ValueError("workspace plan is not the current completed installation")
            current = check(row, state_root)
            activity_check()
            return {
                "status": "verified",
                "operation": current["operation"],
                "plan_digest": value["digest"],
            }
