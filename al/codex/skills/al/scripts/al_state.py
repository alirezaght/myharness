#!/usr/bin/env python3
"""Guarded AL workflow and evidence CLI; repository and cwd are pinned per run."""
import argparse
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_evidence  # noqa: E402
import al_models  # noqa: E402
import al_preflight  # noqa: E402
import al_rework  # noqa: E402
import al_task
import al_scope
import al_schema
import al_transitions
import al_writers
import al_gates
import al_review
import al_recovery
import al_escapes
import al_isolation
from al_guards import (cmd_fanout, cmd_ownership,  # noqa: E402
                       cmd_retry, cmd_snapshot, cmd_spec_gate)
from al_phases import (AUX_PHASES, MAIN_PHASES,  # noqa: E402
                       MAX_CORRECTION_ITERATIONS, PHASE_ALIASES, PHASE_LABEL)
from al_render import (phase_banner, render_line, render_panel,  # noqa: E402
                       split_labels)
from al_review import (NO_FINDINGS, cmd_correction, cmd_oscillation,  # noqa: E402
                       cmd_review_result, cmd_review_start)
from al_routing import cmd_classify  # noqa: E402
from al_store import (locked, clear_current, git, load, refresh_active,  # noqa: E402
                      save, session_id, set_phase, state_path, write_current)


def cmd_start(a, sid):
    if a.review_mode == "whole-mr" and not (a.target and a.target.strip()):
        al_schema.refuse("whole-mr requires an explicit --target (for example main)")
    target = a.target or "HEAD"
    if state_path(sid).exists():
        al_schema.refuse("run identity already exists; history cannot be overwritten")
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
        "run_id": secrets.token_hex(4),  # a restarted session must not accept an older run's results
        "evidence": {"seq": 0, "entries": [], "plan": {}}, "overlaps": [], "handoffs": 0,
        "rework": {"batch": None, "used": 0, "max": al_rework.MAX_REWORK_PER_BATCH,
                   "total": 0, "history": []},
        **al_models.initial_state(),
    }
    set_phase(st, "PLANNING")
    save(sid, st)
    write_current(sid)
    print(f"AL session {sid}")
    print(f"AL state started for session {sid}: {state_path(sid)}")
    print(f"pre-existing working-tree changes: {len(st['preexisting_changes'])}")


def cmd_phase(a, sid):
    st = load(sid)
    phase = PHASE_ALIASES.get(a.phase, a.phase)
    if phase not in MAIN_PHASES + AUX_PHASES:
        sys.exit(f"al_state: unknown phase {a.phase}")
    if a.mode and a.mode not in ("parallel", "sequential"):
        sys.exit("al_state: --mode must be parallel or sequential")
    agents = [x for x in (a.agents or "").split(",") if x.strip()]
    if a.mode == "parallel" and len(agents) < 2:
        sys.exit("al_state: --mode parallel needs at least two --agents")
    al_transitions.require_transition(st, "phase", phase)
    st["failure"] = None
    st["fanout"] = None
    escalated = phase in (st.get("skipped_phases") or [])
    if escalated:
        st["skipped_phases"] = [p for p in st["skipped_phases"] if p != phase]
        st.setdefault("escalations", []).append({
            "phase": phase, "reason": a.reason or "escalated by AL", "at": time.time()})
    set_phase(st, phase, agents or None, a.mode)
    if phase in {"VERIFICATION","FINAL_VERIFICATION"}:
        gaps=al_transitions.workflow_gaps(st)
        if gaps: al_schema.refuse("workflow prerequisites incomplete: " + str(gaps))
    if phase == "REVIEW":
        st["review_status"] = "checking"
    save(sid, st)
    if escalated:
        print(f"AL · {PHASE_LABEL.get(phase, phase)} escalated — this path had skipped it "
              f"({a.reason or 'reason not stated'})")
    if a.mode:
        print(phase_banner(st, phase, a.mode, split_labels(a.labels), a.reason))
    else:
        print(render_line(st))


