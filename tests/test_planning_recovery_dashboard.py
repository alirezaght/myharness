"""Dashboard recovery stays same-task and binds the original preview request."""

import json
from contextlib import contextmanager

import pytest

from harness.dashboard.knowledge_view import planning_action


class _Controller:
    conn = object()


def _body(**values):
    return json.dumps(values).encode()


def test_dashboard_requires_exact_confirmation_before_start():
    status, error = planning_action(
        "/missing.db",
        _body(
            task="t-one",
            execution="e-one",
            epoch_id="ep",
            authority_generation=1,
            request_id="r",
            preview_sha256="sha256:x",
            confirmation="Start planning",
        ),
        "planning-start",
    )
    assert status == 409 and "exactly equal" in error["error"]


def test_dashboard_start_is_same_task_and_retains_preview_digest(monkeypatch, tmp_path):
    import harness.dashboard.knowledge_view as view
    import harness.execution_planning_recovery as recovery

    @contextmanager
    def state(_db_path):
        yield _Controller()

    monkeypatch.setattr(view, "open_state", state)
    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t-one"})
    observed = {}

    def start(_controller, execution_id, **kwargs):
        observed.update(execution=execution_id, **kwargs)
        return {"state": "ACTIVE", "fresh_dispatch": {"status": "reserved"}}

    monkeypatch.setattr(recovery, "start_planning", start)
    db_path = tmp_path / "state.db"
    db_path.touch()
    status, result = planning_action(
        db_path,
        _body(
            task="t-one",
            execution="e-one",
            epoch_id="ep-original",
            authority_generation=3,
            request_id="request-original",
            preview_sha256="sha256:original",
            confirmation="Start new planning",
        ),
        "planning-start",
    )
    assert status == 200 and result["state"] == "ACTIVE"
    assert observed == {
        "execution": "e-one",
        "request_id": "request-original",
        "preview_sha256": "sha256:original",
        "expected_epoch_id": "ep-original",
        "expected_authority_generation": 3,
    }

    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t-other"})
    status, error = planning_action(
        db_path,
        _body(
            task="t-one",
            execution="e-one",
            epoch_id="ep-original",
            authority_generation=3,
            request_id="request-original",
            preview_sha256="sha256:original",
            confirmation="Start new planning",
        ),
        "planning-start",
    )
    assert status == 404 and "not found for task" in error["error"]


def test_dashboard_refuses_unknown_fields_and_stale_preview(monkeypatch, tmp_path):
    import harness.dashboard.knowledge_view as view
    import harness.execution_planning_recovery as recovery
    from harness.execution_models import ExecutionConflict

    unexpected = planning_action(
        "/state.db",
        _body(
            task="t",
            execution="e",
            epoch_id="ep",
            authority_generation=0,
            request_id="r",
            preview_sha256="sha256:x",
            confirmation="Start new planning",
            project_id="forged",
        ),
        "planning-start",
    )
    assert unexpected[0] == 409

    @contextmanager
    def state(_db_path):
        yield _Controller()

    monkeypatch.setattr(view, "open_state", state)
    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t"})
    monkeypatch.setattr(
        recovery,
        "start_planning",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            ExecutionConflict("stale planning preview; inspect current recovery inputs")
        ),
    )
    db_path = tmp_path / "state.db"
    db_path.touch()
    status, error = planning_action(
        db_path,
        _body(
            task="t",
            execution="e",
            epoch_id="ep",
            authority_generation=0,
            request_id="r",
            preview_sha256="sha256:x",
            confirmation="Start new planning",
        ),
        "planning-start",
    )
    assert status == 409 and "stale planning preview" in error["error"]


def test_dashboard_finish_rejects_a_different_original_epoch(monkeypatch, tmp_path):
    import harness.dashboard.knowledge_view as view
    import harness.execution_planning_recovery as recovery

    class Connection:
        def execute(self, _sql, _params):
            class Result:
                def fetchone(self):
                    return {
                        "request": json.dumps(
                            {
                                "transition_mode": "fresh-planning-missing-state",
                                "preview_sha256": "sha256:original",
                                "expected_epoch_id": "epoch-original",
                                "expected_authority_generation": 4,
                            }
                        )
                    }

            return Result()

    class Controller:
        conn = Connection()

    @contextmanager
    def state(_db_path):
        yield Controller()

    monkeypatch.setattr(view, "open_state", state)
    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t"})
    monkeypatch.setattr(
        recovery,
        "finish_planning",
        lambda *_args, **_kwargs: pytest.fail("finished against a different epoch"),
    )
    db_path = tmp_path / "state.db"
    db_path.touch()
    status, error = planning_action(
        db_path,
        _body(
            task="t",
            execution="e",
            epoch_id="epoch-other",
            authority_generation=4,
            request_id="r",
            preview_sha256="sha256:original",
        ),
        "planning-finish",
    )
    assert status == 409 and "another preview or epoch" in error["error"]
