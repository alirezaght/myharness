"""Explicit stage orchestration; the ordinary single-case absent-root path stays strict."""

import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .continuity import validate_stage


def execute_stage(wrapper, mode):
    receipt = validate_stage(wrapper)
    if mode == "validate":
        return {
            "status": "validated",
            "corpus": receipt,
            "provider_started": False,
            "route": "explicit-stage",
            "action": wrapper["action"],
        }
    stage = {key: wrapper[key] for key in ("action", "al_task_id", "task_files", "donor")}
    stage["corpus_receipt"] = receipt
    from scripts.knowledge_native_eval import execute

    return execute(wrapper["package"], mode, existing=wrapper["existing"], stage=stage)


def refresh_recipient(package, admission):
    """Normal task-neutral working-tree refresh retains temporal reports; no synthetic insertion."""
    from harness.db import connect
    from harness.knowledge.models import RefreshRequest
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.refresh import refresh

    started = time.monotonic()
    with closing(connect(admission["database"])) as conn:
        outcome = refresh(
            conn, current_operator_scope(conn, Path(package["corpus"]["root"])), RefreshRequest()
        )
    return {
        "outcome": asdict(outcome),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "service_read_bytes": None,
        "method": "task-neutral normal working-tree incremental refresh",
        "limits": "working-tree observation, not a pure immutable commit snapshot",
    }
