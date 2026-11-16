"""Dashboard restoration is preview-bound and bypasses generic run readiness only."""

import json
from contextlib import contextmanager

import pytest

from harness.dashboard.knowledge_view import (
    spec_restoration_action,
    spec_restoration_preview,
)


class _Controller:
    conn = object()


def _body(**values):
    return json.dumps(values).encode()


def test_preview_requires_same_task_and_forwards_current_epoch(monkeypatch, tmp_path):
    import harness.dashboard.knowledge_view as view
    import harness.execution_spec_recovery as recovery

    @contextmanager
    def state(_db_path):
        yield _Controller()

    monkeypatch.setattr(view, "open_state", state)
    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t-one"})
    observed = {}

    def preview(_controller, execution_id, **kwargs):
        observed.update(execution=execution_id, **kwargs)
        return {"preview_sha256": "sha256:preview", "preview": {"readiness": "ready"}}

    monkeypatch.setattr(recovery, "preview_spec_restoration", preview)
    db_path = tmp_path / "state.db"
    db_path.touch()
    result = spec_restoration_preview(db_path, "t-one", "e-one", "epoch-1", 7)
    assert result["preview_sha256"] == "sha256:preview"
    assert observed == {
        "execution": "e-one",
        "expected_epoch_id": "epoch-1",
        "expected_authority_generation": 7,
    }
    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t-other"})
    with pytest.raises(LookupError, match="not found for task"):
        spec_restoration_preview(db_path, "t-one", "e-one", "epoch-1", 7)


def test_restore_uses_exact_confirmation_hash_and_epoch(monkeypatch, tmp_path):
    import harness.dashboard.knowledge_view as view
    import harness.execution_spec_recovery as recovery

    @contextmanager
    def state(_db_path):
        yield _Controller()

    monkeypatch.setattr(view, "open_state", state)
    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t-one"})
    observed = {}

    def restore(_controller, execution_id, **kwargs):
        observed.update(execution=execution_id, **kwargs)
        return {
            "state": "already-present",
            "restored": False,
            "authority_revalidation": {
                "status": "unavailable",
                "diagnostic": "decision provenance incomplete",
            },
            "implementation_authority_granted": False,
        }

    monkeypatch.setattr(recovery, "restore_specification", restore)
    db_path = tmp_path / "state.db"
    db_path.touch()
    status, result = spec_restoration_action(
        db_path,
        _body(
            task="t-one",
            execution="e-one",
            epoch_id="epoch-1",
            authority_generation=7,
            request_id="request-1",
            preview_sha256="sha256:preview",
            confirmation="Restore approved specification",
        ),
    )
    assert status == 200 and result["state"] == "already-present"
    assert result["authority_revalidation"]["status"] == "unavailable"
    assert not result["implementation_authority_granted"]
    assert observed == {
        "execution": "e-one",
        "request_id": "request-1",
        "preview_sha256": "sha256:preview",
        "expected_epoch_id": "epoch-1",
        "expected_authority_generation": 7,
    }


def test_restore_rejects_confirmation_unknown_fields_and_wrong_task(monkeypatch, tmp_path):
    import harness.dashboard.knowledge_view as view
    import harness.execution_spec_recovery as recovery

    base = {
        "task": "t-one",
        "execution": "e-one",
        "epoch_id": "epoch-1",
        "authority_generation": 7,
        "request_id": "request-1",
        "preview_sha256": "sha256:preview",
        "confirmation": "Restore specification",
    }
    status, error = spec_restoration_action("/missing.db", _body(**base))
    assert status == 409 and "exactly equal" in error["error"]
    base["confirmation"] = "Restore approved specification"
    base["project_id"] = "forged"
    status, error = spec_restoration_action("/missing.db", _body(**base))
    assert status == 409 and "unexpected" in error["error"]
    del base["project_id"]

    @contextmanager
    def state(_db_path):
        yield _Controller()

    monkeypatch.setattr(view, "open_state", state)
    monkeypatch.setattr(view, "get_execution", lambda _conn, _eid: {"task_id": "t-other"})
    monkeypatch.setattr(
        recovery,
        "restore_specification",
        lambda *_args, **_kwargs: pytest.fail("restored against a stale epoch"),
    )
    db_path = tmp_path / "state.db"
    db_path.touch()
    status, error = spec_restoration_action(db_path, _body(**base))
    assert status == 404 and "not found for task" in error["error"]
