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

Budget. Every return to AL-Engineer is counted here, separately from review
corrections, at most MAX_REWORK_PER_BATCH per batch. A batch is the initial
implementation (correction 0) or one review correction; a new review rejection
opens a new batch, and that is printed, never silent. Exhaustion exits 10:
STOP with STATUS: STOPPED_WITH_BLOCKERS. Confirmed independent defects go out
together in ONE return (`--findings N`), not one return each.
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
    if rw.get("batch") != st.get("correction_iteration", 0) or not rw.get("used"):
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


def cmd_rework(a, sid):
    st = load(sid)
    from al_transitions import require_transition
    require_transition(st,"rework","REWORK")
    rw = rework_state(st)
    batch = st.get("correction_iteration", 0)
    if rw["batch"] != batch:
        if rw["batch"] is not None:
            print(f"AL · rework budget · new batch (correction {batch}) — "
                  f"previous batch used {rw['used']}/{rw['max']}")
        rw["batch"], rw["used"] = batch, 0
    if a.findings < 1:
        sys.exit("al_state: --findings must be at least 1 (batch every confirmed defect into one return)")
    if rw["used"] >= rw["max"]:
        print("AL: rework budget exhausted; record explicit stopping state", file=sys.stderr)
        sys.exit(10)
    repro = _check_repro(st, a)
    reasons = [why for key, bad, why in TRIGGERS if getattr(a, key) == bad]
    rw["used"] += 1
    rw["total"] += 1
    rw["history"].append({
        "batch": batch, "n": rw["used"], "source": a.source, "findings": a.findings,
        "route": "architect" if reasons else "fast", "reasons": reasons, "repro": repro,
        "files": [f.strip() for f in (a.files or "").split(",") if f.strip()],
        "label": a.label, "phase": st.get("phase"), "at": time.time(),
    })
    st["status"] = "RUNNING"
    reopened = _reopen_tasks(st, rw["history"][-1]["files"])
    set_phase(st, "REWORK", ["al-architect", "al-engineer"] if reasons else ["al-engineer"])
    save(sid, st)
    head = f"AL · Rework {rw['used']}/{rw['max']} · {a.findings} defect(s) from {a.source}"
    if a.label:
        head += f" · {a.label}"
    print(head)
    if reopened:
        print("AL · reopened execution task(s) for rework: " + ", ".join(reopened))
    if reasons:
        print("→ AL-Architect first (MODE: RESOLVE_CONFLICT) — " + "; ".join(reasons))
        print("→ then AL-Engineer with the resolution · Builder resumes only the affected support work")
        sys.exit(12)
    print("→ AL-Engineer · fast path: confirmed local core defect, interface and invariants unchanged")
    print("Builder resumes only the affected support work afterwards · no new plan, no Designer")


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
