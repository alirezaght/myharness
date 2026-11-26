# ruff: noqa: F811
"""Actual Harness-bound interrupted evidence preserves its authenticated observation."""

import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness.execution_store import get_execution
from tests.test_execution_continuity import command, prepare, state
from tests.test_execution_operations import operations  # noqa: F401


def test_public_harness_isolation_refuses_ungranted_command_before_child_creation(
    operations, tmp_path
):
    operation, principal, controller, _, _, execution, envelope = operations
    command(operation, principal, "show")
    assert state(operation)["phase"] == "PLANNING"
    repository = Path(envelope["repository"])
    candidate = repository / "work/isolation.txt"
    candidate.parent.mkdir()
    candidate.write_text("staged candidate\n")
    subprocess.run(
        ["git", "add", "--", "work/isolation.txt"], cwd=repository, check=True, capture_output=True
    )
    candidate.write_text("unstaged candidate overlay\n")

    def git(*arguments):
        return subprocess.check_output(["git", "--no-optional-locks", *arguments], cwd=repository)

    index = Path(os.fsdecode(git("rev-parse", "--git-path", "index")).strip())
    if not index.is_absolute():
        index = repository / index

    def ordinary_git_state():
        return (
            index.read_bytes(),
            git("rev-parse", "HEAD"),
            git("for-each-ref", "refs/heads", "refs/tags", "refs/remotes"),
            git("ls-files", "--stage"),
            git("diff", "--binary"),
            git("diff", "--cached", "--binary"),
            git("worktree", "list", "--porcelain"),
        )

    source = Path(operation.initialization["source"])
    before_source = source.read_bytes()
    before_git = ordinary_git_state()
    approval_fields = ("approval_id", "binding", "epoch_id", "authority_generation", "status")
    current = get_execution(controller.conn, execution["id"])
    before_approval = {key: copy.deepcopy(current[key]) for key in approval_fields}
    before_channels = tuple(operation.check_channels)
    before_owners = set(operation.check_ownership.owners)
    before_allocations = set(operation.check_ownership.allocations)
    before_processes = set(operation.processes.records)
    destination = tmp_path / "ungranted-isolation"
    # This public API does not grant isolate at all; Git and check creation are unreachable.
    with pytest.raises(ValueError, match="^al_command requires only AL subcommands,"):
        operation.dispatch(
            principal,
            "al_command",
            {"arguments": ["isolate", "--authorized", "--path", str(destination)]},
        )
    assert not destination.exists()
    assert source.read_bytes() == before_source
    assert ordinary_git_state() == before_git
    assert candidate.read_text() == "unstaged candidate overlay\n"
    assert tuple(operation.check_channels) == before_channels
    assert set(operation.check_ownership.owners) == before_owners
    assert set(operation.check_ownership.allocations) == before_allocations
    assert set(operation.processes.records) == before_processes
    current = get_execution(controller.conn, execution["id"])
    assert {key: current[key] for key in approval_fields} == before_approval


def proc_bytes(path, maximum):
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    assert len(raw) <= maximum, "owned process observation exceeds bound"
    return raw


def linux_node(pid):
    directory = Path("/proc") / str(pid)

    def stat_identity():
        raw = proc_bytes(directory / "stat", 4096)
        fields = raw.rsplit(b")", 1)[1].split()
        assert len(fields) >= 20 and fields[0] != b"Z", "owned process is unavailable"
        return tuple(int(fields[index]) for index in (1, 2, 3, 19))

    before = stat_identity()
    namespace = (directory / "ns/pid").stat().st_ino
    lines = proc_bytes(directory / "status", 65536).splitlines()
    matches = [line.split()[1:] for line in lines if line.startswith(b"NSpid:")]
    assert len(matches) == 1 and 1 <= len(matches[0]) <= 32, "PID namespace unavailable"
    values = matches[0]
    assert all(0 < len(v) <= 10 and v.isdigit() for v in values), "invalid namespace PID"
    nspids = tuple(map(int, values))
    assert nspids[0] == pid and all(0 < v <= 2147483647 for v in nspids)
    assert before == stat_identity() and namespace == (directory / "ns/pid").stat().st_ino
    return {
        "pid": pid,
        "ppid": before[0],
        "start": before[3],
        "group": before[1],
        "session": before[2],
        "namespace": namespace,
        "nspids": nspids,
    }