def cmd_agent_failed(a, sid):
    """Record a failed agent and print the AL · AGENT FAILED block.

    A failed agent is NEVER marked completed, and a parallel phase is never
    reported as succeeded because its sibling did: the block always names the
    sibling's real state."""
    st = load(sid)
    import al_retry
    al_retry.record(st,sid,a)
    phase=st['phase']; parallel=st['failure']['parallel']
    save(sid, st)
    used = (st.get("retries") or {}).get(a.agent, 0)
    lines = ["AL · AGENT FAILED",
             f"Agent: {al_models.display(a.agent)}",
             f"Phase: {PHASE_LABEL.get(phase, phase)}",
             f"Parallel phase: {'yes — ' + PHASE_LABEL.get(phase, str(phase)) if parallel else 'no (sequential)'}"]
    if a.peer:
        lines.append(f"Other agent: {al_models.display(a.peer)} — {a.peer_status}")
    elif parallel:
        lines.append("Other agent: unknown — AL must resolve the sibling's state before continuing")
    lines += [f"Error/Reason: {a.reason}",
              f"Retry: {a.retry_safe} · budget used {used}/1"]
    print("\n".join(lines))


def cmd_model_unavailable(a, sid):
    """Latch a model as genuinely unavailable and switch the affected roles to
    their approved fallback. Exits 4 when a role has no approved model left —
    AL must STOP with STATUS: STOPPED_WITH_BLOCKERS, never downgrade."""
    st = load(sid)
    lines, blocked, switched = al_models.mark_unavailable(st, a.model, a.reason)
    refresh_active(st)
    if blocked:
        st["status"] = "STOPPED_WITH_BLOCKERS"
        st["stop_reason"] = al_models.blocked_reason(st, blocked)
        if st.get("phase") in st["phase_timings"]:
            st["phase_timings"][st["phase"]]["ended"] = time.time()
    save(sid, st)
    for line in lines:
        print(line)
    if blocked:
        print(f"AL: {st['stop_reason']} — STOP with STATUS: STOPPED_WITH_BLOCKERS", file=sys.stderr)
        sys.exit(4)
    if not switched:
        print(f"AL: {a.model} already latched unavailable; no role changed")


def cmd_approved(a, sid):
    st = load(sid)
    al_transitions.require_transition(st, "approved", "DONE")
    missing = al_review.approval_gaps(st)
    if missing: al_schema.refuse("approval refused: " + str(missing))
    st['approval'] = {'review': st['review']['id'], 'fingerprint': st['review']['candidate']['fingerprint'],
                      'task_hash': al_task.task_hash(st), 'evidence': [e['id'] for e in st['evidence']['entries'] if al_evidence.L.entry_verdict(st,e)[0]=='VALID'],
                      'fresh': st['review']['fresh'], 'comparison': st['review']['comparison']}
    if a.with_notes != st["findings"]["nonblocking"]: al_schema.refuse("approval notes count differs from Architect findings")
    st["approved_with_notes"] = a.with_notes
    set_phase(st, "DONE")
    st["status"] = "APPROVED"
    st["pending_phases"] = []
    save(sid, st)
    print(render_line(st))


def cmd_stopped(a, sid):
    st = load(sid)
    import al_recovery
    if st['status'] not in {'RUNNING','AGENT_FAILED'} or st['phase']=='DONE':al_schema.refuse('stopped requires a nonterminal running or failed run')
    st["status"] = "STOPPED_WITH_BLOCKERS"
    st["stop_reason"] = a.reason
    al_recovery.close_occurrence(st)
    save(sid, st)
    print(render_line(st))


def cmd_clear(a, sid):
    clear_current(sid)
    print("AL state cleared")


def cmd_show(a, sid):
    st = load(sid)
    if a.spawn:
        if a.spawn not in al_models.ALL_POLICY:
            sys.exit(f"al_state: unknown role {a.spawn}")
        args = al_models.spawn_args(al_models.resolve(st, a.spawn))
        print(f"agent_type={args['agent_type']} model={args['model']} "
              f"reasoning_effort={args['reasoning_effort']} fork_turns={args['fork_turns']}")
    elif a.spec:
        print(st["task"])
    elif a.models:
        print(al_models.render_models(st))
    elif a.line:
        print(render_line(st))
    elif a.panel:
        print(render_panel(st))
    else:
        print(json.dumps(st, indent=2))


