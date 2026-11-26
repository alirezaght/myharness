"""Handle protocol integration; doubles establish bookkeeping, not containment qualification."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.execution_check_channel import CheckChannel


@pytest.fixture
def channel(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps({"repository": str(repo), "temporary": str(tmp_path), "classification": None})
    )
    result = CheckChannel(context, lambda: None)
    yield result, repo
    result.close()


class Process:
    pid = 987654321
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def instrument(monkeypatch, channel):
    hooks = []
    observers = []
    allocated = []

    class Hooks:
        def __init__(self, attempt):
            self.attempt = attempt
            self.events = []
            hooks.append(self)

        def prepare(self, handle, observer):
            self.handle = handle
            self.observer = observer
            self.events.append("prepared")
            return self.attempt.deadline

        def spawned(self, process):
            self.process = process
            self.events.append("spawned")

        def finish(self, **result):
            self.events.append(result)

    def observer(*args, **kwargs):
        value = SimpleNamespace(notify=lambda *a, **kw: None)
        observers.append(value)
        return value

    def temporary():
        path = Path(channel.context_path).parent / ("temporary-" + str(len(allocated)))
        path.mkdir()
        allocated.append(path)
        return str(path)

    monkeypatch.setattr("harness.policy.processes.ProcessHooks", Hooks)
    monkeypatch.setattr("harness.reliability.process_store.pending_process", observer)
    monkeypatch.setattr("harness.execution_isolation.create_process_temporary", temporary)
    monkeypatch.setattr(
        "harness.execution_isolation.release_process_temporary",
        lambda p: Path(p).rmdir() if p and Path(p).exists() else None,
    )
    monkeypatch.setattr(
        "harness.execution_check_channel.subprocess.Popen", lambda *a, **kw: Process()
    )

    def absent(*args):
        raise ProcessLookupError()

    monkeypatch.setattr("harness.execution_check_channel.os.killpg", absent)
    channel.policy_attempt = SimpleNamespace(
        intent=SimpleNamespace(principal_id="owner", task_id="task", revision="revision"),
        deadline=time.monotonic() + 20,
    )
    return hooks, observers, allocated


def start(channel, repo):
    return channel.request(
        {"action": "start", "argv": ["python", "-c", "pass"], "cwd": str(repo)}, [1, 2]
    )["handle"]


def test_retained_handles_keep_own_hooks_deadline_temporary_and_ack(channel, monkeypatch):
    channel, repo = channel
    hooks, observers, allocated = instrument(monkeypatch, channel)
    completed = []
    acknowledged = []
    channel.storage = SimpleNamespace(
        records={},
        completed=lambda h, c: completed.append((h, c.copy())),
        acknowledge=lambda h, r, c, **kw: acknowledged.append((h, r, c.copy())) or {"handle": h},
        retain=lambda *a: None,
    )
    first = start(channel, repo)
    channel.records[first]["process"].returncode = 0
    channel.observe(first)
    second = start(channel, repo)
    assert first != second and hooks[0] is not hooks[1] and observers[0] is not observers[1]
    assert (
        channel.records[first]["deadline"]
        == channel.records[second]["deadline"]
        == channel.policy_attempt.deadline
    )
    assert not allocated[0].exists() and allocated[1].exists()
    response = channel.request(
        {
            "action": "acknowledge_publication",
            "schema_version": 1,
            "handle": first,
            "publication": {"id": "reference"},
        },
        [],
    )
    assert response == {"handle": first} and acknowledged[0][0] == first
    assert hooks[1].events == ["prepared", "spawned"]
    assert completed[0][1]["descendants_settled"] is None
    assert completed[0][1]["descendant_scope"] == "group"
    assert "boundary_closed" not in completed[0][1]
    channel.records[second]["process"].returncode = 0


def test_storage_callback_failure_after_spawn_retains_process_and_hooks(channel, monkeypatch):
    channel, repo = channel
    hooks, _, allocated = instrument(monkeypatch, channel)
    # Real channel source path prepares its managed context before invoking Popen.
    core = {
        "storage_filesystem": SimpleNamespace(publish_json=lambda path, value: {"path": str(path)})
    }

    def fault(*a):
        raise OSError("storage-fault")

    channel.storage = SimpleNamespace(
        core=core,
        records={},
        begin=lambda h, s, a: ({"declaration": {}, "binding": {}}, a),
        started=fault,
        completed=lambda *a: None,
        retain=lambda *a: None,
    )
    with pytest.raises(OSError, match="storage-fault"):
        channel.request(
            {"action": "start", "argv": ["python", "-c", "pass"], "cwd": str(repo), "storage": {}},
            [1, 2],
        )
    record = channel.records[channel.active_handle]
    assert record["process"] is hooks[0].process
    assert allocated[0].exists() and record["policy"] is hooks[0]
    record["process"].returncode = 0


def test_failed_popen_keeps_pending_hook_and_releases_only_temporary(channel, monkeypatch):
    channel, repo = channel
    hooks, _, allocated = instrument(monkeypatch, channel)

    def failed(*a, **kw):
        raise OSError("popen-fault")

    monkeypatch.setattr("harness.execution_check_channel.subprocess.Popen", failed)
    with pytest.raises(OSError, match="popen-fault"):
        start(channel, repo)
    record = channel.records[channel.active_handle]
    assert record["process"] is None and record["policy"] is hooks[0]
    assert hooks[0].events[-1] == {"closed": False, "outcome": "failed"}
    assert not allocated[0].exists()


def test_later_handle_cannot_restart_an_expired_original_deadline(channel, monkeypatch):
    channel, repo = channel
    hooks, _, _ = instrument(monkeypatch, channel)
    first = start(channel, repo)
    channel.records[first]["process"].returncode = 0
    channel.observe(first)
    channel.policy_attempt.deadline = time.monotonic() - 1
    calls = []
    monkeypatch.setattr(
        "harness.execution_check_channel.subprocess.Popen", lambda *a, **kw: calls.append(a)
    )
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        start(channel, repo)
    assert not calls and len(hooks) == 2
    assert channel.records[channel.active_handle]["process"] is None


def test_sealed_profile_preparation_never_acquires_optimization_grants(monkeypatch, tmp_path):
    from harness.policy import control_boundary

    seen = []
    policy = tmp_path / "profile"
    policy.write_text("(version 1)")
    profile = SimpleNamespace(policy=policy)

    def prepare(*args, **kwargs):
        seen.append(kwargs)
        return profile

    operation = SimpleNamespace(profile=prepare, envelope={"repository": str(tmp_path)})
    monkeypatch.setattr(control_boundary.sys, "platform", "darwin")
    monkeypatch.setattr(control_boundary, "operation_popen", lambda *a, **kw: object())
    monkeypatch.setattr(control_boundary, "capture", lambda *a, **kw: (b"denied\n", 0))
    assert control_boundary.prepare(operation, "/trusted/python", al=True)[0] is profile
    assert seen == [{"al": True, "al_optimizations": False}]
    # This is profile-selection protocol proof; the real probe is not mocked into qualification.


@pytest.mark.parametrize("value", ["HEAD", "--config-env=x", "a" * 40 + ";bad", "../tree"])
def test_retained_tree_broker_refuses_unbound_operands(value):
    from harness.policy.control_git import argv

    with pytest.raises(ValueError):
        argv("retained_tree", {"revision": value})


def test_retained_tree_broker_is_fixed_read_only_object_check():
    from harness.policy.control_git import argv

    assert argv("retained_tree", {"revision": "a" * 40}) == ["cat-file", "-e", "a" * 40 + "^{tree}"]


def test_control_descriptor_presence_skips_service_only(tmp_path):
    from al.installer.runtime_service_client import launcher_prelude

    source = launcher_prelude(tmp_path, "fixture-identity")
    definitions, guard = source.rsplit("\nif len", 1)
    namespace = {
        "sys": SimpleNamespace(argv=["alctl", "run", "--repository", str(tmp_path)]),
        "os": SimpleNamespace(environ={"HARNESS_RETRY_CONTROL_FD": "invalid"}),
    }
    exec(compile(definitions, "<actual-service-definitions>", "exec"), namespace)
    calls = []
    namespace["_al_service_run"] = lambda *args: calls.append(args)
    exec(compile("if len" + guard, "<actual-service-guard>", "exec"), namespace)
    assert not calls
    del namespace["os"].environ["HARNESS_RETRY_CONTROL_FD"]
    exec(compile("if len" + guard, "<actual-service-guard>", "exec"), namespace)
    assert len(calls) == 1
    # Presence bypasses optimization; authenticated activation still rejects invalid descriptors.


@pytest.mark.parametrize("policy", [None, {"version": 2, "default_stdin": "closed-pipe"}])
def test_start_selects_owned_eof_pipe_or_retains_legacy_devnull(channel, monkeypatch, policy):
    import os
    import stat
    import subprocess

    channel, repo = channel
    instrument(monkeypatch, channel)
    backend = subprocess.Popen
    observed = []

    def capture(*args, **kwargs):
        descriptor = kwargs["stdin"]
        if policy is None:
            assert descriptor == subprocess.DEVNULL
        else:
            assert stat.S_ISFIFO(os.fstat(descriptor).st_mode)
            assert os.read(descriptor, 1) == b""
            observed.append(descriptor)
        return backend(*args, **kwargs)

    monkeypatch.setattr("harness.execution_check_channel.subprocess.Popen", capture)
    request = {"action": "start", "argv": ["controlled-check"], "cwd": str(repo)}
    if policy is not None:
        request["execution_policy"] = policy
    result = channel.request(request, [1, 2])
    assert result["handle"] in channel.records and result["pid"] == Process.pid
    assert ("execution_policy" in result) is (policy is not None)
    if policy is not None:
        assert result["execution_policy"] == policy and len(observed) == 1
        with pytest.raises(OSError):
            os.fstat(observed[0])


def test_capabilities_and_invalid_policy_do_not_observe_or_allocate(channel, monkeypatch):
    channel, repo = channel
    hooks, observers, allocated = instrument(monkeypatch, channel)
    handle = start(channel, repo)
    retained = channel.records[handle]
    counts = (len(hooks), len(observers), len(allocated))

    def forbidden(*args):
        raise AssertionError("capability/policy request observed a check")

    with monkeypatch.context() as patch:
        patch.setattr(channel, "observe", forbidden)
        assert channel.request({"action": "capabilities", "schema_version": 1}, []) == {
            "schema_version": 1,
            "execution_policy": {"version": 2, "default_stdin": "closed-pipe"},
        }
        assert channel.requesting_handle is None
        invalid = [
            None,
            {},
            {"version": 1, "default_stdin": "devnull"},
            {"version": 2.0, "default_stdin": "closed-pipe"},
            {"version": 2, "default_stdin": "closed-pipe", "extra": True},
        ]
        for policy in invalid:
            with pytest.raises(ValueError, match="execution policy"):
                channel.request(
                    {
                        "action": "start",
                        "execution_policy": policy,
                        "argv": ["controlled-check"],
                        "cwd": str(repo),
                        "storage": {"unusable": True},
                    },
                    [1, 2],
                )
            assert channel.requesting_handle is None
        for request, descriptors in [
            ({"action": "capabilities", "schema_version": True}, []),
            ({"action": "capabilities", "schema_version": 1, "handle": handle}, []),
            ({"action": "capabilities", "schema_version": 1}, [1]),
        ]:
            with pytest.raises(ValueError, match="capabilities"):
                channel.request(request, descriptors)
        assert channel.active_handle == handle and channel.records == {handle: retained}
        assert counts == (len(hooks), len(observers), len(allocated))


def test_capabilities_still_validate_channel_authority(channel, monkeypatch):
    channel, _ = channel

    def denied():
        raise ValueError("controlled authority loss")

    monkeypatch.setattr(channel, "validate", denied)
    with pytest.raises(ValueError, match="controlled authority loss"):
        channel.request({"action": "capabilities", "schema_version": 1}, [])
    assert not channel.records and channel.active_handle is None


def test_spawn_failure_closes_backend_default_pipe(channel, monkeypatch):
    import os
    import stat

    channel, repo = channel
    _, _, allocated = instrument(monkeypatch, channel)
    observed = []

    def fail(*args, **kwargs):
        descriptor = kwargs["stdin"]
        observed.append(descriptor)
        assert stat.S_ISFIFO(os.fstat(descriptor).st_mode)
        assert os.read(descriptor, 1) == b""
        raise OSError("controlled backend spawn refusal")

    monkeypatch.setattr("harness.execution_check_channel.subprocess.Popen", fail)
    with pytest.raises(OSError, match="controlled backend spawn refusal"):
        channel.request(
            {
                "action": "start",
                "argv": ["controlled-check"],
                "cwd": str(repo),
                "execution_policy": {"version": 2, "default_stdin": "closed-pipe"},
            },
            [1, 2],
        )
    assert len(observed) == 1 and len(allocated) == 1 and not allocated[0].exists()
    with pytest.raises(OSError):
        os.fstat(observed[0])
