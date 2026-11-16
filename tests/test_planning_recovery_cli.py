"""Planning recovery CLI preserves the exact approved preview binding."""

import argparse
import io
import json
from contextlib import redirect_stdout

import pytest

from harness.cli_execution import _validate_planning_request, add_execution_parser


class _Controller:
    conn = object()


class _TransitionConnection:
    def __init__(self, request):
        self.request = request

    def execute(self, _sql, _params):
        request = self.request

        class Result:
            def fetchone(self):
                return {"request": json.dumps(request)}

        return Result()


def _args(*arguments):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_execution_parser(subparsers)
    return parser.parse_args(["execution", *arguments])


def test_cli_start_passes_original_preview_and_epoch_to_recovery(monkeypatch, capsys):
    import harness.cli_execution as cli_execution
    import harness.execution_planning_recovery as recovery

    class Context:
        def __enter__(self):
            return _Controller()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli_execution, "_controller", lambda _args: Context())
    monkeypatch.setattr(cli_execution, "get_execution", lambda _conn, _eid: {"task_id": "t-one"})
    observed = {}

    def start(_controller, execution_id, **kwargs):
        observed.update(execution=execution_id, **kwargs)
        return {"state": "ACTIVE", "fresh_dispatch": {"status": "reserved"}}

    monkeypatch.setattr(recovery, "start_planning", start)
    args = _args(
        "planning-start",
        "t-one",
        "e-one",
        "--expected-epoch",
        "epoch-one",
        "--expected-authority-generation",
        "8",
        "--request-id",
        "request-one",
        "--preview-sha256",
        "sha256:original",
        "--confirm",
        "Start new planning",
    )
    output = io.StringIO()
    with redirect_stdout(output):
        assert args.handler(args) == 0
    assert observed == {
        "execution": "e-one",
        "request_id": "request-one",
        "preview_sha256": "sha256:original",
        "expected_epoch_id": "epoch-one",
        "expected_authority_generation": 8,
    }
    assert '"fresh_dispatch"' in output.getvalue()


def test_cli_rejects_wrong_task_and_nonexact_confirmation(monkeypatch):
    import harness.cli_execution as cli_execution
    import harness.execution_planning_recovery as recovery

    class Context:
        def __enter__(self):
            return _Controller()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli_execution, "_controller", lambda _args: Context())
    monkeypatch.setattr(cli_execution, "get_execution", lambda _conn, _eid: {"task_id": "t-other"})
    args = _args(
        "planning-start",
        "t-one",
        "e-one",
        "--expected-epoch",
        "ep",
        "--expected-authority-generation",
        "1",
        "--request-id",
        "r",
        "--preview-sha256",
        "sha256:x",
        "--confirm",
        "Start planning",
    )
    with pytest.raises(ValueError, match="different harness task"):
        args.handler(args)

    monkeypatch.setattr(cli_execution, "get_execution", lambda _conn, _eid: {"task_id": "t-one"})
    monkeypatch.setattr(
        recovery,
        "start_planning",
        lambda *_a, **_kw: pytest.fail("started without exact confirmation"),
    )
    with pytest.raises(ValueError, match="exactly equal"):
        args.handler(args)


def test_cli_inspect_and_finish_require_the_original_epoch_and_preview():
    request = {
        "transition_mode": "fresh-planning-missing-state",
        "preview_sha256": "sha256:original",
        "expected_epoch_id": "epoch-original",
        "expected_authority_generation": 4,
    }
    args = argparse.Namespace(
        execution_id="e-one",
        request_id="r-one",
        preview_sha256="sha256:original",
        expected_epoch="epoch-original",
        expected_authority_generation=4,
    )
    controller = _Controller()
    controller.conn = _TransitionConnection(request)
    _validate_planning_request(controller, args)
    args.expected_epoch = "epoch-other"
    with pytest.raises(ValueError, match="another preview or epoch"):
        _validate_planning_request(controller, args)
