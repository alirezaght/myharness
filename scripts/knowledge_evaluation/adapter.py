"""Private evaluator adapter over authenticated, unchanged OperationController authority."""

import json
import threading
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from harness.analyzer import analyze
from harness.controller import HarnessController
from harness.db import connect
from harness.decision_suggest import suggest
from harness.execution_mcp import validate_arguments
from harness.knowledge.sources import read_source
from harness.registry import DecisionRegistry
from harness.repository_audit import audit

from .package import BUDGETS, canonical, corpus_receipt, git, sha, task_files

VERSION = "qualified-native-evaluation-adapter-v1"
BASE_TOOLS = {"read_file", "await_assignment", "submit_output"}
KNOWLEDGE_TOOLS = {"knowledge_query", "knowledge_explain"}


class EvaluatorAdapter:
    def __init__(self, operation, package, stage=None):
        self.operation, self.package = operation, package
        self.stage, self.ingestion_refusals, self.completion = stage, [], None
        self.root = Path(package["corpus"]["root"])
        if Path(operation.envelope["repository"]).resolve() != self.root:
            raise ValueError("native execution repository differs from frozen corpus")
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.calls = self.delivered = 0
        self.events, self.answers, self.principals = [], [], {}
        self.supervisor = None
        self.registered = set()
        self.allowed = BASE_TOOLS | (KNOWLEDGE_TOOLS if package["arm"] == "C" else set())
        self.budget = None
        self.output_state = {
            "output_committed": False,
            "acknowledgment_delivered": False,
            "acknowledgment_scope": (
                "MCPConnection.request returned content; client consumption unobserved"
            ),
        }
        self.output_attempted = False
        self.completion_acknowledged = False
        if package["schema_version"] == 2:
            from .budget import Budget

            self.budget = Budget(bool(stage and stage["action"] == "donor"))
        self.revalidate()

    def revalidate(self):
        expected = {k: v for k, v in self.package["corpus"].items() if k != "root"}
        if corpus_receipt(self.root) != expected:
            raise ValueError("frozen corpus changed")
        if self.package["arm"] == "B":
            with closing(connect(self.operation.database)) as conn:
                from .continuity import knowledge_empty

                knowledge_empty(conn)

    def check_time(self):
        if time.monotonic() - self.started >= BUDGETS["wall_seconds"]:
            raise ValueError("evaluation wall budget exceeded")

    def visible(self, content, *, name, principal=None, error=False):
        with self.lock:
            raw = content.encode()
            if len(raw) > BUDGETS["delivered_bytes"] - self.delivered:
                self.events.append({"operation": name, "outcome": "byte-budget-refused"})
                raise ValueError("")  # Empty MCP error delivers zero unaccounted bytes.
            self.delivered += len(raw)
            self.events.append(
                {
                    "operation": name,
                    "principal_id": principal,
                    "visible_text": content,
                    "delivered_bytes": len(raw),
                    "is_error": error,
                    "service_read_bytes": None,
                    "unique_source_bytes": None,
                }
            )
            return content

    def dispatch(self, principal, name, arguments):
        with self.lock:
            try:
                self.check_time()
                if self.budget and self.budget.active is None:
                    raise ValueError("M2 requires accounted MCP transport")
                if not self.budget and self.calls >= BUDGETS["tool_calls"]:
                    raise ValueError("evaluation tool budget exceeded")
                if not self.budget:
                    self.calls += 1
                validate_arguments(name, arguments)
                if name not in self.allowed:
                    raise ValueError("evaluation operation refused")
                with closing(connect(self.operation.database)) as conn:
                    _, current = self.operation.authority(
                        HarnessController(conn), principal, operation="read"
                    )
                    if any(w["state"] == "ACTIVE" for w in current["writers"].values()):
                        raise ValueError("evaluation permits no active writer")
                role = principal["role"]
                if self.budget:
                    self.budget.active.update(
                        principal_id=principal["thread_id"], role=role, tool=name
                    )
                if role not in {"orchestrator", self.package["role"]}:
                    raise ValueError("unexpected native specialized role")
                self.principals[principal["thread_id"]] = {"role": role}
                if role == "orchestrator":
                    self.supervisor = dict(principal)
                else:
                    self.register(principal)
                if self.completion and role != "orchestrator":
                    raise ValueError("completed native participant cannot be revived")
                if name == "read_file":
                    if arguments["path"] == "evaluation:complete-donor":
                        if set(arguments) != {"path"}:
                            raise ValueError("closed completion operation required")
                        from .lifecycle import complete

                        result = complete(self, principal)
                    elif role != self.package["role"]:
                        raise ValueError("orchestrator source prefetch refused")
                    else:
                        result = self.read(principal, arguments)
                elif name in KNOWLEDGE_TOOLS:
                    if role != self.package["role"]:
                        raise ValueError("only actual specialized role may query knowledge")
                    result = self.operation.dispatch(principal, name, arguments)
                elif name == "submit_output":
                    if role != self.package["role"] or arguments["name"] != "answer.md":
                        raise ValueError("only specialized role answer.md output permitted")
                    if self.answers or self.output_attempted:
                        raise ValueError("single immutable answer; no retry")
                    if self.budget:
                        from .lifecycle import reconcile_output

                        self.output_attempted = True
                        self.output_state["output_committed"] = None
                        try:
                            result = self.operation.dispatch(principal, name, arguments)
                        finally:
                            reconciled = reconcile_output(self, principal, arguments)
                            if reconciled is None:
                                self.output_state["output_committed"] = False
                            if reconciled:
                                self.answers.append(
                                    {
                                        "principal": principal["thread_id"],
                                        "role": role,
                                        "artifact": reconciled,
                                    }
                                )
                    else:
                        result = self.operation.dispatch(principal, name, arguments)
                    if not self.budget:
                        self.answers.append(
                            {
                                "principal": principal["thread_id"],
                                "role": role,
                                "text": arguments["content"],
                                "artifact": result,
                            }
                        )
                else:
                    result = self.operation.dispatch(principal, name, arguments)
                    safe_assigned = (
                        result.get("status") == "Assigned"
                        and result.get("handoff", {}).get("status") == "unavailable"
                        and result.get("knowledge_context", {}).get("status") == "unavailable"
                    )
                    if result.get("status") not in {"ReadOnly", "Pending"} and not safe_assigned:
                        raise ValueError("writable/automatic assignment context refused")
                if not self.budget:
                    self.visible(json.dumps(result), name=name, principal=principal["thread_id"])
                return result
            except (ValueError, OSError, LookupError) as error:
                message = str(error)[:2048]
                if not self.budget:
                    self.visible(
                        message, name=name, principal=principal.get("thread_id"), error=True
                    )
                raise ValueError(message) from None

    def read(self, principal, arguments):
        path = arguments["path"]
        if path == "evaluation:donor-report":
            from .continuity import read_donor

            return read_donor(self, principal, arguments)
        if path.startswith("evaluation:"):
            if set(arguments) != {"path"}:
                raise ValueError("evaluation resources have closed, unpaged arguments")
            self.revalidate()
            if path == "evaluation:analyze":
                return analyze(self.root, self.package["prompt"]).to_dict()
            if path == "evaluation:audit":
                return audit(self.root, self.package["prompt"])
            if path == "evaluation:suggest":
                suggestions, notes = suggest(self.package["prompt"], [], [self.root])
                return {"suggestions": [s.to_dict() for s in suggestions], "notes": notes}
            if path == "evaluation:decisions":
                registry = DecisionRegistry(self.root / "docs/decisions")
                records = registry.records()
                history = [r for record in records for r in registry.history(record.id)]
                return json.loads(
                    json.dumps([asdict(r) for r in [*records, *history]], default=str)
                )
            raise ValueError("unknown evaluation resource")
        # No harness:, artifact:, absolute, untracked, policy, environment or alternate resource.
        from harness.execution_files import relative_path

        relative_path(path)
        self.revalidate()
        tracked = git(self.root, "ls-tree", "-rz", "--name-only", "HEAD").decode().split("\0")
        if path not in tracked:
            raise ValueError("source must belong to exact frozen corpus")
        raw = read_source(self.root, path, 1024 * 1024)
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 16384)
        if not 1 <= limit <= 65536:
            raise ValueError("bounded source page required")
        return {
            "text": raw[offset : offset + limit].decode(errors="replace"),
            "offset": offset,
            "next_offset": offset + min(limit, max(0, len(raw) - offset)),
            "truncated": offset + limit < len(raw),
            "source_sha256": sha(raw),
        }

    def register(self, principal):
        """Record only an already authenticated actual role; never invent a native identity."""
        identity = principal["thread_id"]
        if identity in self.registered:
            return
        if self.supervisor is None or self.registered:
            raise ValueError("one observed child requires its observed supervisor")
        with closing(connect(self.operation.database)) as conn:
            _, state = self.operation.authority(HarnessController(conn), self.supervisor)
        value = {"id": identity, "name": "evaluation participant", "role": principal["role"]}
        if principal["role"] == "al-engineer" or self.stage:
            scoped = (
                self.package
                if not self.stage
                else dict(
                    self.package,
                    specification=dict(
                        self.package["specification"], task_files=self.stage["task_files"]
                    ),
                )
            )
            scopes = task_files(scoped)
            task_id = self.stage["al_task_id"] if self.stage else "evaluation"
            self.report_registration(
                "task-register",
                "evaluation-task.json",
                {
                    "id": task_id,
                    "title": self.package["specification"]["request"],
                    "files": scopes,
                    "criteria": self.operation.envelope["task_package"]["criteria"],
                },
                state,
            )
            value["task"] = task_id
            with closing(connect(self.operation.database)) as conn:
                _, state = self.operation.authority(HarnessController(conn), self.supervisor)
        self.report_registration("agent-register", "evaluation-agent.json", value, state)
        self.registered.add(identity)

    def report_registration(self, action, name, value, state):
        if self.budget:
            self.budget.internal_operations += 1
        artifact = self.operation.dispatch(
            self.supervisor,
            "write_artifact",
            {
                "name": name,
                "content": canonical(value),
                "expected_graph_revision": state["integration"]["graph_revision"],
            },
        )
        if self.budget:
            self.budget.internal_operations += 1
        result = self.operation.dispatch(
            self.supervisor,
            "al_command",
            {
                "arguments": [
                    "integration",
                    action,
                    "--file",
                    "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"],
                ]
            },
        )
        if result.get("returncode") != 0:
            raise ValueError("ordinary native evaluation registration refused")
        return result

    def receipt(self):
        return {
            **(
                {
                    "method": self.package["method"],
                    "accounting": self.budget.receipt(),
                    "output_lifecycle": self.output_state,
                    "completion_acknowledgment_delivered": self.completion_acknowledged,
                }
                if self.budget
                else {}
            ),
            "adapter_version": "qualified-native-evaluation-adapter-m2" if self.budget else VERSION,
            "permitted_tools": sorted(self.allowed),
            "tool_calls": self.calls,
            "agent_visible_bytes": self.delivered,
            "principals": self.principals,
            "answers": self.answers,
            "events": self.events,
            "service_read_bytes": None,
            "service_read_coverage": "unavailable: no backend read hook",
            "unique_source_bytes": None,
            "input_tokens": None,
            "output_tokens": None,
            "answer_token_enforcement": "unavailable; bytes are not tokens",
            "actual_native_proof": False,
            "producer_completion": self.completion,
            "B_ingestion_refusals": self.ingestion_refusals,
        }
