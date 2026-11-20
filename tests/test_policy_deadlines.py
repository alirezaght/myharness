# ruff: noqa: F811
"""Fixed absolute deadlines, conservative unsupported accounting and clock continuity."""

import time

import pytest

from tests.test_policy_admission import (  # noqa: F401
    attempt,
    configure,
    document,
    observed,
    patch,
    policy_runtime,
    processes,
)


@pytest.mark.parametrize("dimension", ["generation_tokens", "monetary_cost"])
def test_required_unqualified_usage_refuses_before_patch(policy_runtime, dimension):
    r = policy_runtime
    with pytest.raises(ValueError, match="UNSUPPORTED_ENFORCEMENT_CAPABILITY"):
        configure(r, document(r, dimension=dimension, maximum=100))
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_versions").fetchone()[0] == 0


def test_subsecond_remaining_is_not_rounded_or_restarted(policy_runtime):
    r = policy_runtime
    configure(r, document(r, dimension="operation_deadline_ms", maximum=80))
    a = attempt(r)
    a.start()
    assert 0 < a.deadline - time.monotonic() <= 0.08
    time.sleep(0.09)
    from harness.policy.context import check_deadline, scope

    with scope(a), pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        check_deadline()
    r.controller.conn.execute("UPDATE policy_pools SET maximum=0")
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        attempt(r)


def test_clock_rollback_refuses_new_work(policy_runtime):
    r = policy_runtime
    configure(r, document(r, dimension="execution_elapsed_ms", maximum=5000))
    r.controller.conn.execute("UPDATE policy_pools SET last_wall=?", (time.time() + 100,))
    with pytest.raises(ValueError, match="CLOCK_UNCERTAIN"):
        attempt(r)


def test_boot_change_refuses_time_budget_instead_of_resetting_interval(policy_runtime, monkeypatch):
    r = policy_runtime
    configure(r, document(r, dimension="execution_elapsed_ms", maximum=5000))
    monkeypatch.setattr("harness.policy.deadlines.CLOCK_ID", "different-boot")
    with pytest.raises(ValueError, match="CLOCK_UNCERTAIN"):
        attempt(r)


def test_elapsed_remainder_uses_original_root_interval_without_rounding(
    policy_runtime, monkeypatch
):
    from types import SimpleNamespace

    import harness.policy.deadlines as clocks

    r = policy_runtime
    configure(r, document(r, dimension="execution_elapsed_ms", maximum=5000))
    row = r.controller.conn.execute("SELECT * FROM policy_pools").fetchone()
    fake = SimpleNamespace(
        time=lambda: row["origin_wall"] + 4.960, monotonic=lambda: row["origin_monotonic"] + 4.960
    )
    monkeypatch.setattr(clocks, "time", fake)
    a = attempt(r)
    a.start()
    assert 0 < a.deadline - fake.monotonic() < 0.041
    fake.time = lambda: row["origin_wall"] + 5.001
    fake.monotonic = lambda: row["origin_monotonic"] + 5.001
    with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
        attempt(r)


def test_historical_missing_origin_is_not_backfilled_or_reconstructed(policy_runtime):
    r = policy_runtime
    r.controller.conn.execute(
        "DELETE FROM policy_origins WHERE execution_id=?", (r.operation.execution_id,)
    )
    with pytest.raises(ValueError, match="CLOCK_UNCERTAIN"):
        configure(r, document(r, dimension="execution_elapsed_ms", maximum=5000))
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_origins").fetchone()[0] == 0
    assert r.controller.conn.execute("SELECT COUNT(*) FROM policy_versions").fetchone()[0] == 0


