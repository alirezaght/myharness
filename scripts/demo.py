"""Reproducible Phase 1 demo: one task from `harness start` to `harness approve` on throwaway state.

Runs the harness CLI as subprocesses inside a temporary directory (its own sample git repository
and HARNESS_HOME), prints every command and its merged output, and normalises run-specific values
(paths, timestamps, hashes, revisions, task ids) so the transcript is byte-stable. Every synthetic
answer, recommendation, design entry and scenario starts with `[demo]`.

Usage: `uv run python scripts/demo.py` or `python scripts/demo.py`.
HARNESS_PYTHON selects the interpreter that runs the harness (default: the current one);
KEEP_DEMO=1 keeps the temporary directory.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_DATE = "2026-01-01T00:00:00+00:00"
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "demo",
    "GIT_AUTHOR_EMAIL": "demo@example.invalid",
    "GIT_AUTHOR_DATE": GIT_DATE,
    "GIT_COMMITTER_NAME": "demo",
    "GIT_COMMITTER_EMAIL": "demo@example.invalid",
    "GIT_COMMITTER_DATE": GIT_DATE,
}
SAMPLE_DECISION = """# DEC-0001 — [demo] Storage engine

- **ID:** DEC-0001
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-01-01
- **Scope:** Persistence
- **Supersedes:** none

## Decision

[demo] Store exports as plain files.

## Rationale

[demo] Files are simple to inspect.

## Impact

[demo] No database is needed for exports.
"""
SAMPLE_FILES = {
    "README.md": "# [demo] sample repository\n",
    "app/__init__.py": "",
    "app/export.py": "def export_rows(rows):\n    return [list(row) for row in rows]\n",
    "docs/decisions/DEC-0001-storage-engine.md": SAMPLE_DECISION,
}

PATTERNS = (
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"),
        "<timestamp>",
    ),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<date>"),
    (re.compile(r"sha256:[0-9a-f]{64}\b"), "sha256:<hash>"),
    (re.compile(r"\b[0-9a-f]{40}\b"), "<rev>"),
    (re.compile(r"\bt-[0-9a-f]{8}\b"), "t-xxxxxxxx"),
)


def normalise(text: str, demo_dir: str | Path) -> str:
    """Replace run-specific values; DEC-000N, vN, Q/P/E/S<n>, #<n> and counts stay as they are."""
    raw = str(demo_dir)
    for path in sorted({os.path.realpath(raw), raw}, key=len, reverse=True):
        text = text.replace(path, "<demo>")
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class DemoError(RuntimeError):
    pass


