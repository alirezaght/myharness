"""A failed native startup can retry only with independently retained no-spawn proof."""

from pathlib import Path

from harness.execution_models import ExecutionConflict


def startup_disposition(controller, execution):
    from harness.execution_epochs import get_epoch
    from harness.execution_recovery_settlement import _not_started

    conn = controller.conn
    attempts = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=? ORDER BY rowid",
            (execution["id"],),
        )
    ]
    result = {
        "state": "unknown",
        "retry_eligible": False,
        "diagnostic": (
            "Original startup diagnostic unavailable; inspect current readiness separately."
        ),
    }
    logs = conn.execute(
        "SELECT text FROM execution_logs WHERE execution_id=? AND epoch_id=? "
        "AND text LIKE 'Startup failed before native spawn:%' ORDER BY rowid DESC LIMIT 1",
        (execution["id"], execution["epoch_id"]),
    ).fetchone()
    if logs:
        result["diagnostic"] = logs["text"]
    if execution["native_id"] or any(
        a["child_pid"] is not None or a["native_id"] for a in attempts
    ):
        return result | {"state": "native-recorded"}
    if not attempts:
        return result
    qualifying = False
    try:
        for attempt in attempts:
            epoch = get_epoch(conn, execution["id"], attempt["epoch_id"])
            original = dict(
                execution,
                handoff_path=epoch["identity"]["handoff_path"],
                epoch_id=epoch["epoch_id"],
                authority_generation=epoch["authority_generation"],
            )
            if attempt["status"] not in {
                "PreSpawnFailed",
                "Exited",
                "Reconciled",
            } or not _not_started(original, attempt):
                return result
            if (
                attempt["mode"] != "reconnect"
                and attempt["epoch_id"] == execution["epoch_id"]
                and _not_started(original, attempt, required_stage="native-setup")
            ):
                qualifying = True
    except (OSError, ValueError, KeyError):
        return result
    return result | {
        "state": "proven-not-started",
        "retry_eligible": qualifying
        and execution["status"] in {"Unavailable", "RecoveryRequired", "Interrupted"},
    }


def require_startup_retry(controller, execution, envelope):
    from harness.execution_initialization import initialization_identity
    from harness.execution_recovery_settlement import observe_recovery_settlement
    from harness.launch_readiness import require_launch_ready

    if not startup_disposition(controller, execution)["retry_eligible"]:
        raise ExecutionConflict(
            "startup retry requires authentic no-native-start evidence for every attempt; "
            "inspect recorded status"
        )
    observation = observe_recovery_settlement(controller, execution)
    if observation["obligations"]:
        raise ExecutionConflict("startup attempts are not independently settled")
    if not execution["al_run_id"]:
        raise ExecutionConflict("startup retry requires the existing initialized AL run")
    identity = initialization_identity(execution, envelope)
    if identity["al_run_id"] != execution["al_run_id"]:
        raise ExecutionConflict("startup retry AL run binding changed")
    from harness.native_tool_policy import recorded_override

    require_launch_ready(
        envelope["repository"],
        execution["host"],
        accept_unverified_host_version=recorded_override(Path(execution["handoff_path"]).parent),
    )
    from harness.execution_contract import loads
    from harness.execution_hosts import installed_package
    from harness.execution_worker import package_for_native

    package = package_for_native(
        execution,
        envelope,
        installed_package(execution["host"], envelope["repository"]),
        initialization=identity,
    )
    previous = controller.conn.execute(
        "SELECT package FROM execution_attempts WHERE execution_id=? AND epoch_id=? "
        "AND package IS NOT NULL ORDER BY rowid DESC LIMIT 1",
        (execution["id"], execution["epoch_id"]),
    ).fetchone()
    if previous and loads(previous["package"])["sha256"] != package["sha256"]:
        raise ExecutionConflict("startup retry selected AL package changed")
