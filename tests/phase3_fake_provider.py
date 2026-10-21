"""Test-only external-provider executable for the real detached worker path."""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path


def _run(session, *args, expected=0):
    result = subprocess.run(
        [sys.executable, os.environ["PHASE3_PROVIDER_AL_STATE"], "--session", session, *args],
        cwd=os.environ["PHASE3_PROVIDER_REPO"],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"AL {args!r} returned {result.returncode}: {result.stdout}{result.stderr}"
        )
    return result.stdout


def _report(session, action, data):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    ) as stream:
        json.dump(data, stream)
        path = Path(stream.name)
    try:
        return _run(session, "integration", action, "--file", str(path))
    finally:
        path.unlink(missing_ok=True)


def run() -> int:
    repo = Path(os.environ["PHASE3_PROVIDER_REPO"])
    session = os.environ["AL_SESSION_ID"]
    counter = Path(os.environ["PHASE3_PROVIDER_INVOCATIONS"])
    counter.write_text(
        str(int(counter.read_text()) + 1 if counter.exists() else 1), encoding="utf-8"
    )
    argv = sys.argv
    native_id = argv[argv.index("--session-id") + 1]
    uuid.UUID(native_id)

    _report(
        session,
        "task-register",
        {
            "id": "A",
            "title": "Choose storage format",
            "files": ["demo/export.py"],
            "criteria": ["AC-01"],
            "dependencies": [],
        },
    )
    _report(
        session,
        "task-register",
        {
            "id": "B",
            "title": "Independent documentation update",
            "files": ["README.md", "tests/test_phase3_independent.py"],
            "criteria": ["AC-01"],
            "dependencies": [],
        },
    )
    _report(
        session,
        "blocker",
        {
            "id": "architecture",
            "tasks": ["A"],
            "question": "Which storage format should be used?",
            "context": "The approved specification leaves this architecture choice open.",
            "alternatives": ["Keep the current format", "Add an indexed format"],
            "recommendation": "Keep the current format.",
        },
    )
    blocked = subprocess.run(
        [
            sys.executable,
            os.environ["PHASE3_PROVIDER_AL_STATE"],
            "--session",
            session,
            "integration",
            "task-admit",
            "--task",
            "A",
        ],
        cwd=repo,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if blocked.returncode == 0 or "blocked" not in (blocked.stdout + blocked.stderr).lower():
        raise AssertionError("task A was not blocked by its scoped architecture question")
    _run(session, "integration", "task-admit", "--task", "B")
    _report(
        session,
        "agent-register",
        {
            "id": "workerB",
            "name": "B engineer",
            "role": "al-engineer",
            "task": "B",
        },
    )
    _report(
        session,
        "agent-update",
        {
            "id": "workerB",
            "status": "Running",
            "activity": "Writing independent documentation",
        },
    )
    _run(session, "integration", "task-start", "--task", "B")
    _run(
        session,
        "writer",
        "start",
        "--id",
        "writer-B",
        "--role",
        "al-engineer",
        "--files",
        "README.md,tests/test_phase3_independent.py",
        "--task",
        "B",
        "--agent",
        "workerB",
    )
    readme = repo / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nPhase 3 independent worker fixture.\n",
        encoding="utf-8",
    )
    test_file = repo / "tests/test_phase3_independent.py"
    test_file.write_text(
        "from pathlib import Path\n\n"
        "def test_independent_documentation_was_written():\n"
        "    assert 'Phase 3 independent worker fixture.' in Path('README.md').read_text()\n",
        encoding="utf-8",
    )
    _run(session, "writer", "finish", "--id", "writer-B", "--outcome", "success")
    _run(
        session,
        "evidence",
        "plan",
        "--area",
        "B-test",
        "--tier",
        "focused",
        "--scope",
        "README.md,tests/test_phase3_independent.py",
        "--",
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_phase3_independent.py",
    )
    _run(session, "evidence", "run", "--area", "B-test")
    _run(session, "integration", "task-finish", "--task", "B")
    Path(os.environ["PHASE3_PROVIDER_READY"]).write_text(
        "B completed with real pytest evidence", encoding="utf-8"
    )

    source = Path(os.environ["AL_RUNTIME_DIR"]) / f"{session}.json"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = json.loads(source.read_text(encoding="utf-8"))
        outbox = state.get("integration", {}).get("outbox", [])
        if any(row.get("kind") == "decision.acknowledged" for row in outbox):
            break
        time.sleep(0.1)
    else:
        raise TimeoutError(
            "real worker adapter did not deliver the Decision Registry acknowledgement"
        )

    _run(session, "integration", "task-admit", "--task", "A")
    Path(os.environ["PHASE3_PROVIDER_FINISHED"]).write_text(
        "AL acknowledgement observed", encoding="utf-8"
    )
    print(json.dumps({"type": "system", "session_id": native_id}), flush=True)
    print(json.dumps({"type": "result", "session_id": native_id, "is_error": False}), flush=True)
    return 0


def main() -> int:
    try:
        return run()
    except Exception:
        Path(os.environ["PHASE3_PROVIDER_ERROR"]).write_text(
            traceback.format_exc(), encoding="utf-8"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
