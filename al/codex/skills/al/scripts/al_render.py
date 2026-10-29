#!/usr/bin/env python3
"""AL presentation — the one-line status segment, the phase panel and the
transition banners. Pure functions over a state dict: no I/O, no mutation.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_models  # noqa: E402
import al_parallel  # noqa: E402
from al_phases import FANOUT_TITLE, MAIN_PHASES, PHASE_AGENT, PHASE_LABEL  # noqa: E402


def split_labels(raw):
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def phase_banner(st, phase, mode, labels, reason):
    """The transition banner AL prints for a conditionally parallel phase."""
    title = PHASE_LABEL.get(phase, phase)
    roles = [r for r in st.get("agents", []) if r in al_models.ALL_POLICY]
    if mode == "parallel":
        entries = []
        for i, role in enumerate(roles):
            slot = al_models.resolve(st, role)
            tail = f" · {labels[i]}" if i < len(labels) else ""
            entries.append(f"{al_models.display(role)} · {al_models.label(slot)}{tail}")
        return al_parallel.fanout_banner(title, entries, "Running in parallel")
    entries = []
    for i, role in enumerate(roles):
        slot = al_models.resolve(st, role)
        tail = f" · {labels[i]}" if i < len(labels) else ""
        entries.append(f"{al_models.display(role)} · {al_models.label(slot)}{tail}")
    if len(roles) < 2:
        # A single implementer was never a parallel candidate — no "skipped" line.
        return al_parallel.sequential_banner(title, entries, reason)
    return al_parallel.sequential_banner(title, entries, reason,
                                         footer=None, skipped="Parallel execution skipped")


def fmt_elapsed(sec):
    sec = int(max(sec, 0))
    return f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s"


def phase_elapsed(st, phase):
    occurrence = next((row for row in reversed(st.get("phase_occurrences", [])) if row['phase']==phase), None)
    t = occurrence or st["phase_timings"].get(phase)
    if not t:
        return None
    return (t["ended"] or time.time()) - t["started"]


def agents_short(st):
    return " + ".join(al_models.display(a).replace("AL-", "") for a in st.get("agents", []) if a in al_models.POLICY)


def findings_text(st):
    """`2 BLOCKING · 3 NON_BLOCKING` — only the BLOCKING count drives a loop."""
    f = st["findings"]
    return f"{f.get('blocking', 0)} BLOCKING · {f.get('nonblocking', 0)} NON_BLOCKING"


def rework_text(st):
    """`Rework 1/2` while the current batch has sent defects back to AL-Engineer."""
    rw = st.get("rework") or {}
    if not rw.get("used"):
        return ""
    tail = ''
    recovery=st.get('rework_recovery')
    if recovery:
        tail=f" · total {rw['total']} · automatic {recovery['automatic_authorizations']}/2"
        if recovery['pending']:tail+=' · Architect reassessment pending'
        elif recovery['stop']:tail+=' · '+recovery['stop']['reason']
    return f"Rework {rw['used']}/{rw.get('max', 2)}"+tail


def _render_line(st):
    r, c, m = st["review_iteration"], st["correction_iteration"], st["max_correction_iterations"]
    counter = f"Review {r} · Corrections {c}/{m}"
    if rework_text(st):
        counter += f" · {rework_text(st)}"
    status, phase = st["status"], st.get("phase")
    if status == "APPROVED":
        return f"AL │ APPROVED │ {counter}"
    if status in {"WAITING_FOR_USER", "INTERRUPTED", "TRANSFERRED"}:
        return f"AL │ {status} │ {st.get('stop_reason') or 'reason missing'} │ {counter}"
    if status == "STOPPED_WITH_BLOCKERS":
        return f"AL │ STOPPED_WITH_BLOCKERS │ {st.get('stop_reason') or 'reason missing'} │ {counter}"
    if status == "SPEC_BLOCKED":
        return "AL │ SPEC_BLOCKED │ specification not implementable as written"
    if status == "AGENT_FAILED":
        f = st["failure"] or {}
        return f"AL │ AGENT FAILED │ {f.get('agent', '?')} │ {PHASE_LABEL.get(f.get('phase'), '?')}"
    el = phase_elapsed(st, phase)
    tail = f" │ {fmt_elapsed(el)}" if el is not None else ""
    fan = st.get("fanout")
    if fan:
        n = fan.get("count") or 0
        return (f"AL │ {PHASE_LABEL.get(phase, phase)} │ {FANOUT_TITLE.get(fan.get('kind'), 'Fan-out')} │ "
                f"{n} scout{'s' if n != 1 else ''} ∥ read-only{tail}")
    if phase == "VERIFICATION":
        return f"AL │ Verification barrier │ all writes complete │ {counter}{tail}"
    if phase == "REVIEW":
        rs = st.get("review_status")
        if rs == "CHANGES_REQUIRED":
            return f"AL │ Review #{r} │ {findings_text(st)}{tail}"
        if rs == "APPROVED":
            return f"AL │ Review #{r} │ APPROVED{tail}"
        arch = al_models.resolve(st, "al-architect")
        return (f"AL │ Review #{r} │ AL-Architect │ "
                f"{arch['model'].capitalize()}/{arch['effort'].capitalize()} │ checking{tail}")
    par = " ∥" if (st.get("exec_mode") or {}).get(phase) == "parallel" else ""
    if phase == "REWORK":
        return f"AL │ Rework │ {agents_short(st)} │ {counter}{tail}"
    if phase == "CORRECTION":
        return f"AL │ Correction {c}/{m} │ {agents_short(st)}{par} │ {findings_text(st)}{tail}"
    if phase == "FINAL_VERIFICATION":
        return f"AL │ Final verification │ Review {r}{tail}"
    cls = f" │ {st['classification']}" if st.get("classification") else ""
    roles = [a for a in st.get("agents", []) if a in al_models.POLICY]
    if len(roles) > 1:
        return f"AL │ {PHASE_LABEL.get(phase, phase)}{cls} │ {agents_short(st)}{par} │ {counter}{tail}"
    me = f" │ {st['model'].capitalize()}/{st['effort'].capitalize()}" if st.get("model") else ""
    return f"AL │ {PHASE_LABEL.get(phase, phase)}{cls} │ {st['agent']}{me}{par} │ {counter}{tail}"


def continuity_status(st):
    """Recorded facts only: presentation never probes capability or grants approval."""
    if 'implementation_readiness' not in st and 'operational_issues' not in st:
        return None
    obligations = st.get('implementation_readiness', {}).get('obligations', [])
    pending = len(set(obligations))
    if st.get('proof_admission', {}).get('status') == 'pending' and not pending:
        pending = 1
    issues = sum(v.get('state') != 'Resolved' for v in st.get('operational_issues', {}).values())
    decisions = sum(v.get('state') != 'Resolved' and v.get('kind') in {
        'specification', 'developer_engineering_decision'
    } for v in st.get('integration', {}).get('blockers', {}).values())
    if st.get('status') == 'APPROVED':
        verification = 'approved (recorded)'
    elif pending or issues:
        verification = f'pending: {pending} recorded obligation(s), {issues} operational issue(s)'
    else:
        verification = 'approval not recorded'
    implementation = ('scoped work may continue; authority checks apply'
                      if st.get('status') == 'RUNNING' and st.get('spec_status') == 'SPEC_READY'
                      else 'recorded workflow state ' + str(st.get('status', 'unknown')))
    return {'implementation': implementation, 'verification': verification,
            'developer_decisions': decisions, 'pending': pending, 'issues': issues}


def render_line(st):
    result = _render_line(st)
    value = continuity_status(st)
    if value is not None and st.get('status') != 'APPROVED':
        result += ' │ verification ' + ('pending' if value['pending'] or value['issues'] else 'not approved')
        if value['developer_decisions']:
            result += f" │ {value['developer_decisions']} scoped developer decision(s)"
    return result


def render_panel(st):
    rule = "━" * 42
    lines = [rule, "AL", f"Task: {st['task_title']}"]
    if st.get("classification"):
        lines.append(f"Classification: {st['classification']}")
    lines.append("")
    parallel_ran = False
    skipped = st.get("skipped_phases") or []
    optional = st.get("optional_phases") or []
    for p in MAIN_PHASES:
        not_needed = (p in optional and p not in st["phase_timings"]
                      and st["status"] in ("APPROVED", "STOPPED_WITH_BLOCKERS"))
        if p in skipped:
            mark = "–"
        elif not_needed:
            mark = "–"
        elif p == st["phase"] and st["status"] == "RUNNING":
            mark = "●"
        elif p in st["completed_phases"]:
            mark = "✓"
        elif p == st["phase"]:
            mark = "●"
        else:
            mark = "○"
        role = PHASE_AGENT.get(p)
        if p in skipped:
            who = "skipped (path)"
        elif not_needed:
            who = "not needed"
        elif role:
            slot = al_models.resolve(st, role)
            who = f"{slot['model'].capitalize()} / {slot['effort'].upper()}"
            if p in optional:
                who += " (if needed)"
        else:
            who = "AL (barrier)"
        if (st.get("exec_mode") or {}).get(p) == "parallel":
            who += " ∥"
            parallel_ran = True
        el = phase_elapsed(st, p)
        t = fmt_elapsed(el).rjust(8) if el is not None and (mark != "○") else ""
        lines.append(f"{mark} {PHASE_LABEL[p]:<22}{who:<18}{t}")
    if (st.get("exec_mode") or {}).get("CORRECTION") == "parallel":
        parallel_ran = True
    fanouts = st.get("fanouts") or []
    if fanouts:
        n = sum(f.get("count") or 0 for f in fanouts)
        lines.append(f"  read-only scouts       {n} in {len(fanouts)} fan-out"
                     f"{'s' if len(fanouts) != 1 else ''} ∥")
    lines += ["", f"Review: {st['review_iteration']} · Corrections: {st['correction_iteration']} / "
              f"{st['max_correction_iterations']}", f"Status: {st['status'] if st['status'] != 'RUNNING' else st['phase']}"]
    rw = st.get("rework") or {}
    if rw.get("total"):
        lines.append(f"Rework (returns to Engineer before review): {rw['total']} total"
                     + (f" · this batch {rework_text(st).split(' ', 1)[1]}" if rework_text(st) else ""))
    entries = (st.get("evidence") or {}).get("entries") or []
    if entries:
        reused = sum(1 for e in entries if e.get("tier") != "preflight")
        lines.append(f"Evidence: {reused} recorded run(s) · ledger `evidence status`")
    if st.get("handoffs"):
        lines.append(f"Agent handoffs: {st['handoffs']}")
    if st.get("oscillations"):
        lines.append(f"Oscillation stops: {len(st['oscillations'])}")
    if st.get("approved_with_notes"):
        lines.append(f"Approved with {st['approved_with_notes']} NON_BLOCKING note(s)")
    if parallel_ran:
        lines.append("∥ = ran in parallel")
    if skipped or any(p in optional and p not in st["phase_timings"] for p in MAIN_PHASES):
        lines.append("– = not run on this task's path")
    value = continuity_status(st)
    if value is not None:
        lines.append('Implementation: ' + value['implementation'])
        lines.append('Verification: ' + value['verification'])
        if value['developer_decisions']:
            lines.append(f"Developer decisions: {value['developer_decisions']} (affected scope only)")
    lines.append(rule)
    return "\n".join(lines)