def linux_owned_tree(process):
    assert process.poll() is None, "owned wrapper is no longer live"
    deadline = time.monotonic() + 2
    nodes, pending = {}, [(process.pid, None, 0)]
    while pending:
        assert len(nodes) < 256 and time.monotonic() < deadline, "owned process traversal bound"
        pid, parent, depth = pending.pop()
        assert depth <= 64 and pid not in nodes, "ambiguous owned process ancestry"
        node = linux_node(pid)
        assert parent is None or node["ppid"] == parent, "owned child ancestry changed"
        nodes[pid] = node
        tasks = list((Path("/proc") / str(pid) / "task").iterdir())
        assert len(tasks) <= 256, "owned thread observation exceeds bound"
        children = set()
        for task in tasks:
            assert time.monotonic() < deadline, "owned process traversal deadline"
            values = proc_bytes(task / "children", 4096).split()
            assert all(0 < len(v) <= 10 and v.isdigit() for v in values)
            children.update(map(int, values))
        assert len(children) <= 256 and all(1 < child <= 2147483647 for child in children)
        pending.extend((child, pid, depth + 1) for child in children)
    assert process.poll() is None, "owned wrapper exited during observation"
    return nodes


def linux_runner_chain(nodes, wrapper, owner, *, expected=None):
    assert wrapper in nodes, "missing owned wrapper identity"
    matches = []
    for node in nodes.values():
        if node["namespace"] == nodes[wrapper]["namespace"] or node["nspids"][-1] != owner:
            continue
        chain, seen = [], set()
        while True:
            pid = node["pid"]
            assert pid not in seen and len(chain) <= 64, "invalid owned ancestry"
            seen.add(pid)
            chain.append(node)
            if pid == wrapper:
                break
            assert node["ppid"] in nodes, "missing owned ancestry"
            node = nodes[node["ppid"]]
        matches.append(chain)
    assert len(matches) == 1, "owned verifier namespace identity is missing or ambiguous"
    chain = matches[0]
    assert expected is None or chain == expected, "owned verifier identity changed"
    return chain


def signal_linux_runner(process, owner):
    initial = linux_runner_chain(linux_owned_tree(process), process.pid, owner)
    descriptor = os.pidfd_open(initial[0]["pid"])
    try:
        linux_runner_chain(linux_owned_tree(process), process.pid, owner, expected=initial)
        signal.pidfd_send_signal(descriptor, signal.SIGINT)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("defect", ["ambiguous", "missing-owner", "missing-ancestor", "changed"])
def test_linux_runner_mapping_refuses_unverified_identity(defect):
    root = {
        "pid": 100,
        "ppid": 1,
        "start": 10,
        "group": 100,
        "session": 100,
        "namespace": 1,
        "nspids": (100,),
    }
    child = {
        "pid": 101,
        "ppid": 100,
        "start": 11,
        "group": 100,
        "session": 100,
        "namespace": 2,
        "nspids": (101, 2),
    }
    nodes = {100: root, 101: child}
    expected = linux_runner_chain(nodes, 100, 2)
    assert expected == [child, root]
    if defect == "ambiguous":
        nodes[102] = dict(child, pid=102, nspids=(102, 2))
    elif defect == "missing-owner":
        del nodes[100]
    elif defect == "missing-ancestor":
        nodes[101] = dict(child, ppid=999)
    else:
        nodes[101] = dict(child, start=12)
    with pytest.raises(AssertionError):
        linux_runner_chain(nodes, 100, 2, expected=expected)


