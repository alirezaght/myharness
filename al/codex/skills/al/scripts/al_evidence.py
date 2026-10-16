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


def _create(sid, expected_run, **fields):
    """Allocate the entry id and record it as RUNNING, atomically — refused when
    the session was restarted since the caller read the state."""
    with al_store.locked(sid):
        st = al_store.load(sid)
        if st.get("run_id") != expected_run:
            sys.exit("al_state: the AL run was restarted meanwhile — nothing recorded; re-issue the command")
        spec = dict(fields, env=fields.get("env_names", []))
        planned = L.ledger(st)["plan"].get(spec["area"])
        if planned and not L.same_command(spec, planned): sys.exit("al_state: evidence plan changed before reservation")
        al_writers.admit_check(st, spec)
        entry = L.new_entry(st, **fields)
        entry["reservation"] = secrets.token_hex(16)
        entry["fp_before"] = _fingerprint(entry)
        al_store.save(sid, st)
    return entry


def _fingerprint(entry):
    try:
        return L.scope_fingerprint(entry["scope"], entry["env"], entry.get("sources", [])) if entry["scope"] else None
    except L.FingerprintError as exc:
        entry["fingerprint_error"] = str(exc)
        return "unavailable"


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def _stop(proc):
    """SIGTERM the run's process group, give it 10 s, then SIGKILL whatever is
    left of the group — descendants that ignore SIGTERM included."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return proc.wait()


def _execute(sid, entry, timeout, commit=True):
    """Run entry['argv'] in entry['cwd'], streaming everything into the log."""
    cwd = al_store.REPO_ROOT / entry["cwd"]
    name = f"{entry.get('run') or 'run'}-{entry['id']}-{entry['area']}-{entry['kind']}.log"
    log = L.log_dir(sid) / name.replace("/", "_")
    entry["log"] = str(log)
    timed_out, code = False, None
    with open(log, "w") as out:
        out.write(f"$ {' '.join(entry['argv'])}\n# cwd: {entry['cwd']}\n# started: {time.ctime()}\n\n")
        out.flush()
        start = out.tell()
        try:
            try:gaps=L.check_scope(entry['scope'],entry.get('sources',[]),entry['cwd']) if entry['scope'] else []
            except L.FingerprintError as exc:raise PermissionError(str(exc)) from exc
            if entry.get('fingerprint_error') or gaps:
                raise PermissionError(entry.get('fingerprint_error') or '; '.join(gaps))
            proc = subprocess.Popen(entry["argv"], cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except FileNotFoundError:
            code = 127
        except (PermissionError, NotADirectoryError) as exc:
            code = 126
            out.write(f"AL: cannot start: {exc}\n")
        else:
            entry["pid"] = proc.pid
            _commit(sid, entry)
            previous = signal.signal(signal.SIGTERM, _raise_interrupt)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                code = _stop(proc)
            except KeyboardInterrupt:  # Ctrl-C or a killed background command
                entry.update(exit=_stop(proc), status="INFRA", note="cancelled — the run was interrupted", interrupted=True,
                             ended_at=time.time(), log_sha256=None)
                out.write("\n# cancelled\n")
                out.flush()
                _commit(sid, entry)
                raise
            finally:
                signal.signal(signal.SIGTERM, previous)
        out.seek(0, os.SEEK_END)
        end = out.tell()
        out.write(f"\n# exit: {code}{' (timeout)' if timed_out else ''}\n")
    raw = log.read_bytes()
    text = raw.decode(errors="replace")
    child_output = raw[start:end].decode(errors="replace")  # never AL's own header/footer
    entry["exit"] = code
    entry["counts"] = L.parse_counts(child_output) if entry["kind"] == "test" else {}
    entry["fp_after"] = _fingerprint(entry)
    entry["status"], entry["note"] = L.classify(entry["kind"], code, entry["counts"], timed_out,
                                                entry["fp_before"], entry["fp_after"])
    if entry.get("fingerprint_error"):
        entry["status"], entry["note"] = "INFRA", "cannot fingerprint the scope: " + entry["fingerprint_error"]
    entry["ended_at"] = time.time()
    entry["log_sha256"] = L.file_sha(log)
    if commit: _commit(sid, entry)
    if entry.get("rejected"):
        sys.exit(f"al_state: {entry['id']} not recorded — {entry['rejected']}; re-run it in the current run")
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
    planned = L.ledger(st)["plan"].get(a.area)
    if a.command_argv:
        scope = L.norm_scope(a.scope)
        if not scope:
            sys.exit("al_state: an ad-hoc evidence run needs --scope (what this command depends on)")
        sources=_sources_file(a)
        _require_scope(scope,sources,a.cwd or ".")
        spec = {"area": a.area, "kind": a.kind, "tier": a.tier or "focused", "argv": a.command_argv,
                "cwd": a.cwd or ".", "scope": scope, "sources": sources, "env": _split(a.env),
                "preflight": a.preflight or None, "needs": _split(a.needs)}
    elif planned:
        spec = dict(planned)
        if getattr(a,"sources_file",None):spec["sources"]=_sources_file(a)
    else:
        sys.exit(f"al_state: no planned check named {a.area}; plan it or pass `-- <argv>`")
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
    p.set_defaults(fn=cmd_evidence_run)
    ev.add_parser("status").set_defaults(fn=cmd_evidence_status)
    p = ev.add_parser("require"); p.add_argument("--tier", choices=["focused", "broad"])
    p.add_argument("--areas"); p.set_defaults(fn=cmd_evidence_require)
    p = ev.add_parser("validate"); p.add_argument("--claims", required=True)
    p.set_defaults(fn=cmd_evidence_validate)
