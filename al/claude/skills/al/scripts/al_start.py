"""Publish a new AL run only through its independently protected origin."""
import secrets
import sys
import time
from pathlib import Path

import al_models
import al_rework
import al_schema
import al_scope
import al_task
from al_phases import MAIN_PHASES, MAX_CORRECTION_ITERATIONS
from al_review import NO_FINDINGS
from al_store import git, save, set_phase, state_path, write_current


def cmd_start(a, sid):
    from al_new_origin import require_dispatch
    require_dispatch()
    if a.review_mode == "whole-mr" and not (a.target and a.target.strip()):
        al_schema.refuse("whole-mr requires an explicit --target (for example main)")
    target = a.target or "HEAD"
    if state_path(sid).exists():
        al_schema.refuse("run identity already exists; history cannot be overwritten")
    from al_native_sessions import starting
    with starting(sid) as publication:
        import al_store
        al_store._ensure_runtime_dir()
        package, reference = al_task.package(sid, a)
        spec = package['accepted_spec']
        st = {
            "schema_version": al_schema.VERSION, "identity": al_scope.identity(target),
            "task_package": reference, "amendments": [], "writers": {}, "phase_occurrences": [],
            "review_mode": a.review_mode, "review": None,
            "workflow": "AL", "session_id": sid, "status": "RUNNING", "task": spec.strip(),
            "task_title": spec.strip().splitlines()[0][:80], "started_at": time.time(),
            "phase": None, "agent": None, "agents": [], "model": None, "effort": None,
            "review_iteration": 0, "correction_iteration": 0,
            "max_correction_iterations": MAX_CORRECTION_ITERATIONS,
            "review_status": None, "findings": dict(NO_FINDINGS),
            "spec_status": None, "classification": None, "path": None,
            "designer_required": None, "designer_reason": None, "classification_reason": None,
            "skipped_phases": [], "optional_phases": [], "escalations": [],
            "review_history": [], "oscillations": [], "approved_with_notes": 0,
            "completed_phases": [], "pending_phases": list(MAIN_PHASES), "phase_timings": {},
            "baseline_head": git("rev-parse", "HEAD").strip(),
            "preexisting_changes": al_scope.parent_status(),
            "failure": None, "stop_reason": None,
            "exec_mode": {}, "write_ownership": {}, "snapshots": {},
            "retries": {}, "fanout": None, "fanouts": [],
            "run_id": (sys.modules["_al_run_pin_context"].transaction["run_id"]
                   if "_al_run_pin_context" in sys.modules else secrets.token_hex(4)),  # a restarted session must not accept an older run's results
            "evidence": {"seq": 0, "entries": [], "plan": {}}, "overlaps": [], "handoffs": 0,
            "rework": {"batch": None, "used": 0, "max": al_rework.MAX_REWORK_PER_BATCH,
                       "total": 0, "history": []},
            **al_models.initial_state(),
        }
        from al_native_sessions import attribution
        st["native_conversation_id"]=attribution()
        if "_al_run_pin_context" in sys.modules:
            transaction = sys.modules["_al_run_pin_context"].transaction
            st['runtime_origin'] = {key: transaction[key] for key in
                                    ('kind', 'schema_version', 'run_id', 'publication_transaction', 'pin')}
        if getattr(a, 'handoff', None):
            from al_execution_contract import MAX_HANDOFF, loads
            from al_events import initialise
            envelope = loads(Path(a.handoff).read_bytes(), max_bytes=MAX_HANDOFF)
            if package != envelope['task_package']:
                al_schema.refuse('task package does not match approved handoff')
            initialise(st, envelope, a.handoff)
            import al_execution_tasks
            al_execution_tasks.authority(st, 'start')
        from al_task_attribution import capture_attribution
        capture_attribution(st)
        set_phase(st, "PLANNING")
        from al_new_origin import initialize
        initialize(st)
        save(sid, st)
        write_current(sid,publication)
        print(f"AL session {sid}")
        print(f"AL state started for session {sid}: {state_path(sid)}")
        print(f"pre-existing working-tree changes: {len(st['preexisting_changes'])}")