def test_new_execution_origin_is_one_trusted_atomic_receipt_and_never_resets(policy_runtime):
    from harness.execution import start_execution

    r = policy_runtime
    before = dict(
        r.controller.conn.execute(
            "SELECT * FROM policy_origins WHERE execution_id=?", (r.operation.execution_id,)
        ).fetchone()
    )
    assert before["clock_id"] and before["wall"] > 0 and before["monotonic"] > 0
    start_execution(r.controller, r.operation.envelope["task_id"], host="codex", launch=False)
    after = dict(
        r.controller.conn.execute(
            "SELECT * FROM policy_origins WHERE execution_id=?", (r.operation.execution_id,)
        ).fetchone()
    )
    assert after == before


@pytest.mark.parametrize("dimension", ["operation_deadline_ms", "execution_elapsed_ms"])
@pytest.mark.parametrize("boundary", ["before", "hash", "pread", "metadata", "decode"])
def test_public_read_never_returns_a_page_after_its_bound(
    policy_runtime, monkeypatch, dimension, boundary
):
    import os
    from pathlib import Path

    from harness import execution_files
    from harness.policy.admission import Attempt
    from harness.policy.context import CURRENT

    r = policy_runtime
    path = Path(r.operation.envelope["repository"]) / "svc/read.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"ordinary confined read\n" * 7000)
    if boundary in {"pread", "decode"}:
        with path.open("r+b") as stream:
            stream.truncate(execution_files.MAX_HASH_BYTES + 1)
    inode = path.stat().st_ino
    configure(r, document(r, dimension=dimension, maximum=5000))
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    opened, closed, reads = [], [], []
    real_open, real_close, real_read, real_pread, real_stat = (
        os.open,
        os.close,
        os.read,
        os.pread,
        os.stat,
    )

    def expire():
        current = CURRENT.get()
        assert current.started and current.deadline is not None
        clock[0] = current.deadline

    def target(fd):
        return os.fstat(fd).st_ino == inode

    def opening(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        if target(fd):
            opened.append(fd)
        return fd

    def closing(fd):
        if target(fd):
            closed.append(fd)
        return real_close(fd)

    def reading(fd, amount):
        raw = real_read(fd, amount)
        if target(fd):
            reads.append(amount)
            if boundary == "hash":
                expire()
        return raw

    class DecodeBoundary(bytes):
        def decode(self, *args, **kwargs):
            value = super().decode(*args, **kwargs)
            expire()
            return value

    def paging(fd, amount, offset):
        raw = real_pread(fd, amount, offset)
        if target(fd):
            reads.append(amount)
            if boundary == "pread":
                expire()
            elif boundary == "decode":
                return DecodeBoundary(raw)
        return raw

    def stating(*args, **kwargs):
        info = real_stat(*args, **kwargs)
        if boundary == "metadata" and info.st_ino == inode and kwargs.get("dir_fd") is not None:
            expire()
        return info

    if boundary == "before":
        real_start = Attempt.start

        def starting(current, **kwargs):
            permit = real_start(current, **kwargs)
            expire()
            return permit

        monkeypatch.setattr(Attempt, "start", starting)
    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "close", closing)
    monkeypatch.setattr(os, "read", reading)
    monkeypatch.setattr(os, "pread", paging)
    monkeypatch.setattr(os, "stat", stating)
    with pytest.raises(ValueError, match="^BUDGET_EXHAUSTED$"):
        r.operation.dispatch(r.principal, "read_file", {"path": "svc/read.txt", "limit": 17})
    assert opened == closed
    if boundary == "before":
        assert not opened and not reads
    elif boundary in {"hash", "pread", "decode"}:
        assert len(reads) == 1
    assert all(amount <= 65536 for amount in reads)
    attempt_row = r.controller.conn.execute("SELECT state,outcome FROM policy_attempts").fetchone()
    assert tuple(attempt_row) == ("settled", "failed")
    assert all(
        row[0] == "closed"
        for row in r.controller.conn.execute("SELECT state FROM policy_resource_claims")
    )


