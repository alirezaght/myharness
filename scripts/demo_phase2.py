"""Reproducible Phase 2 demo: the wrong-connection scenario on throwaway state.

A task's design draws a connection the wrong way; the developer comments on it in the dashboard;
the harness fixes it through the redesign loop (the developer's verdicts are scripted); the
developer resolves the comment and only then can Approve & Implement be used. The harness CLI,
the redesign tool and the dashboard run as subprocesses in a temporary directory (its own sample
git repository and HARNESS_HOME); the dashboard is driven over HTTP on 127.0.0.1. Every command,
request and response is printed and normalised like the Phase 1 demo (scripts/demo.py), plus the
dashboard port. Every synthetic answer, design entry, reason and comment starts with `[demo]`.

Usage: `uv run python scripts/demo_phase2.py` or `python scripts/demo_phase2.py`.
HARNESS_PYTHON selects the interpreter that runs the harness (default: the current one);
KEEP_DEMO=1 keeps the temporary directory.
"""

import http.client
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location(
    "harness_phase1_demo", Path(__file__).with_name("demo.py")
)
phase1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(phase1)
Demo, DemoError, ident, normalise = phase1.Demo, phase1.DemoError, phase1.ident, phase1.normalise

PORT = re.compile(r"127\.0\.0\.1:\d+")
URL = re.compile(r"http://127\.0\.0\.1:(\d+)/")
WRONG_DESIGN = (
    "[demo] The exporter writes the item rows\n"
    "component Exporter\ncomponent Item Store\nconnect Item Store -> Exporter: writes rows"
)
FIXED_DESIGN = (
    "[demo] The exporter writes the item rows\n"
    "component Exporter\ncomponent Item Store\nconnect Exporter -> Item Store: writes rows"
)
ANSWERS = (
    "[demo] Standard library only, no new packages",
    "[demo] pytest with tests/ at the repository root",
    "[demo] The exporter writes items.csv from the item store",
    "[demo] Existing exports stay unchanged",
    "[demo] items.csv has one row per item",
    "[demo] A unit test reads items.csv back",
)
OTHER_SECTIONS = (
    "responsibilities",
    "interfaces",
    "interactions",
    "data_flow",
    "configuration",
    "failure_handling",
    "required_behaviour",
)


def normalise_phase2(text: str, demo_dir: str | Path) -> str:
    """The Phase 1 rules, then the dashboard's loopback port."""
    text = re.sub(r"\bexec-[0-9a-f]{24}\b", "exec-xxxxxxxxxxxxxxxxxxxxxxxx", text)
    return PORT.sub("127.0.0.1:<port>", normalise(text, demo_dir))