def test_interrupted_public_harness_check_reconciliation(operations):
    operation, principal, _, _, _, _, envelope = operations
    prepare(operation, principal)
    target = Path(envelope["repository"]) / "work/probe.txt"
    target.parent.mkdir()
    target.write_text("scoped interruption fixture\n")
    command(
        operation,
        principal,
        "evidence",
        "plan",
        "--area",
        "owned-interruption",
        "--kind",
        "check",
        "--scope",
        "work/probe.txt",
        "--",
        sys.executable,
        "-c",
        "import time;time.sleep(60)",
    )
    run = operation.dispatch(
        principal,
        "al_command",
        {
            "arguments": ["evidence", "run", "--area", "owned-interruption"],
            "background": True,
        },
    )
    assert run.get("handle"), run
    record = operation.processes.records[run["handle"]]
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            entries = state(operation)["evidence"]["entries"]
            if record["status"] != "Running":
                raise AssertionError(record["output"])
            if entries and entries[-1]["status"] == "RUNNING" and entries[-1].get("pid"):
                entry = entries[-1]
                assert all(type(entry[k]) is int and entry[k] > 0 for k in ("pid", "owner_pid"))
                reference = entry["process_observation"]
                assert set(reference) == {
                    "schema_version",
                    "mode",
                    "host_instance",
                    "owner_ref",
                    "run",
                    "id",
                    "reservation",
                }
                assert reference["schema_version"] == 1 and reference["mode"] == "harness"
                retained_owner = operation.check_ownership.owners[reference["owner_ref"]]
                assert retained_owner["process"] is record["process"]
                allocation = operation.check_ownership.allocations[
                    tuple(entry[k] for k in ("run", "id", "reservation"))
                ]
                assert allocation["reference"] == reference
                assert len(allocation["checks"]) == 1
                assert next(iter(allocation["checks"].values()))["process"].pid == entry["pid"]
                assert operation.check_ownership.observe(reference, retained_owner["context"]) == {
                    "owner": "live",
                    "checks": "live",
                }
                if sys.platform.startswith("linux"):
                    signal_linux_runner(record["process"], entry["owner_pid"])
                else:
                    assert sys.platform == "darwin"
                    runner = entry["owner_pid"]
                    assert os.getpgid(runner) == record["process"].pid
                    os.kill(runner, signal.SIGINT)
                break
            time.sleep(0.05)
        else:
            raise AssertionError("actual evidence child did not start")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            observed = operation.dispatch(principal, "poll_process", {"handle": run["handle"]})
            if observed["status"] != "Running":
                break
            time.sleep(0.05)
        assert observed["status"] != "Running", observed
    finally:
        if record["status"] == "Running":
            operation.dispatch(principal, "cancel_process", {"handle": run["handle"]})
            deadline = time.monotonic() + 20
            while record["status"] == "Running" and time.monotonic() < deadline:
                operation.dispatch(principal, "poll_process", {"handle": run["handle"]})
                time.sleep(0.05)
            assert record["status"] != "Running", "owned interruption fixture did not settle"
    original = copy.deepcopy(state(operation)["evidence"]["entries"][-1])
    assert original["status"] == "INFRA" and original["interrupted"]
    assert original["process_observation"] == reference
    assert operation.check_ownership.observe(reference, retained_owner["context"]) == {
        "owner": "settled",
        "checks": "settled",
    }
    companion = copy.deepcopy(
        state(operation)["agent_contracts"]["verification-result:" + original["id"]]
    )
    legacy = {
        r["attempt_id"]
        for r in operations[2].conn.execute("SELECT attempt_id FROM policy_legacy_admissions")
    }
    proof = operation.dispatch(
        principal,
        "write_artifact",
        {
            "name": "reconciliation.json",
            "expected_graph_revision": state(operation)["integration"]["graph_revision"],
            "content": json.dumps(
                {
                    "runner_returncode": observed["returncode"],
                    "cleanup": original.get("cleanup"),
                    "entry_id": original["id"],
                }
            ),
        },
    )
    resource = "artifact:" + proof["artifact_id"] + ":" + proof["sha256"]
    source = Path(operation.initialization["source"])
    before = source.read_bytes()
    retained = operation.check_ownership.owners.pop(reference["owner_ref"])
    try:
        refused = command(
            operation,
            principal,
            "check-reconcile",
            "--id",
            original["id"],
            "--reason",
            "A missing original host record cannot establish settlement.",
            "--artifact",
            resource,
            success=False,
        )
        assert "unconfirmed original process ownership" in refused["stdout"] + refused["stderr"]
        assert source.read_bytes() == before
    finally:
        operation.check_ownership.owners[reference["owner_ref"]] = retained
    command(
        operation,
        principal,
        "check-reconcile",
        "--id",
        original["id"],
        "--reason",
        "Observed own runner termination and inspected the retained cleanup.",
        "--artifact",
        resource,
    )
    command(operation, principal, "evidence", "status")
    current = state(operation)["evidence"]["entries"][-1]
    ref = current["reconciliation"]["observation"]
    retained = json.loads(Path(ref["stored_path"]).read_bytes())
    assert retained == {"schema_version": 1, "entry": original, "contract": companion}
    assert current["status"] == "INFRA"

    after = {
        r["attempt_id"]
        for r in operations[2].conn.execute("SELECT attempt_id FROM policy_legacy_admissions")
    }
    assert legacy <= after
