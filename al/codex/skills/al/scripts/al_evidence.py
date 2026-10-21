#!/usr/bin/env python3
"""AL evidence commands — run verification through the ledger, reuse what is
still valid, and refuse to advance on evidence that is missing or stale.

  evidence plan     register a required check (area, tier, cwd, scope, argv)
  evidence run      run a planned or ad-hoc check, capture its full log, record
                    it; a VALID PASS for the same command is reused, not re-run
                                     (exit 15 when its preflight / needs are unmet)
  evidence status   every entry with its verdict against today's tree
  evidence require  every planned check of a tier is a VALID PASS   (exit 13)
  evidence validate an agent's claimed ids/statuses match the ledger (exit 14)

`preflight` and `overlap` live in al_preflight.py.

The state script stays observational: these commands run exactly the argv they
are given and never edit code, tests or git.
"""
import os
import json
import al_sources as Sources
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_ledger as L  # noqa: E402
import al_store  # noqa: E402
import al_writers
import secrets


def _split(raw):
    return [x.strip() for x in (raw or "").replace("\n", ",").split(",") if x.strip()]


def _commit(sid, entry):
    """Re-load under the lock and write back one entry: a long run must not
    clobber state other commands wrote meanwhile."""
    with al_store.locked(sid):
        st = al_store.load(sid)
        if st.get("run_id") != entry.get("run"):
            entry["rejected"] = "it belongs to an earlier run of this session (AL was restarted)"
            return
        entries = L.ledger(st)["entries"]
        for i, e in enumerate(entries):
            if e["id"] == entry["id"] and e.get("reservation") == entry.get("reservation") and e["status"] == "RUNNING":
                entries[i] = entry
                break
        else:
            entry["rejected"] = "unknown, completed, or mismatched reservation"
            return
        al_store.save(sid, st)


def _create(sid, expected_run, *, gate_context=None, **fields):
    """Allocate the entry id and record it as RUNNING, atomically — refused when
    the session was restarted since the caller read the state."""
    with al_store.locked(sid):
        st = al_store.load(sid)
        guidance = sys.modules.get("al_recovery_guidance")
        if guidance is not None:
            guidance.check_active(st)
        if st.get("run_id") != expected_run:
            sys.exit("al_state: the AL run was restarted meanwhile — nothing recorded; re-issue the command")
        spec = dict(fields, env=fields.get("env_names", []))
        planned = L.ledger(st)["plan"].get(spec["area"])
        if planned and not L.same_command(spec, planned): sys.exit("al_state: evidence plan changed before reservation")
        destination = None
        if gate_context:
            from al_gate_continuation import admit_destination
            destination = admit_destination(st, gate_context)
        al_writers.admit_check(st, spec, gate_context=destination)
        entry = L.new_entry(st, **fields)
        if destination is not None: entry['gate_reservation'] = destination
        entry["reservation"] = secrets.token_hex(16)
        entry["fp_before"] = _fingerprint(entry)
        al_store.save(sid, st)
    return entry


def _fingerprint(entry):
    try:
        return L.scope_fingerprint(entry["scope"], entry["env"], entry.get("sources", []), entry["cwd"]) if entry["scope"] else None
    except L.FingerprintError as exc:
        entry["fingerprint_error"] = str(exc)
        return "unavailable"


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def _stop(proc):
    """Stop only the process group created by this live child handle."""
    errors = []
    for number, grace in ((signal.SIGTERM, 1), (signal.SIGKILL, 2)):
        try:
            os.killpg(proc.pid, number)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        # Escalation also reaps descendants that ignored the first group signal.
    unresolved = proc.poll() is None
    return {'exit': proc.poll(), 'errors': errors, 'unresolved': unresolved}


