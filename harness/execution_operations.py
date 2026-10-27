"""Native tools enter here; each request rechecks sealed and task authority."""

import json
import os
import secrets
import subprocess
import threading
import time
from contextlib import closing
from pathlib import Path

from harness.command_safety import safe_environment
from harness.controller import HarnessController
from harness.db import connect, utcnow
from harness.execution_artifacts import ArtifactStore
from harness.execution_authority import require_execution_authority
from harness.execution_check_channel import CheckChannel
from harness.execution_contract import canonical, digest
from harness.execution_files import apply_patch, read_file, relative_path
from harness.execution_handoff import durable_file
from harness.execution_hosts import al_argv, al_source
from harness.execution_isolation import operation_popen, prepare_operation_isolation
from harness.execution_logs import redact_value
from harness.execution_processes import ProcessManager, bounded_capture
from harness.execution_store import get_execution

READ_COMMANDS = {"show", "status", "review-status", "next", "help", "routing-audit"}
COMMANDS = READ_COMMANDS | {
    "artifact",
    "preflight",
    "classify",
    "checkpoint",
    "phase",
    "planning",
    "pseudocode",
    "writer",
    "evidence",
    "gate",
    "gates",
    "review",
    "review-packet",
    "review-report",
    "review-compare",
    "review-compare-report",
    "finding",
    "findings",
    "correction",
    "rework",
    "fanout",
    "overlap",
    "handoff",
    "integration",
    "publication",
    "acceptance",
    "rework-recovery",
    "stopped",
    "completed",
    "spec",
    "task",
    "conflict",
    "amendment",
    "decision",
    "escape",
    "aux-run",
    "spec-gate",
    "ownership",
    "snapshot",
    "agent-failed",
    "model-unavailable",
    "model-retry",
    "retry",
    "approved",
    "review-start",
    "review-fresh",
    "review-result",
    "review-compare-start",
    "notes-triage",
    "oscillation",
    "conflict-resolved",
    "rework-resolve",
    "recovery",
    "check-reconcile",
    "behavior",
}


