"""Current-host references cannot turn PID aliases or missing records into proof."""

import copy
import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from harness.execution_check_channel import CheckChannel
from harness.execution_check_ownership import CheckOwnership
from harness.reliability.process_context import ProcessContext

CONTEXT = ProcessContext(
    Path("/unused-test-database"), "execution", "epoch", 1, "original-observation", "principal"
)
BINDING = {"run": "run", "id": "E1", "reservation": "allocation"}
COMPLETE = {"complete": True, "leader_reaped": True, "owned_group_settled": True}


@contextmanager
def owned(program="import time;time.sleep(60)"):
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", program],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield process
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)


def allocation(process, *, coordinator=False):
    registry = CheckOwnership()
    owner = registry.bind_owner(process, CONTEXT, None)
    reference = registry.register(owner, BINDING, coordinator=coordinator)
    return registry, owner, reference


def test_retained_actual_owner_and_all_checks_must_settle():
    with owned() as owner, owned() as child, owned("pass") as done:
        done.wait(timeout=5)
        registry, owner_ref, reference = allocation(owner)
        registry.attach(
            reference, owner_ref, CONTEXT, "first", {"process": done, "completion": dict(COMPLETE)}
        )
        record = {"process": child, "completion": None}
        registry.attach(reference, owner_ref, CONTEXT, "second", record)
        assert registry.observe(reference, CONTEXT) == {"owner": "live", "checks": "live"}
        owner.kill()
        owner.wait(timeout=5)
        assert registry.observe(reference, CONTEXT) == {"owner": "settled", "checks": "live"}
        child.kill()
        child.wait(timeout=5)
        assert registry.observe(reference, CONTEXT)["checks"] == "unknown"
        record["completion"] = dict(COMPLETE)
        assert registry.observe(
            reference, replace(CONTEXT, observation_id="fresh-observation")
        ) == {"owner": "settled", "checks": "settled"}


@pytest.mark.parametrize(
    "defect",
    [
        "instance",
        "owner",
        "reservation",
        "extra",
        "restart",
        "context",
        "principal",
        "generation",
        "missing",
        "malformed",
    ],
)
def test_foreign_missing_and_restarted_references_are_unknown(defect):
    with owned("pass") as process:
        process.wait(timeout=5)
        registry, _, reference = allocation(process, coordinator=True)
        context = CONTEXT
        if defect == "restart":
            registry = CheckOwnership()
        elif defect == "context":
            context = replace(context, epoch_id="different-epoch")
        elif defect == "principal":
            context = replace(context, principal_id="different-principal")
        elif defect == "generation":
            context = replace(context, authority_generation=2)
        elif defect == "malformed":
            reference = reference | {"schema_version": True}
        elif defect == "missing":
            registry.owners.clear()
        else:
            key = {
                "instance": "host_instance",
                "owner": "owner_ref",
                "reservation": "reservation",
                "extra": "untrusted",
            }[defect]
            reference = reference | {key: "foreign"}
        assert registry.observe(reference, context) == {"owner": "unknown", "checks": "unknown"}


def test_dead_leader_with_real_live_descendant_is_not_settled():
    program = (
        "import subprocess,sys; "
        'p=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"],'
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print(p.pid,flush=True)"
    )
    with owned(program) as leader:
        descendant = int(leader.stdout.readline(64))
        leader.wait(timeout=5)
        assert os.getpgid(descendant) == leader.pid
        registry, _, reference = allocation(leader, coordinator=True)
        assert registry.observe(reference, CONTEXT)["owner"] == "unknown"


def test_lookup_is_passive_and_permission_denial_is_unknown(monkeypatch):
    with owned("pass") as process:
        process.wait(timeout=5)
        registry, _, reference = allocation(process, coordinator=True)
        calls = []

        def denied(pid, number):
            calls.append((pid, number))
            raise PermissionError("test-owned denial")

        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", denied)
            assert registry.observe(reference, CONTEXT)["owner"] == "unknown"
        assert calls == [(process.pid, 0)]


def test_no_child_is_unknown_except_explicit_coordinator():
    with owned("pass") as process:
        process.wait(timeout=5)
        registry, _, reference = allocation(process)
        assert registry.observe(reference, CONTEXT)["checks"] == "unknown"
        other = dict(BINDING, id="E2", reservation="second")
        coordinator = registry.register(reference["owner_ref"], other, coordinator=True)
        assert registry.observe(coordinator, CONTEXT)["checks"] == "settled"


def test_conflicting_allocation_and_caller_invented_check_owner_refuse():
    with owned() as process:
        registry, owner, reference = allocation(process)
        before = copy.deepcopy(registry.allocations)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(owner, BINDING, coordinator=False)
        with pytest.raises(ValueError, match="owner changed"):
            registry.attach(reference, "invented", CONTEXT, "handle", {"process": process})
        assert registry.allocations == before


def test_registration_waits_for_actual_spawn_binding(tmp_path):
    registry = CheckOwnership()
    entered = threading.Event()
    results = []
    channel = CheckChannel(
        tmp_path / "unused-context",
        lambda: None,
        ownership=registry,
        validate_allocation=lambda *a, **kw: {},
    )
    channel.process_context = CONTEXT

    def register():
        entered.set()
        results.append(
            channel.request(
                {"action": "register_owner", "binding": BINDING, "coordinator": True}, []
            )
        )

    thread = threading.Thread(target=register)
    try:
        with owned() as process:
            thread.start()
            assert entered.wait(timeout=2)
            assert not channel.owner_ready.is_set() and not registry.allocations
            channel.bind_owner(process, None)
            thread.join(timeout=6)
            assert not thread.is_alive()
            assert len(results) == 1
            assert registry.observe(results[0], CONTEXT)["owner"] == "live"
    finally:
        channel.owner_ready.set()
        if thread.ident is not None:
            thread.join(timeout=6)
        channel.close()