def _dumps(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def selection(data: dict) -> dict:
    """The stable part of GET /api/dashboard that the demo shows."""
    task = data["task"]
    current = task["diagrams"]["current"]
    items = [i for scene in current["diagrams"] for i in scene["items"]] if current else []
    final = task["approval_center"]["final"]
    return {
        "diagram_version": current["version"] if current else None,
        "connections": [
            {"id": i["id"], "label": i["label"]} for i in items if i["kind"] == "connection"
        ],
        "comments": task["comments"]["counts"],
        "final": {k: final[k] for k in ("phase", "enabled", "blockers", "comments")},
    }


class Phase2Demo(Demo):
    def __init__(self, demo_dir: Path, python: str) -> None:
        super().__init__(demo_dir, python)
        native_bin = demo_dir / "native-bin"
        native_bin.mkdir(parents=True)
        from tests.al_launcher import bootstrap_alctl
        from tests.native_provider_fixture import install_fake_claude

        install_fake_claude(native_bin)
        self.env["HOME"] = str(demo_dir / "native-home")
        self.env["PATH"] = os.pathsep.join((str(native_bin), self.env.get("PATH", "")))
        self.env["HARNESS_FIXTURE_CALL_COUNT"] = str(demo_dir / "native-calls.txt")
        self.env["HARNESS_DB"] = str(demo_dir / "harness-home" / "state.db")
        bootstrap_alctl(demo_dir, native_bin / "alctl", self.env)
        self.dashboard: subprocess.Popen | None = None
        self.port: int | None = None

    def make_sample_repo(self) -> None:
        for rel, text in phase1.SAMPLE_FILES.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[demo] initial sample repository")
        package = Path(__file__).resolve().parents[1] / "al" / "claude" / "skills" / "al"
        shutil.copytree(package, self.repo / ".claude" / "skills" / "al")
        role_source = package.parent.parent / "agents"
        role_target = self.repo / ".claude" / "agents" / "al"
        role_target.mkdir(parents=True, exist_ok=True)
        for role in role_source.glob("al-*.md"):
            shutil.copy2(role, role_target / role.name)
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\n.claude/\n", encoding="utf-8")

    def wait_for_execution(self, task: str) -> dict:
        from harness.db import connect
        from harness.execution_store import latest_execution

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            conn = connect(self.env["HARNESS_DB"])
            try:
                execution = latest_execution(conn, task)
            finally:
                conn.close()
            if execution and execution["status"] not in {
                "Reserved",
                "WorkerOwned",
                "Launching",
                "Running",
            }:
                if execution["status"] == "Completed":
                    raise DemoError("the provider fixture cannot complete AL work")
                return execution
            time.sleep(0.05)
        raise DemoError("the isolated approval-bound worker did not finish within ten seconds")

    def emit(self, text: str) -> None:
        sys.stdout.write(normalise_phase2(text, self.demo_dir))
        sys.stdout.flush()

    def module(self, name: str, *args: str, expect: int = 0) -> str:
        self.emit(f"$ python -m harness.{name} {shlex.join(args)}\n")
        proc = subprocess.run(
            [self.python, "-m", f"harness.{name}", *args],
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
            raise DemoError(f"harness.{name} {shlex.join(args)}: exit {proc.returncode}")
        return proc.stdout

    def start_dashboard(self, task: str) -> None:
        self.emit(f"$ python -m harness.dashboard {task} --no-browser &\n")
        self.dashboard = subprocess.Popen(
            [self.python, "-m", "harness.dashboard", task, "--no-browser"],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        line = self.dashboard.stdout.readline()
        found = URL.search(line)
        if not line or found is None or self.dashboard.poll() is not None:
            raise DemoError(f"the dashboard did not start: {line!r}")
        self.port = int(found.group(1))
        self.emit(line)

    def http(self, method: str, path: str, body: dict | None = None, show=None) -> tuple:
        """One request to the dashboard; prints it and its (selected) JSON response."""
        if self.port is None:
            raise DemoError("the dashboard is not running")
        origin = f"http://127.0.0.1:{self.port}"
        headers, raw = {}, None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers = {"Origin": origin, "Content-Type": "application/json"}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=raw, headers=headers)
            response = conn.getresponse()
            status, data = response.status, json.loads(response.read())
        finally:
            conn.close()
        sent = f" {_dumps(body)}" if body is not None else ""
        self.emit(f"$ {method} {path}{sent}\n")
        shown = show(data) if show else data
        if isinstance(shown, list):
            self.emit(f"{status}\n" + "".join(f"  {line}\n" for line in shown))
        else:
            self.emit(f"{status} {_dumps(shown)}\n")
        return status, data

    def dashboard_state(self, task: str) -> dict:
        status, data = self.http("GET", f"/api/dashboard?task={task}", show=selection)
        if status != 200:
            raise DemoError(f"GET /api/dashboard: {status}")
        return data["task"]

    def stop_dashboard(self) -> None:
        proc, self.dashboard = self.dashboard, None
        self.emit("$ kill -TERM <dashboard>\n")
        proc.send_signal(signal.SIGTERM)
        rest, _ = proc.communicate(timeout=10)
        self.emit(rest)
        if proc.returncode != 0:
            raise DemoError(f"the dashboard exited {proc.returncode}")

    def kill_dashboard(self) -> None:
        proc, self.dashboard = self.dashboard, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        proc.stdout.close()


def _expect(status: int, wanted: int, what: str) -> None:
    if status != wanted:
        raise DemoError(f"{what}: {status}, expected {wanted}")


def _tokens(task: dict) -> dict:
    final = task["approval_center"]["final"]
    return {"task": task["id"]} | {k: final[k] for k in ("version", "spec_hash", "review_hash")}


def _compared(data: dict) -> list[str]:
    changes = [f"{c['change']}: {c['kind']} {c['label']} ({c['id']})" for c in data["changes"]]
    return changes + [f"marks {side}: {_dumps(data['marks'][side])}" for side in ("from", "to")]


def _approved(data: dict) -> list[str]:
    execution = data["execution"]
    return [
        f"created: {_dumps(data['created'])}",
        *data["output"],
        f"execution: {execution['id']} ({execution['status']})",
        f"launch: {data['launch']['status']} (external provider boundary simulated)",
    ]


def build_task(demo: Phase2Demo) -> tuple[str, str]:
    demo.banner("1. Build a task whose design has a wrong connection")
    task = ident(
        r"task (t-[0-9a-f]{8}) created",
        demo.harness("start", "--complexity", "trivial", phase1.SAMPLE_REQUEST),
    )
    demo.harness("investigate", task)
    demo.harness("phase", task, "next")
    for number, answer in enumerate(ANSWERS, 1):
        demo.harness("interview", "next", task)
        demo.harness("interview", "answer", task, str(number), answer)
    demo.harness("interview", "checklist", task)
    demo.harness("phase", task, "next")
    wrong = ident(r"E(\d+)", demo.harness("design", "add", task, "components", WRONG_DESIGN))
    for section in OTHER_SECTIONS:
        demo.harness("design", "none", task, section, "--reason", "[demo] Not needed here")
    demo.harness("design", "coverage", task)
    demo.harness("phase", task, "next")
    demo.harness("testplan", "add", task, "unit", "[demo] A unit test reads items.csv back")
    demo.harness("testplan", "approve", task)
    demo.harness("phase", task, "next")
    demo.harness("review", task)
    demo.harness("phase", task, "next")
    return task, wrong


def walkthrough(demo: Phase2Demo) -> None:
    task, wrong_entry = build_task(demo)

    demo.banner("2. Open the dashboard")
    demo.start_dashboard(task)
    state = demo.dashboard_state(task)
    items = [i for s in state["diagrams"]["current"]["diagrams"] for i in s["items"]]
    wrong = next(i["id"] for i in items if i["kind"] == "connection")

    demo.banner("3. Comment on the wrong connection")
    body = {"task": task, "version": 1, "element": wrong, "kind": "correction",
            "text": "[demo] The arrow points the wrong way: the exporter writes the rows",
            "request_id": "demo-comment-0001"}  # fmt: skip
    status, data = demo.http("POST", "/api/comments", body)
    _expect(status, 201, "POST /api/comments")
    comment = data["comment"]["id"]
    state = demo.dashboard_state(task)
    status, _ = demo.http("POST", "/api/approve", _tokens(state))
    _expect(status, 409, "POST /api/approve")

    demo.banner("4. The harness fixes it (the developer's verdicts are scripted)")
    reason = f"[demo] comment #{comment}"
    demo.module("redesign", task)
    demo.harness("phase", task, "back", "design", "--reason", reason)
    out = demo.harness("design", "propose", task, "components", FIXED_DESIGN, "--rationale", reason)
    entry = ident(r"E(\d+) pending", out)
    demo.harness("design", "decide", task, entry, "accept")
    demo.harness("design", "withdraw", task, wrong_entry)
    demo.module("redesign", "address", task, str(comment))
    for _ in range(3):
        demo.harness("phase", task, "next")

    demo.banner("5. Review the revision")
    status, _ = demo.http("GET", f"/api/diagrams/compare?from=1&to=2&task={task}", show=_compared)
    _expect(status, 200, "GET /api/diagrams/compare")
    demo.dashboard_state(task)

    demo.banner("6. Resolve, then Approve & Implement")
    status, _ = demo.http("POST", "/api/comments/resolve", {"task": task, "comment": comment})
    _expect(status, 200, "POST /api/comments/resolve")
    state = demo.dashboard_state(task)
    status, _ = demo.http("POST", "/api/approve", _tokens(state), show=_approved)
    _expect(status, 201, "POST /api/approve")
    execution = demo.wait_for_execution(task)
    if execution["status"] != "Interrupted":
        raise DemoError(f"provider fixture left AL as {execution['status']}; expected Interrupted")
    if Path(demo.env["HARNESS_FIXTURE_CALL_COUNT"]).read_text(encoding="utf-8") != "1":
        raise DemoError("the isolated external executable should be launched exactly once")

    demo.banner("7. Stop the dashboard; the CLI sees the approval")
    demo.stop_dashboard()
    demo.harness("show", task)
    demo.harness("spec", "verify", task)
    demo.harness("resume", task)


def main() -> int:
    python = os.environ.get("HARNESS_PYTHON") or sys.executable
    if shutil.which("git") is None:
        print("demo: git is required", file=sys.stderr)
        return 2
    demo_dir = Path(tempfile.mkdtemp(prefix="harness-demo-phase2."))
    demo = Phase2Demo(demo_dir, python)
    try:
        demo.emit(
            "# AL Engineering Harness, Phase 2 demo (throwaway state; external provider boundary "
            "simulated)\n"
        )
        demo.make_sample_repo()
        walkthrough(demo)
    except (DemoError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    finally:
        demo.kill_dashboard()
        if os.environ.get("KEEP_DEMO") == "1":
            print(f"demo state kept in {demo_dir}", file=sys.stderr)
        else:
            shutil.rmtree(demo_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
