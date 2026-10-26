"""Read-only concise completion facts; AL and provider evidence stay distinct."""

from harness.execution_store import entities, get_execution


def assemble_report(conn, execution_id, delivery):
    execution = get_execution(conn, execution_id)
    tasks = entities(conn, execution_id, "task")
    tests = entities(conn, execution_id, "test")
    blockers = entities(conn, execution_id, "blocker")
    amendments = entities(conn, execution_id, "amendment")
    browser = delivery.get("browser_validation", {})
    return {
        "schema_version": 1,
        "execution_id": execution_id,
        "task_id": execution["task_id"],
        "implementation": {
            "status": execution["status"],
            "result": execution["result"] or {"status": "unavailable"},
        },
        "completed_tasks": [t for t in tasks if t.get("status") == "Completed"],
        "tasks": tasks,
        "al_tests": {"status": "recorded" if tests else "unavailable", "records": tests},
        "merge_request": delivery.get("mr") or {"status": "unavailable"},
        "ci": {
            "pipeline": delivery.get("pipeline"),
            "jobs": delivery.get("jobs", []),
            "tests": delivery.get("test_report")
            or {"status": "unavailable", "counts_available": False},
        },
        "staging": delivery.get("staging", {"status": "unavailable", "ready": False}),
        "browser": browser,
        "blockers": blockers + delivery.get("blockers", []) + browser.get("blockers", []),
        "deviations_requiring_approval": amendments,
        "live_acceptance": "outstanding"
        if browser.get("currency") != "current"
        or browser.get("status") != "Completed"
        or browser.get("evidence_origin") != ["real_browser"]
        else "browser_observed_as_of_timestamp",
        "limitations": [
            "A local fixture demonstration does not establish live release acceptance",
            "Detailed AL verification remains in its recorded artifacts",
        ],
    }


def completion_report(conn, execution_id):
    from harness.browser_store import browser_view
    from harness.delivery_store import delivery_view

    try:
        delivery = delivery_view(conn, execution_id)
    except LookupError:
        delivery = {
            "browser_validation": browser_view(conn, execution_id),
            "blockers": ["Delivery unavailable"],
        }
    return assemble_report(conn, execution_id, delivery)
