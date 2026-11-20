"""Observe the authenticated agent's registration without admitting writer authority."""


def registered_assignment(state, execution, principal):
    integration = state.get("integration", {})
    result = {
        "status": "not_registered",
        "harness_task_id": execution["task_id"],
        "execution_id": execution["id"],
        "epoch_id": execution["epoch_id"],
        "graph_revision": integration.get("graph_revision"),
    }
    agent = integration.get("agents", {}).get(principal["thread_id"])
    if not agent:
        return result
    result.update(agent_id=principal["thread_id"], agent_status=agent.get("status"))
    task = integration.get("tasks", {}).get(agent.get("task"))
    if not task:
        return dict(result, status="unavailable")
    return dict(
        result,
        status="available",
        al_task_id=agent["task"],
        task_status=task["status"],
        task_revision=task["revision"],
        files=list(task["files"]),
    )
