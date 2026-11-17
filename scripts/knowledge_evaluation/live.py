"""One isolated selected-bridge worker, with qualified in-process scheduling only."""

import json
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from .package import sha, write_once


def connection_class(base):
    """Restrict the original MCP listing while retaining its authentication/validation path."""

    class Connection(base):
        def request(self, message):
            adapter = self.dispatcher.__self__
            if adapter.budget:
                from .budget import request

                return request(adapter, self, super().request, message)
            result = super().request(message)
            if message.get("method") == "tools/list":
                adapter = self.dispatcher.__self__
                result["tools"] = [t for t in result["tools"] if t["name"] in adapter.allowed]
                adapter.visible(json.dumps(result), name="tools/list")
            return result

    return Connection


def module_receipt(bridge):
    modules = {}
    for name, module in tuple(sys.modules.items()):
        if name == "harness" or name.startswith("harness."):
            source = Path(module.__file__).resolve()
            if not source.is_relative_to(bridge / "harness"):
                raise ValueError("conflicting Harness import outside selected bridge")
            manifest = json.loads((bridge / "manifest.json").read_bytes())
            digest = sha(source.read_bytes())
            relative = source.relative_to(bridge / "harness").as_posix()
            if manifest["files"].get(relative) != digest:
                raise ValueError("imported Harness source differs from authenticated manifest")
            modules[name] = {"path": str(source), "sha256": digest}
    return modules