@pytest.mark.parametrize("dimension", ["operation_deadline_ms", "execution_elapsed_ms"])
@pytest.mark.parametrize("boundary", ["metadata-delete", "metadata-update", "write", "fsync"])
def test_patch_never_commits_target_after_original_bound(
    policy_runtime, monkeypatch, dimension, boundary
):
    import os
    from pathlib import Path

    from harness.policy.context import CURRENT
    from tests.test_policy_capabilities import grant

    r = policy_runtime
    target = Path(r.operation.envelope["repository"]) / "svc/new.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_text("old\n")
    doc = document(r, maximum=5, rules={"required_approvals": ["grant"]})
    doc["limits"].append(
        {
            "pool_id": "time-pool",
            "dimension": dimension,
            "domain": "execution_tree",
            "owner_ref": r.operation.execution_id,
            "maximum": 5000,
        }
    )
    configure(r, doc)
    grant(r)
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    real_stat, real_open, real_fsync, real_fdopen, real_replace, real_unlink = (
        os.stat,
        os.open,
        os.fsync,
        os.fdopen,
        os.replace,
        os.unlink,
    )
    temporary_fds, commits, boundaries = set(), [], []

    def expire():
        current = CURRENT.get()
        assert current.started and current.deadline is not None
        clock[0] = current.deadline
        boundaries.append(boundary)

    def stating(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        current = CURRENT.get()
        if (
            boundary.startswith("metadata")
            and path == "new.txt"
            and kwargs.get("dir_fd") is not None
            and current
            and current.started
        ):
            expire()
        return result

    def opening(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if str(path).startswith(".harness-patch-"):
            temporary_fds.add(fd)
        return fd

    class Writing:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, raw):
            count = self.stream.write(raw)
            expire()
            return count

        def flush(self):
            return self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    def opening_stream(fd, *args, **kwargs):
        stream = real_fdopen(fd, *args, **kwargs)
        return Writing(stream) if boundary == "write" and fd in temporary_fds else stream

    def syncing(fd):
        result = real_fsync(fd)
        if boundary == "fsync" and fd in temporary_fds:
            expire()
        return result

    def replacing(source, destination, **kwargs):
        if destination == "new.txt":
            commits.append("replace")
        return real_replace(source, destination, **kwargs)

    def unlinking(path, *args, **kwargs):
        if path == "new.txt":
            commits.append("unlink")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stating)
    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "fdopen", opening_stream)
    monkeypatch.setattr(os, "fsync", syncing)
    monkeypatch.setattr(os, "replace", replacing)
    monkeypatch.setattr(os, "unlink", unlinking)
    edit = (
        "*** Delete File: svc/new.txt\n"
        if boundary == "metadata-delete"
        else "*** Update File: svc/new.txt\n@@\n-old\n+new\n"
    )
    rejected = None
    try:
        r.operation.dispatch(
            r.principal,
            "apply_patch",
            {
                "patch": "*** Begin Patch\n" + edit + "*** End Patch",
                "task_id": "work",
                "expected_revision": r.revision,
            },
        )
    except ValueError as error:
        rejected = str(error)
    assert boundaries, "fixture must cross bound during actual target metadata or temporary IO"
    assert rejected == "BUDGET_EXHAUSTED" and not commits, (
        f"R3-FRESH-002: after {boundary}, rejection={rejected}, target_mutations={commits}"
    )
    assert target.read_text() == "old\n"
    assert not list(target.parent.glob(".harness-patch-*"))
    assert r.controller.conn.execute(
        "SELECT consumed,reserved FROM policy_pools WHERE id='base-pool'"
    ).fetchone()[:] == (1, 0)
    assert r.controller.conn.execute(
        "SELECT uses,units FROM policy_capabilities WHERE id='grant'"
    ).fetchone()[:] == (1, 1)