def _execute(sid, entry, timeout, commit=True):
    """Finalize every handled outcome before publishing its reservation receipt."""
    name = f"{entry.get('run') or 'run'}-{entry['id']}-{entry['area']}-{entry['kind']}.log"
    log = L.log_dir(sid) / name.replace('/', '_')
    entry['log'] = str(log)
    code, timed_out, interrupted, failure = None, False, None, None
    cleanup, proc, previous = None, None, {}
    start = end = 0
    try:
        with log.open('w') as out:
            out.write(f"$ {' '.join(entry['argv'])}\n# cwd: {entry['cwd']}\n# started: {time.ctime()}\n\n")
            out.flush(); start = out.tell()
            try:
                relative = Sources.canonical_cwd(al_store.REPO_ROOT, entry['cwd'])
                gaps = L.check_scope(entry['scope'], entry.get('sources', []), relative) if entry['scope'] else []
                if entry.get('fingerprint_error') or gaps:
                    raise PermissionError(entry.get('fingerprint_error') or '; '.join(gaps))
                for number in (signal.SIGINT, signal.SIGTERM):
                    previous[number] = signal.signal(number, _raise_interrupt)
                proc = subprocess.Popen(entry['argv'], cwd=al_store.REPO_ROOT.resolve() / relative,
                                        stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
                entry['pid'] = proc.pid
                entry['process'] = {'pid': proc.pid, 'pgid': proc.pid, 'ownership': 'live-Popen-start_new_session'}
                if commit:
                    _commit(sid, entry)
                try:
                    code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    cleanup = _stop(proc); code = cleanup['exit']
            except KeyboardInterrupt as exc:
                interrupted = exc
                if proc is not None:
                    cleanup = _stop(proc); code = cleanup['exit']
            except FileNotFoundError as exc:
                code = 127; failure = str(exc)
            except (OSError, Sources.SourceError, L.FingerprintError) as exc:
                code = 126; failure = str(exc)
                if proc is not None and proc.poll() is None:
                    cleanup = _stop(proc)
            finally:
                for number, handler in previous.items():
                    signal.signal(number, handler)
                out.flush(); out.seek(0, os.SEEK_END); end = out.tell()
                footer = {'exit': code, 'timeout': timed_out, 'interrupted': interrupted is not None,
                          'cleanup': cleanup, 'error': failure}
                out.write('\n# termination: ' + json.dumps(footer, sort_keys=True) + '\n')
                out.flush()
    except OSError as exc:
        failure = 'log finalization failed: ' + str(exc)
    entry['counts'] = {}
    try:
        raw = log.read_bytes()
        text = raw.decode(errors='replace')
        if entry['kind'] == 'test':
            entry['counts'] = L.parse_counts(raw[start:end].decode(errors='replace'))
        entry['log_sha256'] = L.file_sha(log)
    except OSError as exc:
        text = ''
        entry['log_sha256'] = None
        failure = 'log provenance unavailable: ' + str(exc)
    entry['fp_after'] = _fingerprint(entry)
    status, note = L.classify(entry['kind'], code, entry['counts'], timed_out, entry['fp_before'], entry['fp_after'])
    if interrupted is not None:
        status, note = 'INFRA', 'cancelled — the run was interrupted'
        entry['interrupted'] = True
    if cleanup:
        entry['cleanup'] = cleanup
        if cleanup['unresolved'] or cleanup['errors']:
            status = 'INFRA'
            note = (note + '; ' if note else '') + 'owned-process cleanup uncertain'
    if failure or entry.get('fingerprint_error'):
        status = 'INFRA'
        note = (note + '; ' if note else '') + str(failure or entry['fingerprint_error'])
    entry.update(exit=code, status=status, note=note, ended_at=time.time())
    if commit:
        _commit(sid, entry)
    if entry.get('rejected'):
        sys.exit(f"al_state: {entry['id']} not recorded — {entry['rejected']}; re-run it in the current run")
    if interrupted is not None:
        raise interrupted
    return text


def _require_scope(scope, sources=(), cwd="."):
    try:
        problems = L.check_scope(scope,sources,cwd)
    except L.FingerprintError as exc:
        problems = [str(exc)]
    if problems:
        sys.exit("al_state: invalid --scope — " + "; ".join(problems))


def _sources_file(a):
    if not getattr(a,'sources_file',None):return []
    try:
        value=json.loads(Path(a.sources_file).read_bytes())
        if not isinstance(value,list):raise Sources.SourceError('sources must be an array')
        return Sources.validate_sources(value)
    except (OSError,ValueError) as exc:sys.exit('al_state: invalid --sources-file: '+str(exc))


def cmd_evidence_plan(a, sid):
    st = al_store.load(sid)
    argv, scope = a.command_argv, L.norm_scope(a.scope)
    if not argv or not scope or not a.area:
        sys.exit("al_state: evidence plan needs --area, --scope and `-- <argv>`")
    if a.tier not in ("focused", "broad"):
        sys.exit("al_state: --tier must be focused or broad")
    sources=_sources_file(a)
    _require_scope(scope,sources,a.cwd or ".")
    L.ledger(st)["plan"][a.area] = {
        "area": a.area, "kind": a.kind, "tier": a.tier, "argv": argv, "cwd": a.cwd or ".",
        "scope": scope, "sources": sources, "env": _split(a.env), "preflight": a.preflight or None, "needs": _split(a.needs)}
    al_store.save(sid, st)
    print(f"AL · evidence planned · {a.area} · {a.tier} · `{' '.join(argv)}` · scope {', '.join(scope)}")


def cmd_evidence_run(a, sid):
    st = al_store.load(sid)
    guidance = sys.modules.get("al_recovery_guidance")
    if guidance is not None:
        guidance.check_active(st)
    planned = L.ledger(st)["plan"].get(a.area)
    if a.command_argv:
        scope = L.norm_scope(a.scope)
        if not scope:
            sys.exit("al_state: an ad-hoc evidence run needs --scope (what this command depends on)")
        sources=_sources_file(a)
        spec = {"area": a.area, "kind": a.kind, "tier": a.tier or "focused", "argv": a.command_argv,
                "cwd": a.cwd or ".", "scope": scope, "sources": sources, "env": _split(a.env),
                "preflight": a.preflight or None, "needs": _split(a.needs)}
    elif planned:
        spec = dict(planned)
        if getattr(a,"sources_file",None):spec["sources"]=_sources_file(a)
    else:
        sys.exit(f"al_state: no planned check named {a.area}; plan it or pass `-- <argv>`")
    _require_scope(spec["scope"],spec.get("sources",[]),spec["cwd"])
    if not (al_store.REPO_ROOT / spec["cwd"]).is_dir():
        sys.exit(f"al_state: cwd {spec['cwd']} does not exist under {al_store.REPO_ROOT}")
    missing = L.unmet(st, spec)
    if missing:
        print(f"AL · evidence run refused · {spec['area']}", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        sys.exit(15)
    prev = L.latest(st, spec["area"], spec)
    if prev and not a.fresh and L.entry_verdict(st, prev)[0] == "VALID":
        from al_execution_tasks import admit_check as execution_check
        execution_check(st, spec)
        print(f"AL · evidence {prev['id']} reused · {spec['area']} · PASS · {L.counts_text(prev)} · "
              f"scope fingerprint unchanged ({prev['fp_after']}) · log {prev['log']}")
        return
    entry = _create(sid, st.get("run_id"), area=spec["area"], kind=spec["kind"], tier=spec["tier"], argv=spec["argv"],
                    cwd=spec["cwd"], scope=spec["scope"], env_names=spec["env"],
                    preflight=spec.get("preflight"), needs=spec.get("needs") or (), sources=spec.get("sources", []))
    text = _execute(sid, entry, a.timeout)
    tail = text.splitlines()[-a.tail:] if a.tail else []
    if tail:
        print("\n".join(tail))
    note = f" · {entry['note']}" if entry["note"] else ""
    print(f"AL · evidence {entry['id']} · {entry['area']} · {entry['status']} · {L.counts_text(entry)}"
          f"{note} · log {entry['log']}")
    if entry["status"] != "PASS":
        sys.exit(1)


def cmd_evidence_status(a, sid):
    st = al_store.load(sid)
    entries = L.ledger(st)["entries"]
    if not entries:
        print("AL · evidence ledger empty")
    for e in entries:
        print(L.row(e, *L.entry_verdict(st, e)))
    for area, spec in L.ledger(st)["plan"].items():
        if not L.latest(st, area, spec):
            print(f"--   {area:<18} {spec['tier']:<9} NOT_RUN     planned `{' '.join(spec['argv'])}`")


def cmd_evidence_require(a, sid):
    """Exit 13 unless every planned check of the tier is a VALID PASS today."""
    st = al_store.load(sid)
    plan = L.ledger(st)["plan"]
    areas = _split(a.areas) or [k for k, v in plan.items() if a.tier in (None, v["tier"])]
    repros = [] if a.areas or a.tier == "focused" else L.repro_gaps(st)
    if not areas and not repros:
        sys.exit("al_state: required evidence plan is empty")
    bad = []
    for area in areas:
        e, verdict, why = L.planned_verdict(st, area, plan.get(area))
        print(L.row(e, verdict, why) if e else f"--   {area:<18} {'?':<9} NOT_RUN     {why}")
        if verdict != "VALID":
            bad.append(area)
    for label, verdict in repros:
        print(f"--   {label:<18} {'repro':<9} {verdict:<11} re-run the repro command; it must pass")
        bad.append(label)
    if bad:
        print(f"AL · evidence incomplete · {', '.join(bad)} — run (or re-run) them; do not advance",
              file=sys.stderr)
        sys.exit(13)
    print(f"AL · evidence complete · {len(areas)} check(s) VALID against the current tree")


def cmd_evidence_validate(a, sid):
    """Check an agent's VERIFICATION claims (`E3=PASS,E4=FAIL`) against the ledger."""
    st = al_store.load(sid)
    claims = [c.split("=", 1) for c in _split(a.claims)]
    if not claims or any(len(c) != 2 for c in claims):
        sys.exit("al_state: --claims must look like E3=PASS,E4=FAIL")
    bad = []
    for eid, claimed in claims:
        e = L.find(st, eid.strip())
        if not e:
            bad.append(f"{eid}: no such ledger entry — the run was not recorded")
            continue
        verdict, why = L.entry_verdict(st, e)
        if verdict in ("LOG_MISSING", "LOG_ALTERED", "RUNNING", "INTERRUPTED", "UNVERIFIABLE"):
            bad.append(f"{eid}: {verdict} — {why}")
        elif e["status"] != claimed.strip().upper():
            bad.append(f"{eid}: claimed {claimed.strip().upper()}, ledger says {e['status']}")
        elif e["status"] == "PASS" and verdict != "VALID":
            bad.append(f"{eid}: claimed PASS but it is {verdict} for the current tree — {why}; re-run it")
        else:
            print(L.row(e, verdict, why))
    if bad:
        for b in bad:
            print(f"AL · evidence claim rejected · {b}", file=sys.stderr)
        sys.exit(14)
    print(f"AL · {len(claims)} evidence claim(s) match the ledger")


def register(sub):
    def common(p):
        p.add_argument("--area", required=True); p.add_argument("--kind", default="test", choices=L.KINDS[:-1])
        p.add_argument("--tier"); p.add_argument("--cwd"); p.add_argument("--scope")
        p.add_argument("--sources-file"); p.add_argument("--env"); p.add_argument("--preflight"); p.add_argument("--needs")
    ev = sub.add_parser("evidence").add_subparsers(dest="action", required=True)
    p = ev.add_parser("plan"); common(p); p.set_defaults(fn=cmd_evidence_plan, tier="broad")
    p = ev.add_parser("run"); common(p); p.add_argument("--fresh", action="store_true")
    p.add_argument("--timeout", type=float); p.add_argument("--tail", type=int, default=40)
    p.add_argument("--recovery-file",dest="operation_recovery_file")
    p.set_defaults(fn=cmd_evidence_run)
    ev.add_parser("status").set_defaults(fn=cmd_evidence_status)
    p = ev.add_parser("require"); p.add_argument("--tier", choices=["focused", "broad"])
    p.add_argument("--areas"); p.set_defaults(fn=cmd_evidence_require)
    p = ev.add_parser("validate"); p.add_argument("--claims", required=True)
    p.set_defaults(fn=cmd_evidence_validate)
