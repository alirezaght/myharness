"""Diagnostic progress must never replace source observation or writer authority."""
import copy
import hashlib
import io
import threading

import pytest

import al_repository_facts as facts
import al_scope as scope
import al_task_attribution as attribution


def test_disabled_progress_is_silent_and_does_not_read_clock(monkeypatch, capsys):
    import al_writer_progress as progress

    def unavailable_clock():
        raise AssertionError("disabled diagnostics must not read the clock")

    monkeypatch.setattr(progress.time, "monotonic", unavailable_clock)
    progress.report("inventory-current", count=1000)
    assert capsys.readouterr() == ("", "")


def test_progress_reports_real_counts_throttles_and_flushes(monkeypatch, capsys):
    import al_writer_progress as progress

    class Stream(io.StringIO):
        flushes = 0

        def flush(self):
            self.flushes += 1
            return super().flush()

    stream = Stream()
    clock = iter([10.0, 10.0, 11.0, 15.0, 15.1])
    monkeypatch.setattr(progress.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(progress.sys, "stderr", stream)
    with progress.writer_progress():
        progress.report("inventory-current", count=1)
        progress.report("inventory-current", count=2)
        progress.report("inventory-current", count=3)
        progress.report("inventory-base", count=0)
    lines = stream.getvalue().splitlines()
    assert len(lines) == stream.flushes == 3
    assert "1 items processed" in lines[0] and "0.0s elapsed" in lines[0]
    assert "3 items processed" in lines[1] and "5.0s elapsed" in lines[1]
    assert "0 items processed" in lines[2] and "5.1s elapsed" in lines[2]
    assert "reading current repository inputs" in lines[0]
    assert "reading immutable baseline inputs" in lines[2]
    assert capsys.readouterr().out == ""


def test_only_fixed_stages_and_integer_counts_can_be_rendered(capsys):
    import al_writer_progress as progress

    with progress.writer_progress():
        progress.report("private/api-key=never-render-this", count=42, force=True)
        progress.report("inventory-current", count="/secret/path", force=True)
        progress.report("inventory-current", count=True, force=True)
        progress.report("inventory-current", count=-1, force=True)
        progress.report("inventory-current", count=10**100, force=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private" not in captured.err and "secret" not in captured.err
    assert "True" not in captured.err and "-1 items" not in captured.err


@pytest.mark.parametrize("kind", ["absent", "closed", "broken-write", "broken-flush"])
def test_unavailable_stderr_does_not_change_operation(monkeypatch, kind):
    import al_writer_progress as progress

    class Broken(io.StringIO):
        def write(self, value):
            if kind == "broken-write":
                raise OSError("diagnostics unavailable")
            return super().write(value)

        def flush(self):
            if kind == "broken-flush":
                raise ValueError("closed diagnostics")
            return super().flush()

    stream = None if kind == "absent" else Broken()
    if kind == "closed":
        stream.close()
    monkeypatch.setattr(progress.sys, "stderr", stream)
    with progress.writer_progress():
        progress.report("inventory-current", count=1, force=True)
        result = 17
    assert result == 17


def test_failed_diagnostic_clock_is_nonfatal(monkeypatch):
    import al_writer_progress as progress

    def bad_clock():
        raise OSError("clock unavailable")

    monkeypatch.setattr(progress.time, "monotonic", bad_clock)
    with progress.writer_progress():
        progress.report("preflight", force=True)


def test_context_is_reset_after_failure_and_nested_scope_keeps_outer_clock(monkeypatch, capsys):
    import al_writer_progress as progress

    clock = iter([1.0, 2.0, 3.0, 4.0])
    monkeypatch.setattr(progress.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="original failure"):
        with progress.writer_progress():
            progress.report("preflight", force=True)
            with progress.writer_progress():
                progress.report("inventory-current", count=1, force=True)
            progress.report("inventory-stability", count=1, force=True)
            raise RuntimeError("original failure")
    progress.report("writer-publication", force=True)
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 3
    assert "1.0s elapsed" in lines[0]
    assert "2.0s elapsed" in lines[1]
    assert "3.0s elapsed" in lines[2]


def test_diagnostic_does_not_swallow_cancellation_and_context_resets(monkeypatch):
    import al_writer_progress as progress

    class Cancelled:
        def write(self, value):
            raise KeyboardInterrupt()

    monkeypatch.setattr(progress.sys, "stderr", Cancelled())
    with pytest.raises(KeyboardInterrupt):
        with progress.writer_progress():
            progress.report("preflight", force=True)
    progress.report("preflight", force=True)


def test_new_thread_does_not_inherit_enabled_progress(capsys):
    import al_writer_progress as progress

    with progress.writer_progress():
        thread = threading.Thread(target=lambda: progress.report("inventory-current", count=9))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert capsys.readouterr() == ("", "")


def test_repository_snapshot_progress_preserves_data_and_stability(repo, capsys):
    import al_writer_progress as progress

    baseline = facts.Snapshot(repo, None, ["svc"], [])
    capsys.readouterr()
    with progress.writer_progress():
        observed = facts.Snapshot(repo, None, ["svc"], [])
        assert observed.stable()
        (repo / "svc/app.py").write_text("moved = True\n")
        assert not observed.stable()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "enumerating repository membership" in captured.err
    assert "reading current repository inputs" in captured.err
    assert "checking repository snapshot stability" in captured.err
    assert "svc/app.py" not in captured.err and str(repo) not in captured.err
    for name in ["members", "files", "base", "modes", "base_modes", "gaps", "total"]:
        assert getattr(observed, name) == getattr(baseline, name)


def test_scope_rows_are_identical_with_enabled_progress(repo, capsys):
    import al_writer_progress as progress

    baseline = scope.records("HEAD", root=repo)
    capsys.readouterr()
    with progress.writer_progress():
        observed = scope.records("HEAD", root=repo)
    captured = capsys.readouterr()
    assert observed == baseline
    assert captured.out == ""
    assert "reading indexed source metadata" in captured.err
    assert "hashing current worktree members" in captured.err
    assert str(repo) not in captured.err and "svc/app.py" not in captured.err


def test_attribution_preserves_three_observations_and_retention(repo, monkeypatch, capsys):
    import al_writer_progress as progress

    raw = (repo / "svc/app.py").read_bytes()
    rows = [{"path": "svc/app.py", "worktree": {"type": "file", "sha256": hashlib.sha256(raw).hexdigest()}}]
    calls = []
    retained = []
    monkeypatch.setattr(attribution, "_rows", lambda st: calls.append(1) or copy.deepcopy(rows))
    monkeypatch.setattr(attribution.A, "snapshot_bytes", lambda *args: retained.append(args[1]) or {"sha256": hashlib.sha256(args[1]).hexdigest()})
    state = {"session_id": "public-fixture", "run_id": "run", "identity": {}}
    with progress.writer_progress():
        result = attribution.observe(state, retain_content=True)
    captured = capsys.readouterr()
    assert len(calls) == 3 and retained == [raw]
    assert result["files"] == rows
    assert result["contents"]["svc/app.py"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert "observing source for attribution" in captured.err
    assert "confirming source attribution stability" in captured.err
    assert "retaining immutable source evidence" in captured.err
    assert "checking retained source stability" in captured.err
    assert captured.out == "" and "svc/app.py" not in captured.err


def test_attribution_still_refuses_movement(monkeypatch, capsys):
    import al_writer_progress as progress

    calls = []
    monkeypatch.setattr(attribution, "_rows", lambda st: calls.append(1) or [{"path": str(len(calls))}])
    with pytest.raises(SystemExit, match="unstable task attribution observation"):
        with progress.writer_progress():
            attribution.observe({"session_id": "public", "run_id": "run", "identity": {}})
    assert len(calls) == 2 and capsys.readouterr().out == ""
