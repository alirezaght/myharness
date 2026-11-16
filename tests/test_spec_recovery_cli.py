"""The explicit CLI restoration path uses only the retained preview authority."""

import argparse
import io
from contextlib import redirect_stdout

import pytest

from harness.cli_execution import add_execution_parser


class _Controller:
    conn = object()


def _args(*arguments):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_execution_parser(subparsers)
    return parser.parse_args(["execution", *arguments])


def test_cli_preview_and_restore_bind_task_epoch_request_and_hash(monkeypatch):
    import harness.cli_execution as cli_execution
    import harness.execution_spec_recovery as recovery

    class Context:
        def __enter__(self):
            return _Controller()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli_execution, "_controller", lambda _args: Context())
    monkeypatch.setattr(cli_execution, "get_execution", lambda _conn, _eid: {"task_id": "t-one"})
    observed = {}

    def preview(_controller, execution_id, **kwargs):
        observed.update(operation="preview", execution=execution_id, **kwargs)
        return {"preview_sha256": "sha256:preview", "preview": {"readiness": "ready"}}

    def restore(_controller, execution_id, **kwargs):
        observed.update(operation="restore", execution=execution_id, **kwargs)
        return {
            "state": "restored",
            "restored": True,
            "authority_revalidation": {"status": "current"},
            "implementation_authority_granted": False,
        }

    monkeypatch.setattr(recovery, "preview_spec_restoration", preview)
    monkeypatch.setattr(recovery, "restore_specification", restore)
    preview_args = _args(
        "spec-preview",
        "t-one",
        "e-one",
        "--expected-epoch",
        "epoch-1",
        "--expected-authority-generation",
        "7",
    )
    with redirect_stdout(io.StringIO()):
        assert preview_args.handler(preview_args) == 0
    assert observed == {
        "operation": "preview",
        "execution": "e-one",
        "expected_epoch_id": "epoch-1",
        "expected_authority_generation": 7,
    }
    restore_args = _args(
        "spec-restore",
        "t-one",
        "e-one",
        "--expected-epoch",
        "epoch-1",
        "--expected-authority-generation",
        "7",
        "--request-id",
        "request-1",
        "--preview-sha256",
        "sha256:preview",
        "--confirm",
        "Restore approved specification",
    )
    output = io.StringIO()
    with redirect_stdout(output):
        assert restore_args.handler(restore_args) == 0
    assert observed == {
        "operation": "restore",
        "execution": "e-one",
        "request_id": "request-1",
        "preview_sha256": "sha256:preview",
        "expected_epoch_id": "epoch-1",
        "expected_authority_generation": 7,
    }
    assert '"implementation_authority_granted": false' in output.getvalue()


def test_cli_restoration_requires_same_task_and_exact_confirmation(monkeypatch):
    import harness.cli_execution as cli_execution
    import harness.execution_spec_recovery as recovery

    class Context:
        def __enter__(self):
            return _Controller()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli_execution, "_controller", lambda _args: Context())
    monkeypatch.setattr(cli_execution, "get_execution", lambda _conn, _eid: {"task_id": "t-other"})
    args = _args(
        "spec-restore",
        "t-one",
        "e-one",
        "--expected-epoch",
        "epoch-1",
        "--expected-authority-generation",
        "7",
        "--request-id",
        "request-1",
        "--preview-sha256",
        "sha256:preview",
        "--confirm",
        "Restore specification",
    )
    monkeypatch.setattr(
        recovery,
        "restore_specification",
        lambda *_args, **_kwargs: pytest.fail("restored a different task or unconfirmed request"),
    )
    with pytest.raises(ValueError, match="different harness task"):
        args.handler(args)

    monkeypatch.setattr(cli_execution, "get_execution", lambda _conn, _eid: {"task_id": "t-one"})
    with pytest.raises(ValueError, match="exactly equal"):
        args.handler(args)
