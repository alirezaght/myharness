#!/usr/bin/env python3
"""AL rework — the route and the budget for a defect found BEFORE review
(Builder finalization's CORE_FLAW_REPORTS, a failing pre-review verification).

Route. A confirmed, local core defect inside the approved scope goes straight
to AL-Engineer: no new plan, no Designer, no Architect round for a two-line fix.
AL-Architect is consulted first only when the defect needs a decision — the
cause is not demonstrated, the fix is not local, it changes an interface or
contract, the scope is ambiguous, it is a safety decision, or it conflicts with
the specification or the design (`rework` exits 12).

Demonstrated means recorded. `--cause-demonstrated yes` needs `--repro E<n>`: a
ledger run of one command that goes red on this defect (status FAIL, log
intact). Without one the claim is refused (exit 14) and no budget is spent. The
repro is remembered: before review, the same command must be a VALID PASS on
the current tree (`al_ledger.repro_gaps`), so the fix is proven by the loop that
proved the bug.

Budget. A new run has two return credits. Review corrections never replenish
these credits. A further valid request opens durable Architect reassessment;
at most two evidence-bound automatic grants of two are allowed per run. A
capable subsystem stop supports explicit, bound two-credit user continuation.
Exhaustion exits 10 after persisting the reassessment or precise stopping state.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_ledger as L  # noqa: E402
from al_store import load, save, set_phase  # noqa: E402

MAX_REWORK_PER_BATCH = 2
TRIGGERS = [
    ("cause_demonstrated", "no", "the cause is not demonstrated"),
    ("local", "no", "the patch is not local"),
    ("contract_change", "yes", "it changes an interface, contract or invariant"),
    ("scope_ambiguous", "yes", "the approved scope does not settle it"),
    ("safety_decision", "yes", "it needs a safety decision"),
    ("spec_conflict", "yes", "it conflicts with the specification or the design"),
]


def rework_state(st):
    rw = st.setdefault("rework", {})
    rw.setdefault("batch", None)
    rw.setdefault("used", 0)
    rw.setdefault("max", MAX_REWORK_PER_BATCH)
    rw.setdefault("total", 0)
    rw.setdefault("history", [])
    return rw


def batch_line(st):
    rw = st.get("rework") or {}
    if not rw.get("used"):
        return ""
    return f"Rework {rw['used']}/{rw.get('max', MAX_REWORK_PER_BATCH)}"


def _check_repro(st, a):
    """The ledger id that demonstrates the cause, or exit 14. None when the
    cause is declared not demonstrated (that already routes to the Architect)."""
    if a.cause_demonstrated == "no":
        return a.repro or None
    if not a.repro:
        print("AL: --cause-demonstrated yes needs --repro E<n> — a recorded run that goes red "
              "on this defect; record it with `evidence run`, or pass --cause-demonstrated no",
              file=sys.stderr)
        sys.exit(14)
    e = L.find(st, a.repro)
    problem = None
    if not e:
        problem = "no such ledger entry — the run was not recorded"
    else:
        verdict, why = L.assess(e)
        if verdict in ("LOG_MISSING", "LOG_ALTERED", "RUNNING", "INTERRUPTED"):
            problem = f"{verdict} — {why}"
        elif e["status"] != "FAIL":
            problem = f"status {e['status']}, not FAIL — a repro must go red on the defect"
    if problem:
        print(f"AL · repro rejected · {a.repro}: {problem}", file=sys.stderr)
        sys.exit(14)
    return a.repro


def _reopen_tasks(st, files):
    """A defect found after a task completed reopens that task (new revision), so it can be admitted again."""
    tasks = st.get("integration", {}).get("tasks", {})
    if not tasks:
        return []
    import al_events as E
    from al_execution_tasks import _overlap
    reopened = []
    for task_id, row in sorted(tasks.items()):
        if row["status"] != "Completed" or (files and not any(_overlap(f, p) for f in files for p in row["files"])):
            continue
        row.update(status="Waiting", revision=row.get("revision", 0) + 1,
                   progress={"completed": 0, "total": 1, "unit": "task"})
        row.pop("admission", None)
        row.pop("completed_at", None)
        E.queue(st, "task.updated", row)
        reopened.append(task_id)
    return reopened


def validated_request(st,a):
    from al_schema import refuse
    if type(a.findings) is not int or a.findings<1:refuse('--findings must be at least 1 (batch every confirmed defect into one return)')
    repro=_check_repro(st,a)
    reasons=[why for key,bad,why in TRIGGERS if getattr(a,key)==bad]
    from al_writers import canonical_paths
    files=[f.strip() for f in (a.files or '').split(',') if f.strip()]
    if files:files=canonical_paths(files)
    arguments={key:getattr(a,key) for key,_,_ in TRIGGERS}
    arguments.update(source=a.source,findings=a.findings,files=','.join(files),label=a.label,repro=a.repro)
    return {'arguments':arguments,'source':a.source,'findings':a.findings,'files':files,
            'label':a.label,'repro':repro,'reasons':reasons,'route':'architect' if reasons else 'fast'}


def consume_return(st,request):
    rw=rework_state(st)
    row={key:request[key] for key in ('source','findings','files','label','repro','reasons','route')}
    if 'rework_recovery' in st:
        r=st['rework_recovery'];row.update(grant_id=r['grants'][-1]['id'],correction_iteration=st.get('correction_iteration',0))
    if rw['used']>=rw['max']:raise ValueError('no rework credit')
    rw['used']+=1;rw['total']+=1
    row.update(batch=rw['batch'],n=rw['used'],phase=st['phase'],at=time.time());rw['history'].append(row)
    st['status']='RUNNING';_reopen_tasks(st,row['files'])
    set_phase(st,'REWORK',['al-architect','al-engineer'] if row['reasons'] else ['al-engineer'])
    return row


def cmd_rework(a, sid):
    st=load(sid)
    request=validated_request(st,a)
    from al_rework_recovery_contract import require
    recovery=require(st)
    if recovery['pending']:
        o=next(o for o in recovery['occurrences'] if o['id']==recovery['pending'])
        if o['request']!=request:
            from al_schema import refuse
            refuse('conflicting pending rework request')
        from al_rework_recovery import current
        current(st,o)
        print('AL · rework budget exhausted · Architect reassessment pending',file=sys.stderr);sys.exit(10)
    from al_transitions import require_transition
    require_transition(st,'rework','REWORK')
    rw=rework_state(st)
    if rw['used']>=rw['max']:
        from al_rework_recovery import enter
        enter(st,sid,request)
        print('AL · rework budget exhausted · '+('Architect reassessment pending' if recovery['pending'] else st['stop_reason']),file=sys.stderr)
        sys.exit(10)
    consume_return(st,request);save(sid,st)
    print(f"AL · Rework {rw['used']}/{rw['max']} · {a.findings} defect(s) from {a.source}")
    if request['reasons']:
        print('→ AL-Architect first (MODE: RESOLVE_CONFLICT) — '+'; '.join(request['reasons']));sys.exit(12)
    print('→ AL-Engineer · fast path: confirmed local core defect, interface and invariants unchanged')
    print('Builder resumes only the affected support work afterwards · no new plan, no Designer')


def cmd_rework_resolve(a, sid):
    from al_artifacts import snapshot_artifact
    from al_schema import refuse
    st = load(sid)
    history = rework_state(st)['history']
    if a.index < 1 or a.index > len(history): refuse('unknown rework occurrence')
    item = history[a.index - 1]
    if item.get('repro'): refuse('rework already has a repro')
    from types import SimpleNamespace
    red = _check_repro(st, SimpleNamespace(cause_demonstrated='yes', repro=a.repro))
    item.update(repro=red, resolution={'reason':a.reason, 'artifact':snapshot_artifact(sid,Path(a.artifact),'rework-resolution')})
    save(sid, st)


def register(sub):
    p = sub.add_parser("rework-resolve"); p.add_argument("--index", type=int, required=True)
    p.add_argument("--repro", required=True); p.add_argument("--reason", required=True); p.add_argument("--artifact", required=True); p.set_defaults(fn=cmd_rework_resolve)
    p = sub.add_parser("rework")
    p.add_argument("--source", required=True, choices=["finalization", "verification", "core-flaw"])
    p.add_argument("--findings", type=int, default=1)
    p.add_argument("--files", default="")
    p.add_argument("--label", default="")
    p.add_argument("--repro", default="", help="ledger id of the red run that demonstrates the cause")
    for key, _, _ in TRIGGERS:
        p.add_argument(f"--{key.replace('_', '-')}", required=True, choices=["yes", "no"])
    p.set_defaults(fn=cmd_rework)
