"""Server-bound current-task derived draft policy, never caller-selected exclusions."""

import json

from harness.authoring_contract import canonical, digest


def stored(conn, task_id):
    if not conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='investigation_artifact_policies'"
    ).fetchone():
        return None
    row = conn.execute(
        "SELECT policy_json FROM investigation_artifact_policies WHERE task_id=?", (task_id,)
    ).fetchone()
    return json.loads(row[0]) if row else None


def derive(service):
    draft = service.manager().store.draft_path.absolute()
    staged = draft.with_name("." + draft.name + ".tmp")
    paths = [
        path.relative_to(service.root).as_posix()
        for path in (draft, staged)
        if path.is_relative_to(service.root)
    ]
    value = {
        "schema_version": 1,
        "task": service.task_id,
        "project": service.project,
        "repository": str(service.root),
        "specs_root": str(draft.parent.parent),
        "excluded_paths": paths,
    }
    return value | {"digest": digest(value)}


def bind(service):
    policy = derive(service)
    if not service.conn.in_transaction:
        raise RuntimeError("managed artifact binding requires task transaction")
    if stored(service.conn, service.task_id) == policy:
        return policy
    service.conn.execute(
        (
            "INSERT INTO investigation_artifact_policies VALUES(?,?,?) ON "
            "CONFLICT(task_id) DO UPDATE SET "
            "project=excluded.project,policy_json=excluded.policy_json"
        ),
        (service.task_id, service.project, canonical(policy)),
    )
    return policy


def require_policy(service, captured):
    current = derive(service)
    if captured != current or stored(service.conn, service.task_id) != current:
        raise ValueError(
            "managed artifact configuration changed; obtain fresh repository observations"
        )
    return current


def reject_source(policy, path):
    from harness.execution_files import relative_path

    normalized = str(relative_path(path))
    if policy and normalized in policy["excluded_paths"]:
        raise ValueError(
            "current task managed draft is authoring context, not independent source evidence"
        )
