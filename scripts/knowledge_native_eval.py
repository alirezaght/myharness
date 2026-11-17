#!/usr/bin/env python3
"""Validate/prepare a single case; only explicit live mode may spend provider tokens."""

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

# Support direct script invocation; isolated live workers select the authenticated bridge first.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.knowledge_evaluation.admission import admit, environment
from scripts.knowledge_evaluation.package import canonical, load, sha, validate, write_once


def adapter_hashes():
    root = Path(__file__).resolve().parent
    paths = [Path(__file__), *sorted((root / "knowledge_evaluation").glob("*.py"))]
    return {str(p.relative_to(root)): sha(p.read_bytes()) for p in paths}


def cold_index(package, admission):
    from harness.db import connect
    from harness.knowledge.models import RefreshRequest
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.refresh import refresh

    started = time.monotonic()
    root = Path(package["corpus"]["root"])
    with closing(connect(admission["database"])) as conn:
        scope = current_operator_scope(conn, root)
        outcome = refresh(conn, scope, RefreshRequest(commit=package["corpus"]["head"]))
    return {
        "outcome": asdict(outcome),
        "status": outcome.status,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "service_read_bytes": None,
        "storage_bytes": Path(admission["database"]).stat().st_size,
        "method": "task-neutral complete immutable commit refresh, before native question delivery",
    }


def execute(package, mode, *, existing=None, stage=None):
    receipt = validate(package) if stage is None else stage["corpus_receipt"]
    if mode == "validate":
        return {"status": "validated", "corpus": receipt, "provider_started": False}
    state, output = Path(package["state_directory"]), Path(package["output_directory"])
    if existing is None:
        state.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    write_once(output / "package.json", package)
    write_once(
        output / "isolation.json",
        {
            "corpus": receipt,
            "adapter_hashes": adapter_hashes(),
            "budgets": package["budgets"],
            "qualification": "private evaluator adapter; not unmodified production tool exposure",
            "roots": package["allowed_roots"],
            "arm": package["arm"],
            "participant": package["participant"],
            "provenance": package["provenance"],
        },
    )
    admission = admit(package, existing=existing, stage=stage)
    if mode == "prepare":
        write_once(output / "admission.json", admission)
        return {"status": "prepared", "provider_started": False, "task_id": admission["task_id"]}
    from harness.controller import HarnessController
    from harness.db import connect
    from harness.execution import start_execution
    from harness.execution_bridge import bridge_root
    from harness.execution_store import get_execution

    with environment(state), closing(connect(admission["database"])) as conn:
        reservation = start_execution(
            HarnessController(conn),
            admission["task_id"],
            host=package["host"],
            specs=admission["specs"],
            launch=False,
        )
        eid = reservation["execution"]["id"]
        execution = get_execution(conn, eid)
        bridge = bridge_root(execution)
        admission["execution_id"] = eid
        write_once(output / "admission.json", admission)
        if package["arm"] == "C":
            if stage and stage["action"] == "recipient":
                from scripts.knowledge_evaluation.stages import refresh_recipient

                write_once(output / "recipient-refresh.json", refresh_recipient(package, admission))
            elif not stage or stage["action"] == "donor":
                write_once(output / "cold-index.json", cold_index(package, admission))
        # New isolated interpreter: no pre-imported checkout Harness modules can leak in.
        program = (
            "import sys;sys.path.insert(0,sys.argv[1]);sys.path.append(sys.argv[2]);"
            "from scripts.knowledge_evaluation.live import run;run(*sys.argv[3:])"
        )
        command = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            program,
            str(bridge),
            str(Path(__file__).resolve().parents[1]),
            str(output / "package.json"),
            str(output / "admission.json"),
            str(bridge),
        ]
        # Normal supervisor owns native timeout/cleanup. A killed evaluator is NOT settled.
        result = subprocess.run(
            command,
            env=dict(os.environ, HARNESS_AUTHORITY_ROOT=str(bridge)),
            check=False,
            capture_output=True,
        )
    if result.returncode:
        # Do not copy unfiltered transport/native diagnostics into the evaluation report.
        write_once(
            output / "operational-failure.json",
            {
                "status": "unavailable",
                "worker_returncode": result.returncode,
                "stderr_sha256": sha(result.stderr),
                "stdout_sha256": sha(result.stdout),
                "recovery": (
                    "inspect actual execution diagnostics and reservation; no automatic retry"
                ),
                "settlement": "unknown; inspect authoritative attempt/closure receipts",
            },
        )
        return {"status": "unavailable", "execution_id": eid, "provider_started": None}
    return json.loads((output / "native-result.json").read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--route", choices=("single", "stage"), default="single")
    parser.add_argument("--mode", choices=("validate", "prepare", "live"), default="validate")
    args = parser.parse_args(argv)
    try:
        value = load(args.package)
        if args.route == "stage":
            from scripts.knowledge_evaluation.stages import execute_stage

            result = execute_stage(value, args.mode)
        else:
            result = execute(value, args.mode)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # Error text may include paths, but never raw native streams or credentials.
        print(canonical({"status": "refused", "reason": str(error)[:2048]}))
        return 2
    print(canonical(result))
    return 0 if result["status"] in {"validated", "prepared", "observed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
