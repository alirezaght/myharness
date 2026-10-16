#!/usr/bin/env python3
"""AL runtime preflight and verification overlap.

  preflight  runtime prerequisites (native libraries, loopback, an owned
             disposable DB/sidecar, required env vars by name), checked once
             before any expensive suite                               (exit 15)
  overlap    may a narrowly scoped fix be written while a check runs? (exit 5)
"""
import os
import shutil
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_ledger as L  # noqa: E402
import al_parallel  # noqa: E402
import al_store  # noqa: E402
from al_evidence import _commit, _create, _execute, _split  # noqa: E402

BARRIER_PHASES = ("VERIFICATION", "REVIEW", "FINAL_VERIFICATION", "DONE")


def _check(spec):
    """(ok, detail) for one built-in preflight check. Never prints a value."""
    kind, _, arg = spec.partition(":")
    if kind == "loopback":
        try:
            with socket.socket() as srv:
                srv.bind(("127.0.0.1", 0))
                srv.listen(1)
                with socket.create_connection(srv.getsockname(), timeout=2):
                    pass
            return True, "loopback bind + connect"
        except OSError as exc:
            return False, f"loopback not usable ({exc.__class__.__name__})"
    if kind == "port":
        host, _, port = arg.rpartition(":")
        try:
            with socket.create_connection((host or "127.0.0.1", int(port)), timeout=2):
                return True, f"{host or '127.0.0.1'}:{port} accepts connections"
        except (OSError, ValueError):
            return False, f"nothing listening on {host or '127.0.0.1'}:{port}"
    if kind == "cmd":
        return (bool(shutil.which(arg)), f"`{arg}` on PATH" if shutil.which(arg) else f"`{arg}` not on PATH")
    if kind == "env":
        return (bool(os.environ.get(arg)), f"{arg} is set" if os.environ.get(arg) else f"{arg} is unset or empty")
    if kind == "file":
        ok = (al_store.REPO_ROOT / arg).exists()
        return ok, f"{arg} {'exists' if ok else 'is missing'}"
    return False, f"unknown check `{spec}` (use loopback, port:[HOST:]PORT, cmd:X, env:X, file:X)"


def cmd_preflight(a, sid):
    """Runtime prerequisites, once, before expensive suites. Exit 15 on a miss."""
    if not a.check and not a.command_argv and not (a.validate_isolation): sys.exit("al_state: preflight requires explicit checks or bootstrap argv")
    env = _split(a.env) + [c.split(":", 1)[1] for c in a.check or [] if c.startswith("env:")]
    entry = _create(sid, al_store.load(sid).get("run_id"), area=a.name, kind="preflight", tier="preflight",
                    argv=a.command_argv or ["(built-in checks)"], cwd=a.cwd or ".",
                    scope=[], env_names=sorted(set(env)))
    results = [(c, *_check(c)) for c in a.check or []]
    if (a.validate_isolation):
        common=Path(al_store.load(sid)["identity"]["common_dir"])
        results.append(("isolated worktree", common != al_store.REPO_ROOT / ".git", "separate worktree identity"))
    if a.command_argv:
        _execute(sid, entry, a.timeout, commit=False)
        results.append((" ".join(a.command_argv), entry["status"] == "PASS", entry["note"] or "exit 0"))
    log = L.log_dir(sid) / f"{entry.get('run') or 'run'}-{entry['id']}-{a.name}-preflight.log".replace("/", "_")
    lines = [f"{'ok ' if ok else 'MISS'} {name} — {detail}" for name, ok, detail in results]
    prior = Path(entry["log"]).read_text(errors="replace") if entry.get("log") else ""
    log.write_text(prior + "\n".join(lines) + "\n")
    failed = [r for r in results if not r[1]]
    entry.update(log=str(log), log_sha256=L.file_sha(log), exit=1 if failed else 0,
                 status="INFRA" if failed else "PASS", ended_at=time.time(),
                 note="; ".join(r[2] for r in failed), fp_after=L.scope_fingerprint([], entry["env"]))
    _commit(sid, entry)
    if entry.get("rejected"):
        sys.exit(f"al_state: preflight {a.name} not recorded — {entry['rejected']}; re-run it")
    print("\n".join(lines))
    if failed:
        print(f"AL · preflight {a.name} FAILED · fix the environment before any suite runs "
              f"(log {log})", file=sys.stderr)
        sys.exit(15)
    print(f"AL · preflight {a.name} PASS · {len(results)} check(s) · {entry['id']}")


def cmd_overlap(a, sid):
    """May a narrowly scoped fix be written while check `--area` is running?

    Refused (exit 5) at a barrier, when nothing scoped is running, when the fix's
    files touch the running command's scope (code, fixtures, contracts, deps,
    config), or when a fix command mutates shared tree state. An allowed overlap
    is still checked after the fact: a run whose scope moved ends TORN."""
    st = al_store.load(sid)
    root = al_store.REPO_ROOT.resolve()
    fix_files, reasons = [], []
    for raw in al_parallel.parse_paths(a.fix_files):
        target = (root / raw).resolve()  # absolute or relative → repository-relative
        if target == root:
            reasons.append(f"fix file {raw} is the whole repository")
        elif root not in target.parents:
            reasons.append(f"fix file {raw} is outside the repository")
        else:
            fix_files.append(target.relative_to(root).as_posix())
    running = L.latest(st, a.area)
    if st.get("phase") in BARRIER_PHASES:
        reasons.append(f"phase {st.get('phase')} is a barrier — the candidate must be frozen")
    if not running or L.assess(running)[0] != "RUNNING":
        reasons.append(f"no running check named {a.area}")
    elif "." in running["scope"]:
        reasons.append("the running check's scope is the whole repository")
    if not fix_files:
        reasons.append("the fix declared no files")
    if running and not reasons:
        active = [e for e in L.ledger(st)["entries"] if L.assess(e)[0] == "RUNNING"]
        for e in active:  # every running check, not only the named one
            if "." in e["scope"]:
                reasons.append(f"running check {e['id']} ({e['area']}) covers the whole repository")
            for fa, sb in al_parallel.overlaps(fix_files, e["scope"]):
                reasons.append(f"fix file {fa} lies in running check {e['id']} ({e['area']}) scope ({sb})")
    for cmd, why in al_parallel.shared_state_hits(al_parallel.parse_commands(a.fix_commands)):
        reasons.append(f"fix command `{cmd}` — {why}")
    st.setdefault("overlaps", []).append({"area": a.area, "files": fix_files, "allowed": not reasons,
                                          "reasons": reasons, "at": time.time()})
    al_store.save(sid, st)
    if reasons:
        print("AL · Overlap refused — stay sequential", file=sys.stderr)
        for r in reasons:
            print(f"  {r}", file=sys.stderr)
        sys.exit(5)
    print(f"AL · Overlap allowed · fix ({', '.join(fix_files)}) is outside {running['id']}'s scope · "
          f"the run ends TORN if its scope moves anyway")


def register(sub):
    p = sub.add_parser("preflight"); p.add_argument("--name", required=True)
    p.add_argument("--validate-isolation", action="store_true"); p.add_argument("--check", action="append"); p.add_argument("--env"); p.add_argument("--cwd")
    p.add_argument("--timeout", type=float); p.set_defaults(fn=cmd_preflight)
    p = sub.add_parser("overlap"); p.add_argument("--area", required=True)
    p.add_argument("--fix-files", default=""); p.add_argument("--fix-commands", default="")
    p.set_defaults(fn=cmd_overlap)