class Demo:
    def __init__(self, demo_dir: Path, python: str) -> None:
        self.demo_dir = demo_dir
        self.repo = demo_dir / "repo"
        self.python = python
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "HARNESS_"))}
        pythonpath = os.pathsep.join(p for p in (str(ROOT), env.get("PYTHONPATH")) if p)
        env.update(GIT_IDENTITY)
        env.update(
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
            HARNESS_HOME=str(demo_dir / "harness-home"),
            PYTHONPATH=pythonpath,
            PYTHONUNBUFFERED="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.env = env

    def emit(self, text: str) -> None:
        sys.stdout.write(normalise(text, self.demo_dir))
        sys.stdout.flush()

    def banner(self, title: str) -> None:
        self.emit(f"\n## {title}\n\n")

    def harness(self, *args: str, expect: int = 0) -> str:
        self.emit(f"$ harness {shlex.join(args)}\n")
        proc = subprocess.run(
            [self.python, "-m", "harness.cli", *args],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.emit(proc.stdout)
        if proc.returncode != 0:
            self.emit(f"(exit {proc.returncode})\n")
        if proc.returncode != expect:
            raise DemoError(
                f"harness {shlex.join(args)}: exit {proc.returncode}, expected {expect}"
            )
        return proc.stdout

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True)

    def make_sample_repo(self) -> None:
        for rel, text in SAMPLE_FILES.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[demo] initial sample repository")


def ident(pattern: str, output: str) -> str:
    match = re.search(pattern, output)
    if match is None:
        raise DemoError(f"no match for {pattern!r} in {output!r}")
    return match.group(1)


def walkthrough(demo: Demo) -> None:
    demo.banner("1. Start a task and override its complexity")
    task = ident(
        r"task (t-[0-9a-f]{8}) created", demo.harness("start", "[demo] Add a CSV export of items")
    )
    demo.harness("classify", task, "trivial")

    demo.banner("2. Read-only investigation of the sample repository")
    demo.harness("investigate", task)
    demo.harness("phase", task, "next")

    demo.banner("3. Interview: answer, unknown, recommendation and decision")
    demo.harness("interview", "next", task)
    demo.harness("interview", "answer", task, "1", "[demo] Standard library only, no new packages")
    demo.harness("interview", "next", task)
    demo.harness(
        "interview", "answer", task, "2", "[demo] pytest with tests/ at the repository root"
    )
    demo.harness("interview", "next", task)
    demo.harness("interview", "unknown", task, "3")
    demo.harness("interview", "next", task)
    proposal = ident(
        r"P(\d+) pending",
        demo.harness(
            "interview",
            "propose",
            task,
            "4",
            "--title",
            "[demo] Existing exports stay unchanged",
            "--recommendation",
            "[demo] Keep export_rows as it is and add the CSV writer next to it",
            "--rationale",
            "[demo] The change is additive, so no caller has to change",
            "--impact",
            "[demo] export_rows keeps its current behaviour",
        ),
    )
    demo.harness("interview", "decide", task, proposal, "accept")
    demo.harness("interview", "checklist", task)
    demo.harness("interview", "next", task)

    demo.banner("Interrupt: the session ends here, the asked question stays unanswered")
    demo.emit("(a later session finds the task and continues where it stopped)\n\n")
    demo.harness("resume")
    demo.harness("resume", task)
    demo.harness("interview", "next", task)

    demo.banner("4. Finish the interview")
    demo.harness("interview", "answer", task, "5", "[demo] items.csv has one row per item")
    demo.harness("interview", "next", task)
    demo.harness("interview", "answer", task, "6", "[demo] A unit test reads items.csv back")
    demo.harness("interview", "next", task)
    demo.harness("interview", "answer", task, "3", "[demo] Write items.csv with a header row")
    demo.harness("interview", "checklist", task)
    demo.harness("phase", task, "next")

    demo.banner("5. Architecture design with a delegated detail")
    entry = ident(
        r"E(\d+) pending",
        demo.harness(
            "design",
            "propose",
            task,
            "components",
            "[demo] app/export.py gains write_csv",
            "--rationale",
            "[demo] Keeps all export code in one module",
        ),
    )
    demo.harness("design", "decide", task, entry, "accept")
    demo.harness(
        "design", "add", task, "responsibilities", "[demo] write_csv writes one row per item"
    )
    demo.harness(
        "design", "add", "--delegated", task, "interfaces", "[demo] Exact write_csv signature"
    )
    for section in (
        "interactions",
        "data_flow",
        "configuration",
        "failure_handling",
        "required_behaviour",
    ):
        demo.harness(
            "design", "none", task, section, "--reason", "[demo] Not needed for one CSV file"
        )
    demo.harness("design", "coverage", task)
    demo.harness("phase", task, "next")

    demo.banner("6. Approval is refused before the approval phase")
    demo.harness("approve", task, expect=1)

    demo.banner("7. Test plan")
    scenario = ident(
        r"S(\d+) pending",
        demo.harness(
            "testplan",
            "propose",
            task,
            "unit",
            "[demo] write_csv writes one row per item",
            "--rationale",
            "[demo] Covers the acceptance criterion directly",
            "--covers",
            "Q5",
        ),
    )
    demo.harness("testplan", "decide", task, scenario, "accept")
    demo.harness("testplan", "review", task)
    demo.harness("testplan", "approve", task)
    demo.harness("phase", task, "next")

    demo.banner("8. Final design review")
    demo.harness("review", task)
    demo.harness("phase", task, "next")

    demo.banner("9. Commit the recorded decision, then Approve & Implement")
    demo.emit('$ git commit -m "[demo] record decisions"\n')
    demo.git("add", "docs/decisions")
    demo.git("commit", "-q", "-m", "[demo] record decisions")
    demo.harness("approve", task)
    demo.harness("show", task)
    demo.harness("spec", "verify", task)
    spec = demo.repo / "docs" / "specs" / task / "v1.md"
    demo.emit(f"$ cat docs/specs/{task}/v1.md\n")
    demo.emit(spec.read_text(encoding="utf-8"))
    demo.harness("resume", task)


def main() -> int:
    python = os.environ.get("HARNESS_PYTHON") or sys.executable
    if shutil.which("git") is None:
        print("demo: git is required", file=sys.stderr)
        return 2
    demo_dir = Path(tempfile.mkdtemp(prefix="harness-demo."))
    try:
        demo = Demo(demo_dir, python)
        demo.emit("# AL Engineering Harness, Phase 1 demo (throwaway state under <demo>)\n")
        demo.make_sample_repo()
        walkthrough(demo)
    except (DemoError, subprocess.CalledProcessError) as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if os.environ.get("KEEP_DEMO") == "1":
            print(f"demo state kept in {demo_dir}", file=sys.stderr)
        else:
            shutil.rmtree(demo_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
