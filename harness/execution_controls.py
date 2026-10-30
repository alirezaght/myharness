"""Explicit dashboard recovery reuses supported guarded bridge and resume paths."""

from harness.execution_epoch_lock import epoch_action_lock
from harness.execution_epochs import require_active_epoch
from harness.execution_store import get_execution


def recover_and_continue(controller, execution_id, *, action, **epoch):
    if action not in {"recover", "retry-checks"}:
        raise ValueError("unsupported execution recovery action")
    execution = get_execution(controller.conn, execution_id)
    with epoch_action_lock(execution, exclusive=True):
        current = require_active_epoch(controller.conn, execution_id, mutation=True, **epoch)
        if current["status"] in {"Completed", "Failed"}:
            raise ValueError("terminal execution cannot be continued")
        from harness.execution_bridge import upgrade_bridge
        from harness.execution_recovery import resume_execution
        from harness.execution_settlement import settle_exited_work

        settlement = settle_exited_work(controller, execution_id)
        bridge = upgrade_bridge(
            controller,
            execution_id,
            reason=f"Dashboard request: {action} for the existing approved execution.",
        )
        controller.workspace(current["task_id"]).record_event(
            "execution.control_requested",
            {
                "execution_id": execution_id,
                "epoch_id": current["epoch_id"],
                "action": action,
                "bridge_status": bridge["status"],
                "settlement": settlement,
            },
        )
        result = resume_execution(
            controller,
            execution_id,
            focus="tooling" if action == "recover" else "verification",
            **epoch,
        )
        return result | {
            "control_result": {
                "requested_action": action,
                "bridge_status": bridge["status"],
                "continuation_requested": True,
            }
        }