@pytest.mark.parametrize("dimension", ["operation_deadline_ms", "execution_elapsed_ms"])
def test_public_read_retains_historical_parent_time_ceiling(policy_runtime, monkeypatch, dimension):
    import os
    from pathlib import Path

    from harness.policy.context import CURRENT

    r = policy_runtime
    path = Path(r.operation.envelope["repository"]) / "svc/time.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"safe source\n" * 10000)
    original_maximum = 80 if dimension == "operation_deadline_ms" else 5000
    configure(r, document(r, policy="parent-time", dimension=dimension, maximum=original_maximum))
    child = document(r, policy="child-time", maximum=20)
    child["parents"] = ["parent-time-v1"]
    configure(r, child)
    configure(
        r,
        document(r, policy="parent-time", dimension=dimension, maximum=10000, version=2),
        revision=1,
    )
    pool = r.controller.conn.execute(
        "SELECT * FROM policy_pools WHERE id='parent-time-pool'"
    ).fetchone()
    assert pool["maximum"] == 10000
    if dimension == "execution_elapsed_ms":
        clock = [pool["origin_monotonic"] + 4.960, pool["origin_wall"] + 4.960]
        expected = pool["origin_monotonic"] + 5
    else:
        clock = [time.monotonic(), time.time()]
        expected = clock[0] + original_maximum / 1000
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "time", lambda: clock[1])
    real_read = os.read
    seen = []
    inode = path.stat().st_ino

    def crossing(fd, amount):
        raw = real_read(fd, amount)
        if os.fstat(fd).st_ino == inode:
            seen.append(CURRENT.get().deadline)
            clock[0] = expected + 0.001
        return raw

    monkeypatch.setattr(os, "read", crossing)
    with pytest.raises(ValueError, match="^BUDGET_EXHAUSTED$"):
        r.operation.dispatch(r.principal, "read_file", {"path": "svc/time.txt"})
    assert seen == [pytest.approx(expected, abs=0.00001)]
    assert r.controller.conn.execute(
        "SELECT maximum,origin_monotonic FROM policy_pools WHERE id='parent-time-pool'"
    ).fetchone()[:] == (10000, pool["origin_monotonic"])


def test_patch_post_commit_expiry_reports_failure_without_rollback_or_refund(
    policy_runtime, monkeypatch
):
    import os
    from pathlib import Path

    from harness.policy.context import CURRENT

    r = policy_runtime
    doc = document(r, maximum=5)
    doc["limits"].append(
        {
            "pool_id": "return-time",
            "dimension": "operation_deadline_ms",
            "domain": "execution_tree",
            "owner_ref": r.operation.execution_id,
            "maximum": 5000,
        }
    )
    configure(r, doc)
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    real_replace = os.replace

    def completed(*args, **kwargs):
        result = real_replace(*args, **kwargs)
        current = CURRENT.get()
        assert current.started
        clock[0] = current.deadline
        return result

    monkeypatch.setattr(os, "replace", completed)
    with pytest.raises(ValueError, match="^BUDGET_EXHAUSTED$"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r))
    assert (Path(r.operation.envelope["repository"]) / "svc/new.txt").read_text() == "safe\n"
    assert r.controller.conn.execute(
        "SELECT consumed,reserved FROM policy_pools WHERE id='base-pool'"
    ).fetchone()[:] == (1, 0)
    assert r.controller.conn.execute("SELECT state,outcome FROM policy_attempts").fetchone()[:] == (
        "settled",
        "failed",
    )


def test_patch_expired_traversal_does_not_create_later_directory(policy_runtime, monkeypatch):
    import os
    from pathlib import Path

    from harness.policy.context import CURRENT

    r = policy_runtime
    root = Path(r.operation.envelope["repository"])
    (root / "svc").mkdir(exist_ok=True)
    configure(r, document(r, dimension="operation_deadline_ms", maximum=5000))
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    real_open = os.open
    crossed = []

    def traversal(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        current = CURRENT.get()
        if path == "svc" and current and current.started:
            crossed.append(True)
            clock[0] = current.deadline
        return fd

    monkeypatch.setattr(os, "open", traversal)
    with pytest.raises(ValueError, match="^BUDGET_EXHAUSTED$"):
        r.operation.dispatch(r.principal, "apply_patch", patch(r, "svc/later/new.txt"))
    assert crossed and not (root / "svc/later").exists()
