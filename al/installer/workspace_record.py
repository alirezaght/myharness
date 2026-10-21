"""Coordinated workspace receipts retain AL and Harness ownership independently."""

from . import journal as J
from . import operations as O
from .engine import R, T
from .registry import completed, load_record, namespace, read_json
from .workspace_cli import existing_owner
from .workspace_environment import verify_environment


def record_path(state_root, target):
    from .registry import record_path as al_record_path

    # Reuse the existing strict identity grammar without sharing its record namespace.
    key = al_record_path(state_root, target["target_id"]).name
    return namespace(state_root) / "workspace-records" / key


def load(target, state_root):
    path = record_path(state_root, target)
    if not path.exists():
        return None
    value = read_json(path)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "target", "current", "previous"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["target"] != target
    ):
        raise ValueError("invalid coordinated workspace record")
    return value


def check(value, state_root):
    current = value["current"]
    journal = completed(current)
    J.validate_backups(journal)
    al_row = load_record(state_root, value["target"]["target_id"])
    if not al_row or al_row.get("current") != current["al_snapshot"]:
        raise ValueError("coordinated AL snapshot differs")
    O.check_current(al_row, state_root)
    verify_environment(current["environment"])
    if existing_owner(current["cli"]["destination"], state_root) != current["cli"]["owner"]:
        raise ValueError("coordinated global CLI drift")
    for name, resource in current["resources"].items():
        if T.state(R.safe_absolute(name)) != resource["state"]:
            raise ValueError("coordinated Harness resource drift: " + name)
    return current