def run(package_path, admission_path, bridge_path, *, evaluator_source=None):
    from .worker import hashes, verify_source

    engine, expected = evaluator_source or (Path(__file__).resolve().parents[2], None)
    expected = hashes(engine) if expected is None else expected
    verify_source(engine, expected)
    # The isolated launcher inserts the selected bridge BEFORE importing this module.
    from harness import execution_mcp as mcp
    from harness import execution_worker as worker
    from harness import native_runtime
    from harness.db import connect
    from harness.execution_bridge import bridge_root
    from harness.execution_store import get_execution

    from .adapter import EvaluatorAdapter
    from .package import load

    package, admission = load(Path(package_path)), json.loads(Path(admission_path).read_bytes())
    from .method import validate_method

    validate_method(package)
    bridge = Path(bridge_path).resolve(strict=True)
    output = Path(package["output_directory"])
    evaluator_hashes = None
    if package["schema_version"] == 2:
        from scripts.knowledge_native_eval import adapter_hashes

        evaluator_hashes = adapter_hashes()
        isolation = json.loads((output / "isolation.json").read_bytes())
        if (
            isolation["adapter_hashes"] != evaluator_hashes
            or isolation.get("method") != package["method"]
            or admission.get("evaluation_method") != package["method"]
        ):
            raise ValueError("M2 evaluator source/method binding changed before dispatch")
    captured, adapters, telemetry, ingestion_refusals = [], [], [], []
    original_connection, original_serve = mcp.MCPConnection, native_runtime.serve_mcp
    original_runtime = native_runtime.NativeRuntime

    def capture(argv, execution, attempt):
        if captured:
            raise ValueError("second worker dispatch refused")
        captured.append((list(argv), execution["id"], attempt))

    Connection = connection_class(original_connection)

    def serve(resolve, dispatch, **kwargs):
        operation = dispatch.__self__
        adapter = next((a for a in adapters if a.operation is operation), None)
        if adapter is None:
            adapter = EvaluatorAdapter(operation, package, admission.get("evaluation_stage"))
            adapter.ingestion_refusals = ingestion_refusals
            adapters.append(adapter)
        return original_serve(resolve, adapter.dispatch, **kwargs)

    class Runtime(original_runtime):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                directory = (
                    Path(self.policy["directory"])
                    if "directory" in self.policy
                    else (
                        Path(
                            self.policy["options"][self.policy["options"].index("--agents") + 1]
                        ).parent
                    )
                )
                if package["host"] == "codex":
                    import tomllib

                    role = tomllib.loads((directory / (package["role"] + ".toml")).read_text())
                    actual = (role["model"], role["model_reasoning_effort"])
                else:
                    role = json.loads((directory / "agents.json").read_bytes())[package["role"]]
                    actual = (role["model"], role["effort"])
                if actual != (package["model"], package["effort"]):
                    raise ValueError("exact specialized role model/effort profile mismatch")
            except BaseException:
                self.close()
                raise

        def tick(self):
            for adapter in adapters:
                adapter.check_time()
            super().tick()

    original_drain = worker._drain

    def drain(stream, channel, queue):
        class ObservedQueue:
            def put(self, value, *args, **kwargs):
                # Parse only public usage/model fields. Never persist native reasoning/text blocks.
                if value[0] == "host":
                    try:
                        event = json.loads(value[1])
                        if event.get("type") in {"assistant", "turn.completed", "turn_context"}:
                            message = event.get("message", event.get("payload", event))
                            if isinstance(message, dict):
                                telemetry.append(
                                    {
                                        "type": event["type"],
                                        "parent_tool_use_id": event.get("parent_tool_use_id"),
                                        **{
                                            k: message[k]
                                            for k in ("model", "effort", "usage")
                                            if k in message
                                        },
                                    }
                                )
                        if event.get("type") == "result":
                            telemetry.append(
                                {
                                    k: event[k]
                                    for k in (
                                        "type",
                                        "subtype",
                                        "usage",
                                        "modelUsage",
                                        "duration_ms",
                                        "duration_api_ms",
                                        "total_cost_usd",
                                        "session_id",
                                    )
                                    if k in event
                                }
                            )
                    except (ValueError, TypeError):
                        pass
                return queue.put(value, *args, **kwargs)

        return original_drain(stream, channel, ObservedQueue())

    started = time.monotonic()
    with closing(connect(admission["database"])) as conn:
        execution_id = admission["execution_id"]
        execution = get_execution(conn, execution_id)
        if bridge_root(execution).resolve() != bridge:
            raise ValueError("selected bridge changed")
        # bridge_root already authenticates the selected snapshot; retain its manifest identity.
        manifest = json.loads((bridge / "manifest.json").read_bytes())
        initial_modules = module_receipt(bridge)
        evaluator_modules = verify_source(engine, expected)
        with patch.object(worker, "_spawn_worker", capture):
            outcome = worker.launch_worker(conn, execution_id)
        if len(captured) != 1 or outcome.get("status") != "WorkerOwned":
            raise ValueError("exactly one real WorkerOwned dispatch required")
        argv, eid, attempt = captured[0]
        options = argv[argv.index("--db") :]
        values = dict(zip(options[::2], options[1::2], strict=True))
        execution = get_execution(conn, execution_id)
        row = conn.execute("SELECT * FROM execution_attempts WHERE id=?", (attempt,)).fetchone()
        if (
            eid != execution_id
            or values.get("--execution") != eid
            or values.get("--db") != admission["database"]
            or values.get("--mode") != "start"
            or set(values) != {"--db", "--execution", "--token", "--mode"}
            or outcome.get("attempt_id") != attempt
            or not row
            or row["token"] != values["--token"]
            or execution["worker_token"] != row["token"]
            or row["status"] != "Reserved"
            or execution["status"] != "WorkerOwned"
        ):
            raise ValueError("actual worker reservation differs; preserve for reconciliation")
        write_once(
            output / "worker-reservation.json",
            {
                "execution_id": eid,
                "attempt_id": attempt,
                "status": outcome["status"],
                "selected_bridge": str(bridge),
                "bridge_fingerprint": manifest["fingerprint"],
                "initial_modules": initial_modules,
            },
        )
        from .lifecycle import ingestion_boundary

        with (
            ingestion_boundary(package["arm"], ingestion_refusals),
            patch.object(mcp, "MCPConnection", Connection),
            patch.object(native_runtime, "serve_mcp", serve),
            patch.object(native_runtime, "NativeRuntime", Runtime),
            patch.object(
                worker,
                "_prompt",
                lambda *args, **kwargs: prompt(package, admission.get("evaluation_stage")),
            ),
            patch.object(worker, "_drain", drain),
        ):
            # Public launch_worker has returned and released its lock. No detached spawn occurred.
            worker.supervise(conn, eid, values["--token"])
        actual = get_execution(conn, eid)
        receipt = {
            "status": "observed" if adapters and adapters[0].answers else "unavailable",
            "execution_status": actual["status"],
            "execution_id": eid,
            "al_run_id": actual["al_run_id"],
            "native_id": actual["native_id"],
            "epoch_id": actual["epoch_id"],
            "approval_binding": actual["binding"],
            "adapter": adapters[0].receipt() if adapters else None,
            "provider_telemetry": telemetry,
            "actual_role_model_telemetry": "see public host telemetry; unreported fields unknown",
            "arm_isolation": "independent fresh native root/store/corpus; no other arm context",
            "clean_review_provenance": "not claimed or enabled",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "selected_bridge": str(bridge),
            "evaluator_source": {
                "root": str(engine),
                "hashes": expected,
                "initial_modules": evaluator_modules,
                "modules": verify_source(engine, expected),
            },
            "bridge_fingerprint": manifest["fingerprint"],
            "modules": module_receipt(bridge),
            "mission_complete": False,
            "qualification": "private in-process scheduling and authenticated evaluator adapter",
            "continuity": (
                {
                    "route": admission["evaluation_stage"]["action"],
                    "producer_invocation_completed": bool(adapters and adapters[0].completion),
                    "limits": (
                        "inspect actual completion/capability events; no verified task completion"
                    ),
                }
                if admission.get("evaluation_stage")
                else "unavailable: no completed producer/supervisor transfer demonstrated"
            ),
        }
        if package["schema_version"] == 2:
            from harness.execution_settlement import closed_context

            from .method import participant_profile
            from .observer import observe_return
            from .usage import usage_receipt

            if adapter_hashes() != evaluator_hashes:
                raise ValueError("M2 evaluator source changed during execution")
            receipt["evaluator_hashes"] = evaluator_hashes
            receipt["prompt_sha256"] = sha(
                prompt(package, admission.get("evaluation_stage")).encode()
            )
            receipt["child_returns"] = []
            for answer in adapters[0].answers if adapters else []:
                try:
                    observed = observe_return(
                        adapters[0].operation.registry.native_root,
                        actual["native_id"],
                        answer["principal"],
                        answer["role"],
                        expected_profile=participant_profile(package),
                    )
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    observed = {"status": "unavailable", "reason": str(exc)[:256]}
                receipt["child_returns"].append(observed)
            receipt["method"] = package["method"]
            receipt["usage"] = usage_receipt(adapters[0] if adapters else None)
            attempts = conn.execute(
                "SELECT * FROM execution_attempts WHERE execution_id=?", (eid,)
            ).fetchall()
            receipt["settlement"] = {
                "execution_status": actual["status"],
                "attempts": [
                    {
                        **{k: a[k] for k in ("id", "status", "worker_pid", "child_pid")},
                        "closed_context": closed_context(actual, a),
                    }
                    for a in attempts
                ],
                "limits": "native invocation teardown only; not completed/verified AL work",
            }
        write_once(output / "native-result.json", receipt)


