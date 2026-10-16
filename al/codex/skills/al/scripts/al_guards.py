#!/usr/bin/env python3
"""AL mechanical guards.

These commands are the part of AL that does NOT rely on the orchestrator's
judgement:

  fanout     — records/announces a READ-ONLY worker fan-out and its tree check
  ownership  — refuses parallel work whose writes overlap, or whose declared
               commands mutate shared working-tree state                (exit 5)
  snapshot   — refuses to review a tree that moved since verification   (exit 6)
  retry      — refuses a second retry of the same failed agent          (exit 7)
  spec-gate  — stops the run when AL-Architect returns SPEC_BLOCKED     (exit 8)

A non-zero exit is not advice: AL must take the sequential / stop branch.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_models  # noqa: E402
import al_parallel  # noqa: E402
from al_phases import FANOUT_FOOTER, FANOUT_TITLE  # noqa: E402
from al_render import render_line, split_labels  # noqa: E402
from al_store import git, load, save  # noqa: E402


def cmd_fanout(a, sid):
    """Record and announce a READ-ONLY parallel worker fan-out (al-scout).

    Read-only by contract: al-scout runs in a read-only sandbox. AL still
    compares `git status --porcelain` around the fan-out; `--done --tree-changed`
    records that a worker touched the tree, which is a defect to report."""
    st = load(sid)
    if a.done:
        active = st.get("fanout") or {}
        if a.tree_changed and active:
            active["tree_changed"] = True
            for rec in st.get("fanouts", []):
                if rec.get("at") == active.get("at"):
                    rec["tree_changed"] = True
        st["fanout"] = None
        save(sid, st)
        if a.tree_changed:
            print("AL · WARNING · a read-only worker changed the working tree — report it as a caveat")
        else:
            print(render_line(st))
        return
    kind = a.kind
    if kind not in FANOUT_TITLE:
        sys.exit("al_state: fanout kind must be investigation or analysis")
    labels = split_labels(a.labels)
    if not labels:
        sys.exit("al_state: fanout needs --labels (one brief per worker)")
    slot = al_models.resolve(st, "al-scout")
    rec = {"role": "al-scout", "kind": kind, "count": len(labels), "labels": labels,
           "phase": st.get("phase"), "at": time.time()}
    st["fanout"] = dict(rec)
    st.setdefault("fanouts", []).append(rec)
    st.setdefault("aux_runs", []).append({"role": "al-scout", "kind": kind,
                                          "count": len(labels), "at": rec["at"]})
    save(sid, st)
    entries = [f"{al_models.display('al-scout')} · {al_models.label(slot)} · {b}" for b in labels]
    print(al_parallel.fanout_banner(FANOUT_TITLE[kind], entries, FANOUT_FOOTER[kind]))


def cmd_ownership(a, sid):
    """Mechanical parallel-safety gate. Exit 5 ⇒ AL MUST run the phase sequentially.

    Two independent refusals:
      * the two agents' owned paths overlap, or
      * either agent declared a command that rewrites state the whole tree shares
        (install, lockfile, codegen, migration, formatter, snapshot, git, build) —
        disjoint files do not protect a peer from those.
    """
    st = load(sid)
    sets = {"al-engineer": al_parallel.parse_paths(a.engineer),
            "al-builder": al_parallel.parse_paths(a.builder)}
    cmds = {"al-engineer": al_parallel.parse_commands(a.engineer_commands),
            "al-builder": al_parallel.parse_commands(a.builder_commands)}
    conflicts, warnings = al_parallel.check_ownership(sets, st.get("preexisting_changes") or [])
    shared = {agent: al_parallel.shared_state_hits(v) for agent, v in cmds.items()}
    shared = {agent: hits for agent, hits in shared.items() if hits}
    st.setdefault("write_ownership", {})[st.get("phase") or "UNKNOWN"] = {
        "sets": sets, "commands": cmds, "conflicts": [list(c) for c in conflicts],
        "shared_state": {k: [list(h) for h in v] for k, v in shared.items()},
        "warnings": warnings, "at": time.time(),
    }
    save(sid, st)
    for w in warnings:
        print(f"AL · ownership warning · {w}")
    if conflicts or shared:
        print("AL · Parallel execution refused", file=sys.stderr)
        for ea, pa, eb, pb in conflicts:
            same = " (same path)" if pa == pb else ""
            print(f"  write overlap · {ea}: {pa}  ⟷  {eb}: {pb}{same}", file=sys.stderr)
        for agent, hits in shared.items():
            for cmd, why in hits:
                print(f"  shared tree state · {agent}: `{cmd}` — {why}", file=sys.stderr)
        print("AL: run this phase SEQUENTIALLY", file=sys.stderr)
        sys.exit(5)
    n = sum(len(v) for v in sets.values())
    print(f"AL · write ownership disjoint · {n} owned path(s) · no shared-state command "
          f"· parallel execution allowed")


def cmd_spec_gate(a, sid):
    """Record AL-Architect's specification verdict. Exit 8 on SPEC_BLOCKED.

    SPEC_BLOCKED is reserved for MATERIAL ambiguity, contradiction or missing
    information — the kind that would change the resulting implementation. AL
    must stop and report the blocking questions instead of inventing
    requirements; it must never downgrade a block into an assumption."""
    st = load(sid)
    if st["phase"] != "PLANNING": sys.exit("al_state: spec gate requires planning")
    st["spec_status"] = a.verdict
    if a.verdict == "SPEC_BLOCKED":
        st["status"] = "SPEC_BLOCKED"
        st["stop_reason"] = a.reason or "specification not implementable as written"
        if st.get("phase") in st["phase_timings"]:
            st["phase_timings"][st["phase"]]["ended"] = time.time()
        save(sid, st)
        print("AL · SPEC_BLOCKED")
        print(f"Reason: {st['stop_reason']}")
        print("AL: stop implementation and report the blocking questions", file=sys.stderr)
        sys.exit(8)
    save(sid, st)
    print("AL · SPEC_READY")


def cmd_snapshot(a, sid):
    """Fingerprint the working tree. With --require, exit 6 if it moved since
    that label — the review barrier: never review a tree that is still changing."""
    st = load(sid)
    fp, changed = al_parallel.tree_fingerprint(git)
    snaps = st.setdefault("snapshots", {})
    prev = snaps.get(a.require) if a.require else None
    if a.require and not prev:
        print(f"AL: no snapshot named {a.require} to compare against", file=sys.stderr)
        sys.exit(6)
    if prev and prev["fingerprint"] != fp:
        print(f"AL · DIFF NOT STABLE · tree changed since {a.require} "
              f"({prev['fingerprint']} → {fp})", file=sys.stderr)
        print("AL: writes are still landing — wait for every implementation agent, "
              "then re-run verification before review", file=sys.stderr)
        sys.exit(6)
    snaps[a.label] = {"fingerprint": fp, "changed_paths": changed, "at": time.time()}
    save(sid, st)
    suffix = f" · matches {a.require} · diff stable" if a.require else ""
    print(f"AL · snapshot {a.label} · {fp} · {changed} changed path(s){suffix}")


def cmd_retry(a, sid):
    from al_retry import cmd_retry as guarded_retry
    return guarded_retry(a,sid)
