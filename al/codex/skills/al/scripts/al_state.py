#!/usr/bin/env python3
"""Guarded AL workflow and evidence CLI; repository and cwd are pinned per run."""
import argparse
import json
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


from al_start import cmd_start


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
    if phase == "FINAL_VERIFICATION":
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
              f"Failure kind: {st['failure']['kind']}",
              f"Retry: {st['failure']['retry_safe']} · budget used {used}/1",
              f"Next step: {st['failure']['next_step']}"]
    print("\n".join(lines))


def cmd_model_unavailable(a, sid):
    """Latch a model as genuinely unavailable and switch the affected roles to
    their approved fallback. Exits 4 when a role has no approved model left —
    AL must STOP with STATUS: STOPPED_WITH_BLOCKERS, never downgrade."""
    st = load(sid)
    import al_failures
    kind = al_failures.classify(a.reason)
    if kind in {'model-refusal', 'permission'}:
        al_schema.refuse('model fallback is not available for this failure. ' + al_failures.next_step(kind))
    if kind == 'model-capacity' and not any(
        r['model'] == a.model.strip().lower() for r in st.get('model_retry_history', [])
    ):
        al_schema.refuse('retry the same approved primary once with model-retry before recording capacity as unavailability')
    lines, blocked, switched = al_models.mark_unavailable(st, a.model, a.reason)
    refresh_active(st)
    if blocked:
        st["status"] = "STOPPED_WITH_BLOCKERS"
        st["stop_reason"] = al_models.blocked_reason(st, blocked)
        if st.get("phase") in st["phase_timings"]:
            st["phase_timings"][st["phase"]]["ended"] = time.time()
        recovery = st.get('rework_recovery')
        if recovery and recovery['pending']:
            from al_rework_recovery import stop
            pending = next(o for o in recovery['occurrences'] if o['id']==recovery['pending'])
            stop(st,sid,pending,st['stop_reason'],'model')
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
    from al_execution_tasks import completion_gaps, authority
    if completion_gaps(st): al_schema.refuse(str(completion_gaps(st)))
    if "integration" in st: authority(st, "approve")
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


def _main():
    ap = argparse.ArgumentParser(prog="al_state.py")
    ap.add_argument("--session")
    ap.add_argument("--recovery-file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--spec-file"); s.add_argument("--spec"); s.add_argument("--package-file"); s.add_argument("--handoff"); s.add_argument("--review-mode", choices=["task","whole-mr"], default="task"); s.add_argument("--target", default=None); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("spec-gate"); s.add_argument("verdict", choices=["SPEC_READY", "SPEC_BLOCKED"])
    s.add_argument("--reason", default=""); s.set_defaults(fn=cmd_spec_gate)
    s = sub.add_parser("classify"); s.add_argument("classification")
    s.add_argument("--assessment-file", help="source-bound residual implementation risk")
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
    import al_inspection
    al_inspection.register(sub)
    al_review.register(sub)
    import al_retry
    al_retry.register(sub)
    import al_failures
    al_failures.register(sub)
    import al_artifact_cli
    al_artifact_cli.register(sub)
    s = sub.add_parser("agent-failed"); s.add_argument("agent"); s.add_argument("--reason", default="")
    s.add_argument("--peer"); s.add_argument("--peer-status", default="unknown",
                                             choices=["completed", "running", "failed", "none", "unknown"])
    s.add_argument("--retry-safe", default="undetermined", choices=["yes", "no", "undetermined"])
    s.add_argument("--kind", default="auto", choices=al_failures.KINDS)
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
    import al_execution_tasks
    al_execution_tasks.register(sub)
    import al_repository_audit
    al_repository_audit.register(sub)
    al_isolation.register(sub)
    al_writers.register(sub)
    al_gates.register(sub)
    al_task.register(sub)
    import al_planning
    al_planning.register(sub)
    import al_behavior_observations
    al_behavior_observations.register(sub)
    import al_publication
    al_publication.register(sub)
    import al_recovery_guidance
    al_recovery_guidance.register(sub)
    al_recovery_guidance.reset()
    al_recovery.register(sub)
    al_escapes.register(sub)
    al_rework.register(sub)
    import al_rework_recovery
    al_rework_recovery.register(sub)
    import al_acceptance
    al_acceptance.register(sub)
    al_evidence.register(sub)
    al_preflight.register(sub)
    argv = sys.argv[1:]
    command_argv = []
    if "--" in argv:  # everything after `--` is the command a check runs, verbatim
        i = argv.index("--")
        argv, command_argv = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    a.command_argv = command_argv
    local_recovery=getattr(a,"operation_recovery_file",None)
    if local_recovery and a.recovery_file:al_schema.refuse("supply one exact recovery descriptor")
    a.recovery_file=a.recovery_file or local_recovery
    if a.recovery_file and (a.cmd == "inspect" or a.cmd == "integration" and a.action == "capabilities"):
        al_schema.refuse("recovery descriptor is only valid for its exact guarded operation")
    if a.cmd == "integration" and a.action == "capabilities":
        a.fn(a, None)
        return
    if a.cmd == 'inspect':
        if not a.session:al_schema.refuse('inspect requires explicit --session')
        a.fn(a, a.session)
        return
    from al_new_origin import require_dispatch
    require_dispatch()
    sid = session_id(a.session, allow_new=(a.cmd == "start"))
    if a.recovery_file:
        import al_proof_json as J
        al_recovery_guidance.activate(load(sid),J.loads(Path(a.recovery_file).read_bytes()),a)
    # `evidence run` / `preflight` can take minutes: they lock each write
    # themselves; every other command is one short locked load-modify-save.
    if a.cmd in {"preflight", "isolate"} or (a.cmd in {"evidence", "gates"} and a.action == "run"):
        a.fn(a, sid)
    else:
        with locked(sid):
            if a.cmd not in {"rework-recovery","start","show","clear","resume","checkpoint","waiting","interrupt","transfer","stopped","writer","check-reconcile","retry","agent-peer-reconcile","integration","recovery","artifact"} and not (a.cmd in {"publication", "acceptance"} and a.action in {"packet", "status"}):
                existing = load(sid)
                if existing["status"] != "RUNNING": al_schema.refuse("run is not running; explicit resume required")
            a.fn(a, sid)


def main():
    """Normalize textual refusals to an explicit nonzero process exit for embedders."""
    try:
        return _main()
    except SystemExit as error:
        if error.code is None or isinstance(error.code, int):
            raise
        print(str(error.code), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