def prompt(package, stage=None):
    extra = ""
    child_policy = ""
    if stage and stage["action"] == "donor":
        extra = (
            "After the real child returns, while still supervising, call read_file with "
            "evaluation:complete-donor. A returned answer alone is insufficient. If native "
            "terminal metadata is pending, report the refusal; never fabricate completion. "
        )
    elif stage and stage["action"] == "recipient":
        extra = (
            "The child may explicitly read evaluation:donor-report for the exact authorized "
            "same-task historical report. Do not inject prior conversation or donor prose. "
        )
    if package["schema_version"] == 2:
        from .method import INSTRUCTIONS

        child_policy = INSTRUCTIONS + "\n"
        if stage and stage["action"] == "recipient":
            child_policy += extra
        extra += INSTRUCTIONS + "\n"
    return extra + (
        "This is a read-only evaluation under prior mission authorization. "
        "First call await_assignment to authenticate the supervisor. "
        "Spawn exactly one fresh specialized " + package["role"] + " invocation with no inherited "
        "conversation. Use that actual named role, its installed model and effort; no fallback. "
        "Send only the following evaluation instructions and question. Wait for its authored "
        "answer, then stop. Do not implement changes or claim AL/mission completion.\n"
        "Child instructions: "
        + child_policy
        + "use only evaluator Harness tools. First await_assignment; "
        "ReadOnly, Pending, or Assigned with unavailable handoff/context are expected; "
        "there is no admitted writer. Baseline resources via read_file: evaluation:analyze, "
        "evaluation:audit, evaluation:decisions, evaluation:suggest, "
        "and confined tracked source paths. "
        "Use knowledge tools only if listed. Return a sourced answer at most 1800 tokens "
        "as authenticated submit_output name answer.md using the graph revision from a fresh "
        "await_assignment after your initial call. "
        "Do not read other resources, use shell, mutate files, or rely on parent context.\n"
        "Question: " + package["prompt"]
    )
