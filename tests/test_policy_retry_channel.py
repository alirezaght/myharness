# ruff: noqa: F811
"""A fixed one-use broker can claim while parent controller lock remains held."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.policy.retry_channel import RetryChannel
from tests.test_policy_admission import (  # noqa: F401
    attempt,
    configure,
    document,
    observed,
    policy_runtime,
    processes,
)


def broker_attempt(r):
    import secrets
    from dataclasses import replace

    from harness.policy.admission import Attempt

    original = attempt(r)
    original.release_unstarted()
    intent = replace(
        original.intent,
        id=secrets.token_hex(16),
        observation_id=None,
        retry=True,
        retry_reference=(
            r.state["run_id"],
            r.operation.envelope["al_session_id"],
            "al-engineer",
            "owned-checkpoint-fixture",
        ),
    )
    return Attempt(r.operation, r.principal, intent, original.digest).admit()


def test_independent_broker_claims_once_under_controller_lock(policy_runtime):
    r = policy_runtime
    doc = document(r, dimension="retry_attempts")
    configure(r, doc)
    a = broker_attempt(r)
    channel = RetryChannel(a, {"agent": "al-engineer", "failure": "opaque-fixture"})
    script = Path(__file__).resolve().parents[1] / "al/codex/skills/al/scripts"
    code = (
        "import sys;sys.path.insert(0,sys.argv[1]);import al_policy_retry;print(a"
        'l_policy_retry.claim({"agent":"al-engineer","failure":"opaque-fixture"})'
        ")"
    )
    try:
        with r.operation.lock:
            child = subprocess.run(
                [sys.executable, "-c", code, str(script)],
                env=dict(
                    os.environ,
                    HARNESS_POLICY_RETRY_FD=str(channel.descriptor),
                    HARNESS_POLICY_RETRY_NONCE=channel.nonce,
                ),
                pass_fds=(channel.descriptor,),
                capture_output=True,
                text=True,
                timeout=10,
            )
        assert child.returncode == 0, child.stderr
        assert a.started and channel.used
        assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
            :
        ] == (1, 0)
        with pytest.raises(ValueError):
            channel.claim(
                {
                    "version": 1,
                    "operation": "claim",
                    "nonce": channel.nonce,
                    "payload": channel.expected,
                }
            )
    finally:
        channel.close()


def test_broker_cannot_select_pool_amount_or_command(policy_runtime):
    r = policy_runtime
    configure(r)
    a = attempt(r)
    channel = RetryChannel(a, {"role": "al-engineer"})
    try:
        with pytest.raises(ValueError):
            channel.claim(
                {
                    "version": 1,
                    "operation": "claim",
                    "nonce": channel.nonce,
                    "payload": {"role": "al-engineer"},
                    "amount": 0,
                }
            )
        assert not a.started
    finally:
        channel.close()


def test_claim_commit_survives_lost_response_and_never_replays(policy_runtime):
    from harness.execution_initialization_channel import send

    r = policy_runtime
    configure(r, document(r, dimension="retry_attempts"))
    a = broker_attempt(r)
    channel = RetryChannel(a, {"failure": "fixed-private-binding"})
    try:
        send(
            channel.client,
            {
                "version": 1,
                "operation": "claim",
                "nonce": channel.nonce,
                "payload": channel.expected,
            },
        )
        channel.client.shutdown(__import__("socket").SHUT_RDWR)
        channel.client.close()
        channel.thread.join(5)
        assert not channel.thread.is_alive()
        assert a.started
        assert r.controller.conn.execute("SELECT consumed,reserved FROM policy_pools").fetchone()[
            :
        ] == (1, 0)
        with pytest.raises(ValueError):
            channel.claim(
                {
                    "version": 1,
                    "operation": "claim",
                    "nonce": channel.nonce,
                    "payload": channel.expected,
                }
            )
    finally:
        channel.close()
