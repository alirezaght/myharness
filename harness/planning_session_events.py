"""Transaction-local canonical event collection; never a process-global suppression flag."""

from contextlib import contextmanager
from contextvars import ContextVar

SCOPES = ContextVar("harness_session_mutation_scopes", default=())
BROWSER_TASK = ContextVar("harness_session_browser_task", default=None)
LEGACY_TASK = ContextVar("harness_session_legacy_task", default=None)


@contextmanager
def mutation_scope(conn, task_id, origin="browser", *, aggregate=True):
    scope = {
        "connection": id(conn),
        "task": task_id,
        "origin": origin,
        "aggregate": aggregate,
        "events": [],
    }
    token = SCOPES.set((*SCOPES.get(), scope))
    try:
        yield scope
    finally:
        SCOPES.reset(token)


def current_scope(conn, task_id):
    scope = next(
        (s for s in reversed(SCOPES.get()) if s["connection"] == id(conn) and s["task"] == task_id),
        None,
    )
    if scope is None and BROWSER_TASK.get() == task_id:
        return {
            "connection": id(conn),
            "task": task_id,
            "origin": "browser",
            "aggregate": False,
            "events": [],
        }
    return scope


def write_allowed(conn, task_id):
    return int(current_scope(conn, task_id) is not None)


def workspace_event(conn, task_id, event):
    from harness.planning_session_store import PlanningSessionStore, schema_available

    if not schema_available(conn):
        return
    scope = current_scope(conn, task_id)
    if scope and scope["aggregate"]:
        scope["events"].append({"id": event.id, "kind": event.kind})
        return
    store = PlanningSessionStore(conn, task_id)
    if store.owner():
        store.append_event(
            event.kind,
            {"workspace_event": event.id, "payload": event.payload},
            origin=scope["origin"] if scope else "legacy_cli",
            canonical_reference="workspace:" + str(event.id),
        )


def guard_legacy_transaction(conn):
    from harness.planning_session_contract import SessionError
    from harness.planning_session_store import PlanningSessionStore, schema_available

    task_id = LEGACY_TASK.get()
    if task_id and schema_available(conn):
        owner = PlanningSessionStore(conn, task_id).owner()
        if owner and owner["mode"] == "attached":
            raise SessionError("SESSION_OPERATION_INCOMPATIBLE")


def native_binding(conn, task_id):
    from harness.planning_session_store import PlanningSessionStore, schema_available

    if not schema_available(conn):
        return None
    owner = PlanningSessionStore(conn, task_id).owner()
    return {"mode": owner["mode"], "epoch": owner["epoch"]} if owner else None


def require_standalone(conn, task_id, captured=None, *, check_captured=False):
    from harness.planning_session_contract import SessionError

    current = native_binding(conn, task_id)
    if (current and current["mode"] != "standalone") or (check_captured and current != captured):
        raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
    return current