class OperationController:
    def __init__(self, database, execution, envelope, package, initialization, registry, directory):
        self.database, self.execution_id = Path(database), execution["id"]
        self.envelope, self.package, self.initialization = envelope, package, initialization
        self.registry = registry
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.directory / "drafts")
        self.processes = ProcessManager()
        self.check_channels = []
        self.generated = {}
        self.lock = threading.RLock()
        self.assignment_path = self.directory / "assignments.json"
        self.assignments = (
            json.loads(self.assignment_path.read_bytes()) if self.assignment_path.exists() else {}
        )

    def state(self, execution):
        path = al_source(execution, self.envelope)
        if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("bounded ordinary AL source required")
        value = json.loads(path.read_bytes())
        if value.get("integration", {}).get("binding", {}).get("execution_id") != self.execution_id:
            raise ValueError("AL source belongs to another execution")
        return value

    def authority(self, controller, principal, *, operation="admit"):
        execution = get_execution(controller.conn, self.execution_id)
        if (
            principal["binding"] != execution["binding"]
            or principal["policy"] != self.registry.policy
        ):
            raise ValueError("native principal approval or policy is stale")
        require_execution_authority(controller, self.execution_id, operation=operation)
        state = self.state(execution)
        if state.get("run_id") != execution["al_run_id"]:
            raise ValueError("native principal AL run is stale")
        return execution, state

    def admitted(self, controller, principal, task_id, revision, *, paths=()):
        execution, state = self.authority(controller, principal)
        integration = state["integration"]
        task = integration["tasks"].get(task_id)
        assignment = self.assignments.get(principal["thread_id"])
        if not principal["writable"] or not assignment or assignment["task"] != task_id:
            raise ValueError("native principal has no writable task assignment")
        if not task or state["status"] != "RUNNING" or task["status"] != "Running":
            raise ValueError("task is not currently running")
        expected = digest(
            {
                "task": task_id,
                "revision": task["revision"],
                "graph_revision": integration["graph_revision"],
                "amendments": [
                    a["artifact"]["sha256"]
                    for a in state["amendments"]
                    if task_id in a.get("affected_tasks", [])
                ],
            }
        )
        if revision != expected or assignment["revision"] != expected:
            raise ValueError("task assignment revision is stale")
        if task.get("admission") != {
            "revision": task["revision"],
            "graph_revision": integration["graph_revision"],
        } or any(integration["tasks"][d]["status"] != "Completed" for d in task["dependencies"]):
            raise ValueError("task lacks current dependency admission")
        if any(
            b["state"] != "Resolved" and task_id in b.get("affected", b["tasks"])
            for b in integration["blockers"].values()
        ):
            raise ValueError("task is blocked")
        if any(
            task_id in r["tasks"]
            and (r["state"] != "Validated" or r["graph_revision"] != integration["graph_revision"])
            for r in integration["revalidations"].values()
        ):
            raise ValueError("task requires current revalidation")
        writers = [
            w
            for w in state["writers"].values()
            if w.get("task") == task_id
            and w.get("agent") == principal["thread_id"]
            and w["state"] == "ACTIVE"
        ]
        if len(writers) != 1 or writers[0]["role"] != principal["role"]:
            raise ValueError("task requires this native principal's active writer")
        for path in paths:
            relative_path(path)
            if not all(
                any(path == p or path.startswith(p + "/") for p in scope)
                for scope in (task["files"], writers[0]["files"])
            ):
                raise ValueError("operation exceeds admitted writer files")
        return execution, state, writers[0]["files"]

    def profile(self, writable=(), *, al=False):
        root = Path(self.envelope["repository"])
        execution_dir = Path(self.envelope["database"]).parent
        profile_dir = self.directory / ("sandbox-" + secrets.token_hex(8))
        return prepare_operation_isolation(
            root,
            writable=writable,
            protected=[execution_dir, self.initialization["pin_root"]],
            hidden=[
                self.registry.directory,
                self.directory.parent / "native-profiles",
                os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
                os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")),
                *([] if al else [root / n for n in (".codex", ".claude", ".agents")]),
            ],
            directory=profile_dir,
            session_directory=(str(Path(self.initialization["source"]).parent) if al else None),
            coordination_lock=(self.initialization["coordination_lock"] if al else None),
        )

    def dispatch(self, principal, name, arguments):
        if name == "await_assignment":
            return self.await_assignment(principal, arguments.get("timeout", 1))
        with self.lock, closing(connect(self.database)) as conn:
            controller = HarnessController(conn)
            audit = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO operation_audit VALUES (?,?,?,?,?,?)",
                (
                    audit,
                    self.execution_id,
                    canonical(
                        redact_value(
                            {
                                "principal": principal["thread_id"],
                                "tool": name,
                                "arguments": arguments,
                            }
                        )
                    ),
                    "guarded",
                    None,
                    utcnow(),
                ),
            )
            try:
                mode = "delivery" if name == "delivery_operation" else "admit"
                args = arguments.get("arguments") or [None]
                if name == "al_command" and args[0] == "publication":
                    mode = "delivery"
                if name in {
                    "read_file",
                    "poll_process",
                    "cancel_process",
                    "diagnostic",
                    "submit_output",
                } or (name == "al_command" and args[0] in READ_COMMANDS):
                    mode = "read"
                _, state = self.authority(controller, principal, operation=mode)
                result = self._dispatch(controller, principal, name, arguments, state)
            except (ValueError, OSError, LookupError, subprocess.SubprocessError) as error:
                conn.execute(
                    "UPDATE operation_audit SET outcome=? WHERE id=?",
                    (canonical({"error": str(error)[:2048]}), audit),
                )
                raise ValueError(str(error)) from None
            result = redact_value(result)
            conn.execute(
                "UPDATE operation_audit SET outcome=? WHERE id=?", (canonical(result), audit)
            )
            return result

    def _dispatch(self, controller, principal, name, a, state):
        root, owner = self.envelope["repository"], principal["thread_id"]
        if name == "read_file":
            if a["path"] == "harness:planning-adoptions":
                from harness.execution_adoption import adoptions

                return {
                    "adoptions": adoptions(controller, self.execution_id),
                    "authority": "REFERENCE_ONLY_CURRENT_HARNESS_ARCHITECT_REVALIDATION_REQUIRED",
                }
            if a["path"] == "harness:repository-audit":
                from harness.repository_audit import audit

                execution = get_execution(controller.conn, self.execution_id)
                package = json.loads(
                    (Path(execution["handoff_path"]).parent / "task-package.json").read_bytes()
                )
                raw = canonical(audit(root, package["original_request"])).encode()
                offset, limit = a.get("offset", 0), a.get("limit", 16384)
                if (
                    type(offset) is not int
                    or type(limit) is not int
                    or offset < 0
                    or not 1 <= limit <= 65536
                ):
                    raise ValueError("bounded repository audit cursor required")
                return {
                    "text": raw[offset : offset + limit].decode(errors="replace"),
                    "offset": offset,
                    "size": len(raw),
                    "truncated": offset + limit < len(raw),
                    "sha256": digest(raw),
                    "resource": a["path"],
                }
            if a["path"].startswith("harness:skill"):
                from harness.pinned_resources import read_skill

                return read_skill(
                    self.initialization,
                    self.envelope["host"],
                    a["path"],
                    offset=a.get("offset", 0),
                    limit=a.get("limit", 16384),
                )
            if a["path"] == "harness:outputs":
                if principal["role"] != "orchestrator":
                    raise ValueError("only orchestrator may list all agent report outputs")
                return self.artifacts.outputs()
            fixed = {
                "harness:handoff": Path(self.envelope["database"]).parent
                / "executions"
                / self.execution_id
                / "handoff.json",
                "harness:task-package": Path(
                    self.initialization["source"]
                ).parent,  # replaced below
                "harness:al-state": Path(self.initialization["source"]),
                "harness:skill": Path(self.initialization["pin_root"])
                / "payload"
                / self.envelope["host"]
                / "skills/al/SKILL.md",
            }
            execution = get_execution(controller.conn, self.execution_id)
            fixed["harness:handoff"] = Path(execution["handoff_path"])
            fixed["harness:task-package"] = (
                Path(execution["handoff_path"]).parent / "task-package.json"
            )
            if a["path"] in fixed:
                path = fixed[a["path"]]
                if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError("invalid fixed Harness document")
                offset, limit = a.get("offset", 0), a.get("limit", 16384)
                if not 0 <= offset or not 1 <= limit <= 65536:
                    raise ValueError("bounded Harness document cursor required")
                with path.open("rb") as stream:
                    stream.seek(offset)
                    raw = stream.read(limit + 1)
                return {
                    "text": raw[:limit].decode(errors="replace"),
                    "truncated": len(raw) > limit,
                    "offset": offset,
                }
            if a["path"].startswith("artifact:"):
                _, artifact, sha = a["path"].split(":", 2)
                return self.artifacts.read(
                    artifact, sha, offset=a.get("offset", 0), limit=a.get("limit", 16384)
                )
            return read_file(
                root, a["path"], offset=a.get("offset", 0), limit=a.get("limit", 16384)
            )
        if name == "write_artifact":
            return self.artifacts.write_artifact(
                principal, current_graph_revision=state["integration"]["graph_revision"], **a
            )
        if name == "submit_output":
            return self.artifacts.submit_output(
                principal, current_graph_revision=state["integration"]["graph_revision"], **a
            )
        if name == "apply_patch":

            def authorize(paths):
                self.admitted(
                    controller, principal, a["task_id"], a["expected_revision"], paths=paths
                )

            return apply_patch(root, a["patch"], authorize)
        if name == "run_command":
            from harness.command_safety import classify_command, git_argv

            cwd = Path(root) / a["cwd"]
            if not cwd.resolve(strict=True).is_relative_to(Path(root)):
                raise ValueError("operation cwd escapes repository")
            decision = classify_command({"argv": a["argv"], "cwd": str(cwd)})
            writable = ()
            if decision["decision"] != "read_only":
                _, _, writable = self.admitted(
                    controller, principal, a["task_id"], a["expected_revision"]
                )
            argv = a["argv"]
            if decision["decision"] == "read_only" and argv[0] == "git":
                args = list(argv[1:])
                if args[0] in {"log", "show", "diff"}:
                    args[1:1] = ["--no-ext-diff", "--no-textconv"]
                argv = git_argv(args)
            return self.processes.start(
                owner,
                self.profile(writable),
                argv,
                str(cwd),
                task=(a["task_id"] if writable else None),
                revision=a["expected_revision"],
                timeout=a.get("timeout", 30),
            )
        if name in {"poll_process", "cancel_process"}:
            record = self.processes._owned(owner, a["handle"])
            if record["task"]:
                self.admitted(controller, principal, record["task"], record["revision"])
            if name == "poll_process":
                result = self.processes.poll(owner, a["handle"], limit=a.get("limit", 16384))
                if result["status"] != "Running" and a["handle"] in self.generated:
                    result["generated"] = self.capture_outputs(self.generated.pop(a["handle"]))
                return result
            return self.processes.cancel(owner, a["handle"])
        if name == "al_command":
            return self.al_command(controller, principal, a, state)
        if name == "diagnostic":
            request = a["request"]
            if set(request) <= {"kind", "path"} and request.get("kind") in {
                "source",
                "log",
                "config",
            }:
                return read_file(root, request["path"])
            if request == {"kind": "ci"}:
                from harness.delivery_store import delivery_view

                return delivery_view(controller.conn, self.execution_id)
            raise ValueError(
                "native diagnostics require a confined file or configured CI capability"
            )
        if name == "delivery_operation":
            if a["request_id"] != self.execution_id:
                raise ValueError("delivery capability belongs to another execution")
            if a["operation"] == "status":
                from harness.delivery_store import delivery_view

                return delivery_view(controller.conn, self.execution_id)
            if principal["role"] != "orchestrator":
                raise ValueError("only orchestrator may request configured delivery operations")
            if a["operation"] == "poll":
                from harness.delivery_monitor import poll_delivery

                return poll_delivery(controller, self.execution_id)
            from harness.delivery import publish_delivery, reconcile_delivery

            return (publish_delivery if a["operation"] == "publish" else reconcile_delivery)(
                controller, self.execution_id
            )
        raise ValueError("unknown operation capability")

    def al_command(self, controller, principal, request, state):
        args = list(request["arguments"])
        if not args or args[0] not in COMMANDS:
            raise ValueError("AL command is not available through the controller")
        if args[0] == "integration" and (
            len(args) < 2 or args[1] in {"decision", "delivery-correction"}
        ):
            raise ValueError("only supervisor delivers developer decisions and corrections")
        child_task = None
        if principal["role"] != "orchestrator" and args[0] not in READ_COMMANDS:
            assignment = self.assignments.get(principal["thread_id"])
            if not assignment or len(args) != 4 or args[:3] != ["evidence", "run", "--area"]:
                raise ValueError(
                    "native workers may run only a registered check for their own admitted writer"
                )
            _, _, files = self.admitted(
                controller, principal, assignment["task"], assignment["revision"]
            )
            spec = state["evidence"]["plan"].get(args[3])
            if not spec or any(
                not any(p == f or p.startswith(f + "/") for f in files) for p in spec["scope"]
            ):
                raise ValueError("check scope exceeds this native writer")
            writer = next(
                w
                for w in state["writers"].values()
                if w.get("agent") == principal["thread_id"] and w["state"] == "ACTIVE"
            )
            args.extend(["--writer", writer["id"]])
            child_task = assignment
        if any(x in {"--repository", "--platform", "--session", "--handoff", "--db"} for x in args):
            raise ValueError("AL origin cannot be overridden")
        # File inputs must be immutable execution artifacts, never model-selected host paths.
        file_flags = {
            "--file",
            "--artifact",
            "--plan",
            "--pseudocode",
            "--package-file",
            "--commands-file",
            "--manifest",
            "--report",
            "--packet",
            "--output",
            "--paths-file",
            "--recovery-file",
            "--claims",
        }
        outputs = []
        for i, value in enumerate(args):
            if value.startswith("artifact:"):
                _, artifact, sha = value.split(":", 2)
                args[i] = str(self.artifacts.resolve(artifact, sha))
            elif value.startswith("output:"):
                name = value.removeprefix("output:")
                if "/" in name or "\\" in name or not name.endswith((".json", ".md")):
                    raise ValueError("simple generated artifact basename required")
                path = (
                    Path(self.initialization["source"]).parent
                    / "harness-generated"
                    / secrets.token_hex(16)
                    / name
                )
                path.parent.mkdir(mode=0o700, parents=True)
                args[i] = str(path)
                outputs.append((name, path))
            elif i and (args[i - 1] in file_flags or value.startswith("--") and "=" in value):
                raise ValueError("AL file inputs require immutable artifact references")
        if args[:2] == ["integration", "agent-register"]:
            i = args.index("--file")
            agent = json.loads(Path(args[i + 1]).read_bytes())
            native = (
                self.registry.enroll(agent["id"])
                if self.envelope["host"] == "codex"
                else self.claude_child(agent["id"])
            )
            if native["role"] != agent["role"]:
                raise ValueError("AL registration must match observed native child role")
            task = state["integration"]["tasks"].get(agent.get("task"))
            if not task and (
                agent.get("task") is not None
                or native.get("writable", agent["role"] in {"al-engineer", "al-builder"})
            ):
                raise ValueError("native child assignment requires registered task")
            if any(
                v["task"] == agent.get("task") and k != agent["id"]
                for k, v in self.assignments.items()
            ):
                raise ValueError("task already assigned to a different native child")
        else:
            agent = None
        command = al_argv(
            self.package["launcher"],
            self.envelope["host"],
            self.envelope["repository"],
            self.envelope["al_session_id"],
            *args,
            existing_run=True,
        )
        environment = safe_environment() | {
            k: v
            for k, v in os.environ.items()
            if k in {"HOME", "PATH", "AL_DISTRIBUTION_STATE", "XDG_DATA_HOME", "XDG_STATE_HOME"}
        }
        environment["HARNESS_AUTHORITY_ROOT"] = str(
            __import__("harness.execution_bridge", fromlist=["bridge_root"]).bridge_root(
                get_execution(controller.conn, self.execution_id)
            )
        )
        profile = self.profile(al=True)
        context_path = Path(profile.policy).parent / "check-context.json"
        durable_file(
            context_path,
            canonical(
                {
                    "handoff": get_execution(controller.conn, self.execution_id)["handoff_path"],
                    "repository": self.envelope["repository"],
                    "temporary": profile.temporary,
                    "al_session": str(Path(self.initialization["source"]).parent),
                    "protected": list(profile.protected),
                    "hidden": list(profile.hidden),
                }
            ).encode(),
        )
        environment["HARNESS_CHECK_CONTEXT"] = str(context_path)
        environment["TMPDIR"] = profile.temporary

        def validate_check():
            with closing(connect(self.database)) as conn:
                _, current = self.authority(HarnessController(conn), principal)
                if current["status"] != "RUNNING":
                    raise ValueError("check authority is no longer running")

        channel = CheckChannel(context_path, validate_check)
        self.check_channels.append(channel)
        environment["HARNESS_CHECK_FD"] = str(channel.descriptor)
        if request.get("background"):
            if agent:
                raise ValueError("native agent registration must complete synchronously")
            result = self.processes.start(
                principal["thread_id"],
                profile,
                command,
                self.envelope["repository"],
                task=child_task["task"] if child_task else None,
                revision=child_task["revision"]
                if child_task
                else str(state["integration"]["graph_revision"]),
                timeout=request.get("timeout", 900),
                environment=environment,
                max_timeout=900,
                pass_fds=(channel.descriptor,),
            )
            channel.spawned()
            self.generated[result["handle"]] = outputs
            return result
        proc = operation_popen(
            profile,
            command,
            cwd=self.envelope["repository"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=(channel.descriptor,),
        )
        channel.spawned()
        try:
            stdout, stderr = bounded_capture(proc)
        finally:
            channel.close()
        if proc.returncode == 0 and agent and agent.get("task"):
            current = self.state(get_execution(controller.conn, self.execution_id))
            task = current["integration"]["tasks"][agent["task"]]
            revision = digest(
                {
                    "task": agent["task"],
                    "revision": task["revision"],
                    "graph_revision": current["integration"]["graph_revision"],
                    "amendments": [
                        a["artifact"]["sha256"]
                        for a in current["amendments"]
                        if agent["task"] in a.get("affected_tasks", [])
                    ],
                }
            )
            self.assignments[agent["id"]] = {"task": agent["task"], "revision": revision}
            temporary = self.assignment_path.with_suffix(".new")
            temporary.write_text(canonical(self.assignments))
            temporary.chmod(0o600)
            os.replace(temporary, self.assignment_path)
        result = {
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace")[:16384],
            "stderr": stderr.decode(errors="replace")[:16384],
        }
        if len(stdout) > 16384:
            result["artifact"] = self.artifacts.capture_generated(
                "al-output.json" if stdout.lstrip().startswith(b"{") else "al-output.md", stdout
            )
        result["generated"] = self.capture_outputs(outputs)
        return result

    def capture_outputs(self, outputs):
        result = []
        session = Path(self.initialization["source"]).parent
        for name, path in outputs:
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(session):
                if path.stat().st_nlink != 1:
                    raise ValueError("generated AL artifact hardlink refused")
                if path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("generated AL artifact exceeds capture bound")
                result.append(self.artifacts.capture_generated(name, path.read_bytes()))
        return result

    def claude_child(self, agent_id):
        from harness.execution_contract import identity

        identity(agent_id)
        row = json.loads(
            (self.registry.directory / ("claude-agent-" + agent_id + ".json")).read_bytes()
        )
        if row["session_id"] != self.registry.root:
            raise ValueError("foreign Claude child")
        return {"role": row["role"]}

    def await_assignment(self, principal, timeout):
        if type(timeout) is not int or not 1 <= timeout <= 30:
            raise ValueError("assignment wait must be between 1 and 30 seconds")
        deadline = time.monotonic() + timeout
        while True:
            with self.lock, closing(connect(self.database)) as conn:
                _, state = self.authority(HarnessController(conn), principal)
                if not principal["writable"]:
                    return {
                        "status": "ReadOnly",
                        "graph_revision": state["integration"]["graph_revision"],
                    }
                value = self.assignments.get(principal["thread_id"])
                if value:
                    return {
                        "status": "Assigned",
                        **value,
                        "graph_revision": state["integration"]["graph_revision"],
                    }
            if time.monotonic() >= deadline:
                return {"status": "Pending"}
            time.sleep(0.1)

    def tick(self):
        def validate(record):
            with closing(connect(self.database)) as conn:
                principal = {
                    "thread_id": record["owner"],
                    "binding": self.registry.binding,
                    "policy": self.registry.policy,
                }
                require_execution_authority(HarnessController(conn), self.execution_id)
                if record["task"]:
                    actual = (
                        self.registry.enroll(record["owner"])
                        if self.envelope["host"] == "codex"
                        else principal | self.claude_child(record["owner"]) | {"writable": True}
                    )
                    self.admitted(
                        HarnessController(conn), actual, record["task"], record["revision"]
                    )

        self.processes.revalidate(validate)

    def close(self):
        self.processes.close()
        for channel in self.check_channels:
            channel.close()