def main():
    ap = argparse.ArgumentParser(prog="al_state.py")
    ap.add_argument("--session")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--spec-file"); s.add_argument("--spec"); s.add_argument("--package-file"); s.add_argument("--review-mode", choices=["task","whole-mr"], default="task"); s.add_argument("--target", default=None); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("spec-gate"); s.add_argument("verdict", choices=["SPEC_READY", "SPEC_BLOCKED"])
    s.add_argument("--reason", default=""); s.set_defaults(fn=cmd_spec_gate)
    s = sub.add_parser("classify"); s.add_argument("classification")
    s.add_argument("--designer", choices=["yes", "no"], required=True)
    s.add_argument("--implementers"); s.add_argument("--reason"); s.add_argument("--designer-reason")
    s.set_defaults(fn=cmd_classify)
    s = sub.add_parser("phase"); s.add_argument("phase"); s.add_argument("--agents")
    s.add_argument("--mode"); s.add_argument("--labels"); s.add_argument("--reason")
    s.set_defaults(fn=cmd_phase)
    s = sub.add_parser("fanout"); s.add_argument("kind", nargs="?"); s.add_argument("--labels")
    s.add_argument("--done", action="store_true"); s.add_argument("--tree-changed", action="store_true")
    s.set_defaults(fn=cmd_fanout)
    s = sub.add_parser("ownership"); s.add_argument("--engineer", default=""); s.add_argument("--builder", default="")
    s.add_argument("--engineer-commands", default=""); s.add_argument("--builder-commands", default="")
    s.set_defaults(fn=cmd_ownership)
    s = sub.add_parser("snapshot"); s.add_argument("label"); s.add_argument("--require")
    s.set_defaults(fn=cmd_snapshot)
    s = sub.add_parser("retry"); s.add_argument("agent"); s.add_argument("--reason", default="")
    s.set_defaults(fn=cmd_retry)
    al_review.register(sub)
    import al_retry
    al_retry.register(sub)
    s = sub.add_parser("agent-failed"); s.add_argument("agent"); s.add_argument("--reason", default="")
    s.add_argument("--peer"); s.add_argument("--peer-status", default="unknown",
                                             choices=["completed", "running", "failed", "none", "unknown"])
    s.add_argument("--retry-safe", default="undetermined", choices=["yes", "no", "undetermined"])
    s.set_defaults(fn=cmd_agent_failed)
    s = sub.add_parser("model-unavailable"); s.add_argument("model"); s.add_argument("--reason", default="")
    s.set_defaults(fn=cmd_model_unavailable)
    s = sub.add_parser("approved"); s.add_argument("--with-notes", type=int, default=0)
    s.set_defaults(fn=cmd_approved)
    s = sub.add_parser("stopped"); s.add_argument("--reason", default=""); s.set_defaults(fn=cmd_stopped)
    sub.add_parser("clear").set_defaults(fn=cmd_clear)
    s = sub.add_parser("show")
    for k in ("line", "panel", "spec", "models", "json"):
        s.add_argument(f"--{k}", action="store_true")
    s.add_argument("--spawn", metavar="ROLE")
    s.set_defaults(fn=cmd_show)
    al_isolation.register(sub)
    al_writers.register(sub)
    al_gates.register(sub)
    al_task.register(sub)
    al_recovery.register(sub)
    al_escapes.register(sub)
    al_rework.register(sub)
    al_evidence.register(sub)
    al_preflight.register(sub)
    argv = sys.argv[1:]
    command_argv = []
    if "--" in argv:  # everything after `--` is the command a check runs, verbatim
        i = argv.index("--")
        argv, command_argv = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    a.command_argv = command_argv
    sid = session_id(a.session, allow_new=(a.cmd == "start"))
    # `evidence run` / `preflight` can take minutes: they lock each write
    # themselves; every other command is one short locked load-modify-save.
    if a.cmd in {"preflight", "isolate"} or (a.cmd in {"evidence", "gates"} and a.action == "run"):
        a.fn(a, sid)
    else:
        with locked(sid):
            if a.cmd not in {"start","show","clear","resume","checkpoint","waiting","interrupt","transfer","stopped","writer","check-reconcile","retry","agent-peer-reconcile"}:
                existing = load(sid)
                if existing["status"] != "RUNNING": al_schema.refuse("run is not running; explicit resume required")
            a.fn(a, sid)


if __name__ == "__main__":
    main()
