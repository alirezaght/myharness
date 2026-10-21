#!/usr/bin/env python3
"""AL evidence ledger — the record of every verification run, tied to the code
it actually tested.

A ledger entry is evidence only while all of this still holds:

  * the command ran through `evidence run` (argv, cwd, exit status and the full
    output were captured by AL, not retyped by an agent),
  * its log file still exists and still has the hash recorded at the end of the
    run,
  * its status is PASS (exit 0 and, for tests, a non-zero test count),
  * the source fingerprint of its declared scope is unchanged since the run —
    and did not move DURING the run either.

The scope is what the command depends on: the implementation, the fixtures, the
contracts, the dependency manifests and the runtime configuration files. A
change outside the scope leaves the entry valid (a web-only edit does not void a
service test run); a change inside it makes the entry STALE. Environment
variables named with `--env` are folded in as hashes — their values are never
stored or printed.

Pure helpers over the state dict plus the fingerprint/log I/O; the CLI lives in
al_evidence.py.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import al_store  # noqa: E402
import al_sources as Sources
import al_scope as S

STATUSES = ("PASS", "FAIL", "EMPTY", "INFRA", "TORN", "RUNNING")
TIERS = ("focused", "broad", "preflight")
KINDS = ("test", "typecheck", "lint", "build", "check", "preflight")
# Exit statuses that say "the command could not run", not "the code is wrong".
INFRA_EXIT = {126: "command not executable", 127: "command not found"}
COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|errors?|flaky|todo)\b", re.I)
# File/suite-level summaries ("Test Files  2 passed", "Test Suites: 1 passed") say
# nothing about how many tests ran; AL's own log header is not runner output.
NOT_TEST_COUNT_RE = re.compile(r"\bTest (?:Files|Suites)\b")  # anywhere: Turbo prefixes lines


def ledger(st):
    ev = st.setdefault("evidence", {})
    ev.setdefault("seq", 0)
    ev.setdefault("entries", [])
    ev.setdefault("plan", {})
    return ev


def log_dir(sid):
    d = al_store.RUNTIME_DIR / sid / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_file(h, path):
    """Content plus the executable bit. An unreadable file is not hashed as a
    constant — that would keep stale evidence VALID — it raises."""
    try:
        h.update(b"\0x" if os.stat(path).st_mode & 0o111 else b"\0-")
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        raise FingerprintError(f"cannot read {path}: {exc.strerror}") from exc


def file_sha(path):
    h = hashlib.sha256()
    _hash_file(h, path)
    return h.hexdigest()


def norm_scope(raw):
    """Repository-relative, normalised scope paths. An absolute path inside the
    repository becomes relative; one outside it is kept as given so
    check_scope reports it."""
    root = al_store.REPO_ROOT.resolve()
    out = []
    for chunk in (raw or "").replace("\n", ",").split(","):
        p = chunk.strip().strip('"').strip("'").rstrip("/")
        if not p:
            p = "." if chunk.strip() else ""
        elif os.path.isabs(p):
            target = Path(os.path.normpath(p))
            p = target.relative_to(root).as_posix() or "." if (target == root or root in target.parents) else p
        else:
            p = os.path.normpath(p)
        if p and p not in out:
            out.append(p)
    return out


class FingerprintError(RuntimeError):
    """git could not describe the scope — the result must not count as evidence."""


def _git_bytes(*args):
    try:
        r = subprocess.run(["git", *args], cwd=al_store.REPO_ROOT, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FingerprintError(f"git {args[0]} failed: {exc.__class__.__name__}") from exc
    if r.returncode != 0:
        raise FingerprintError(f"git {args[0]} exited {r.returncode}: "
                               f"{r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout


def check_scope(scope, sources=(), cwd='.', require_matches=True):
    root = al_store.REPO_ROOT.resolve()
    problems = []
    for p in scope:
        if p.startswith(":") or any(c in p for c in "*?["):
            problems.append(f"{p} is a pattern — list the files or directories (overlap checks compare paths)")
            continue
        target = (root / p).resolve()
        if not Path(p).is_absolute() and any((root / q).is_symlink() for q in _prefixes(p)):
            problems.append(f"{p} is or passes through a symlink — declare the real path")
        elif target != root and root not in target.parents:
            problems.append(f"{p} is outside the repository")
        elif require_matches and not target.exists() and not _git_bytes("ls-files", "-z", "--", p):
            # A deleted Git entry can still be an explicit dependency.
            if not _git_bytes('ls-tree','-r','--name-only','HEAD','--',p):problems.append(f"{p} matches nothing in the worktree or the index")
    if problems:return problems
    try:
        rows=S.source_rows()
        return Sources.capability_gaps(scope,sources,rows,cwd,root=root)
    except (Sources.SourceError,SystemExit) as exc:
        raise FingerprintError(str(exc)) from exc

def _prefixes(p):
    parts = Path(p).parts
    return [Path(*parts[: i + 1]) for i in range(len(parts))] if p != "." else []


def scope_fingerprint(scope, env_names=(), sources=(), cwd='.'):
    from al_analysis_cache import CURRENT
    from al_profile import span
    context=CURRENT.get()
    with span('scope-hashing'):
        if context is None:return _scope_fingerprint(scope,env_names,sources,cwd)
        from al_environment_observation import value_digest
        environment={name:value_digest(name) for name in env_names}
        return context.get_or_compute('fingerprint',[scope,list(env_names),sources,cwd,environment],lambda:_scope_fingerprint(scope,env_names,sources,cwd))


def _scope_fingerprint(scope, env_names=(), sources=(), cwd='.'):

    root = al_store.REPO_ROOT
    h = hashlib.sha256(json.dumps(sorted(scope)).encode())
    if not scope:
        return _env_digest(h, env_names)
    try:
        rows=S.source_rows()
        normalized=Sources.validate_sources(sources)
        gaps=Sources.capability_gaps(scope,normalized,rows,cwd,root=root)
        if gaps:raise FingerprintError('; '.join(gaps))
    except (Sources.SourceError,SystemExit) as exc:
        raise FingerprintError(str(exc)) from exc
    if normalized:
        h.update(b'\0sources:'+json.dumps(normalized,sort_keys=True).encode())
        h.update(b'\0pointers:'+json.dumps(Sources.pointer_dependencies(scope,normalized,rows),sort_keys=True).encode())
    links=Sources.boundaries(rows)
    listed = _git_bytes('ls-files','-z','--cached','--others','--exclude-standard','--',*scope)
    for rel in sorted(set(x for x in listed.split(b'\0') if x)):
        name=os.fsdecode(rel).rstrip('/')
        if any(Sources.contains(path,name) for path in links):continue
        h.update(b'\0file:'+rel)
        path=root/name
        if path.is_symlink():
            if path.is_dir():raise FingerprintError(name+' is a symlinked directory — declare its real path in the scope')
            h.update(b'\0link:'+os.fsencode(os.readlink(path)))
            if path.is_file():_hash_file(h,path)
        elif path.is_file():_hash_file(h,path)
        elif path.is_dir():raise FingerprintError(name+' is an unsupported listed directory')
        else:h.update(b'\0deleted')
    for p in sorted(scope):
        target=root/p
        if p!='.' and target.is_file() and not any(Sources.contains(path,p) for path in links):
            h.update(b'\0named:'+p.encode());_hash_file(h,target)
    return _env_digest(h,env_names)

def _env_digest(h, env_names):
    for name in sorted(env_names or ()):
        from al_environment_observation import value_digest
        digest = value_digest(name)
        h.update(f"\0env:{name}={digest}".encode())
    return h.hexdigest()[:16]


def parse_counts(text):
    """Best-effort test counts from a runner's output (pytest, vitest, jest,
    playwright). The last summary wins for each count, except failures and
    errors: any reported anywhere in the output are kept, so a composite command
    whose final suite passes cannot hide an earlier failure. Returns {} when no
    summary was recognised — 'unknown', never a guess."""
    counts, worst = {}, {}
    for line in text.splitlines():
        suite_line = bool(NOT_TEST_COUNT_RE.search(line))
        for n, word in COUNT_RE.findall(line):
            key = word.lower().rstrip("s") if word.lower().startswith("error") else word.lower()
            if key in ("failed", "error"):  # a failed suite or file is a failure, wherever reported
                worst[key] = max(worst.get(key, 0), int(n))
            if not suite_line:  # but suite/file passes are not test counts
                counts[key] = int(n)
    counts.update(worst)
    return counts


def classify(kind, exit_code, counts, timed_out, before, after):
    """(status, note) for a finished run."""
    if timed_out:
        return "INFRA", "timed out"
    if exit_code in INFRA_EXIT:
        return "INFRA", INFRA_EXIT[exit_code]
    if before != after:
        return "TORN", "the scope changed while the command ran — the result describes no single tree"
    if exit_code != 0:
        if kind == "test" and exit_code == 5 and not counts:
            return "EMPTY", "no tests collected"
        return "FAIL", f"exit {exit_code}"
    if kind == "test" and (counts.get("failed") or counts.get("error")):
        return "FAIL", "exit 0 but the runner reported failed tests or errors"
    if kind == "test" and not (counts.get("passed") or counts.get("failed") or counts.get("flaky")):
        return "EMPTY", ("exit 0 but no test count was recognised — no proof a test ran "
                         "(use --kind check for a command that is not a test runner)")
    return "PASS", ""


def new_entry(st, *, area, kind, tier, argv, cwd, scope, env_names, preflight=None, needs=(), sources=()):
    ev = ledger(st)
    ev["seq"] += 1
    entry = {
        "id": f"E{ev['seq']}", "area": area, "kind": kind, "tier": tier,
        "argv": list(argv), "cwd": cwd or ".", "scope": list(scope), "sources": Sources.validate_sources(sources),
        "env": sorted(env_names or ()), "preflight": preflight, "needs": list(needs),
        "status": "RUNNING", "exit": None, "note": "", "counts": {},
        "started_at": time.time(), "ended_at": None, "pid": None,
        "owner_pid": os.getpid(),  # the verifier itself: alive from allocation to the final write
        "log": None, "log_sha256": None, "fp_before": None, "fp_after": None,
        "phase": st.get("phase"), "correction_iteration": st.get("correction_iteration", 0),
        "run": st.get("run_id"),
    }
    ev["entries"].append(entry)
    return entry


def find(st, entry_id):
    for e in ledger(st)["entries"]:
        if e["id"] == entry_id:
            return e
    return None


def same_command(e, spec):
    return (e["area"] == spec["area"] and e["argv"] == list(spec["argv"])
            and e["cwd"] == (spec.get("cwd") or ".") and e["scope"] == list(spec["scope"])
            and e.get("kind") == spec.get("kind", e.get("kind"))
            and sorted(e.get("env") or ()) == sorted(spec.get("env") or ())
            and e.get("sources", []) == spec.get("sources", [])
            and e.get("preflight") == spec.get("preflight")
            and sorted(e.get("needs") or ()) == sorted(spec.get("needs") or ()))


def latest(st, area, spec=None, kind=None):
    for e in reversed(ledger(st)["entries"]):
        if e["area"] != area or (kind and e["kind"] != kind):
            continue
        if spec is None or same_command(e, spec):
            return e
    return None


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def assess(e):
    """(verdict, why). Only verdict VALID is evidence of a pass on today's tree."""
    if e["status"] == "RUNNING":
        alive = _alive(e.get("owner_pid")) or _alive(e.get("pid"))
        return ("RUNNING", "still running") if alive else ("INTERRUPTED", "run never finished")
    if not e.get("log") or not Path(e["log"]).is_file():
        return "LOG_MISSING", "the log file is gone — no proof of the result"
    try:
        log_sha = file_sha(e["log"])
    except FingerprintError as exc:
        return "LOG_MISSING", str(exc)
    if log_sha != e.get("log_sha256"):
        return "LOG_ALTERED", "the log no longer matches the hash recorded at the end of the run"
    if e["status"] != "PASS":
        return e["status"], e.get("note") or ""
    try:
        now = scope_fingerprint(e["scope"], e.get("env"), e.get("sources", []), e.get("cwd", "."))
    except FingerprintError as exc:
        return "UNVERIFIABLE", str(exc)
    if now != e["fp_after"]:
        if e["kind"] == "preflight":
            return "STALE", "a declared environment variable changed since the preflight"
        return "STALE", "the scope changed since this run (code, fixtures, contracts, deps or config)"
    return "VALID", ""


def counts_text(e):
    c = e.get("counts") or {}
    return " ".join(f"{k}={v}" for k, v in sorted(c.items())) or "counts=unknown"


def row(e, verdict=None, why=""):
    verdict = verdict or assess(e)[0]
    cmd = " ".join(e["argv"])
    tail = f" — {why}" if why else ""
    return (f"{e['id']:<4} {e['area']:<18} {e['tier']:<9} {verdict:<11} "
            f"{counts_text(e):<28} `{cmd}` (cwd {e['cwd']}){tail}")


def unmet(st, spec, _seen=()):
    """Prerequisites a check needs: a VALID preflight and VALID `needs` checks,
    each matched against its CURRENT plan and itself checked recursively (a
    prerequisite whose own prerequisite broke is not valid either)."""
    missing = []
    if spec.get("preflight"):
        pre = latest(st, spec["preflight"], kind="preflight")
        if not pre or assess(pre)[0] != "VALID":
            missing.append(f"preflight `{spec['preflight']}` has not passed in this run")
    for area in spec.get("needs") or ():
        if area in _seen:
            missing.append(f"`{area}` is part of a `needs` cycle")
            continue
        verdict = planned_verdict(st, area, ledger(st)["plan"].get(area), (*_seen, area))[1]
        if verdict != "VALID":
            missing.append(f"`{area}` must be a VALID PASS first (now {verdict})")
    return missing


def planned_verdict(st, area, spec, _seen=()):
    """(entry, verdict, why) for a planned check: its own run must be VALID AND
    its prerequisites must still hold — a broad PASS whose focused prerequisite
    later failed or changed is not evidence."""
    if not spec: return None, "PLAN_MISSING", "dependency has no current plan"
    e = latest(st, area, spec)
    if not e:
        return None, "NOT_RUN", "no run recorded"
    return (e, *entry_verdict(st, e, _seen or (area,)))


def entry_verdict(st, e, _seen=()):
    """assess() plus the prerequisites this run recorded (its plan's, when it
    matches one), checked recursively."""
    verdict, why = assess(e)
    for invalidation in ledger(st).get('scope_invalidations', []):
        from al_execution_tasks import _overlap
        if e.get('started_at', 0) < invalidation['at'] and any(_overlap(a,b) for a in e['scope'] for b in invalidation['scope']):
            return "STALE", "scoped developer amendment invalidated evidence"
    if e.get("started_at",0) < ledger(st).get("invalidated_at",0): return "STALE", "task amendment invalidated evidence"
    current = ledger(st)["plan"].get(e["area"])
    if current and not same_command(e,current): return "PLAN_CHANGED", "entry differs from current evidence plan"
    if verdict == "VALID" and "reuse" in e:
        import al_gate_reuse
        gaps = al_gate_reuse.reuse_gaps(st, e)
        if gaps:return "REUSE_INVALID", "; ".join(gaps)
    if verdict == "VALID" and 'execution_association' in e:
        import al_execution_receipts
        gaps=al_execution_receipts.validate_association(st,e)
        if gaps:return 'REUSE_INVALID', '; '.join(gaps)
    if verdict == "VALID":
        missing = unmet(st, e, _seen or (e["area"],))
        if missing:
            return "PREREQ_INVALID", "; ".join(missing)
    return verdict, why


def repro_gaps(st):
    """[(label, verdict)] for every rework repro whose command is not yet a
    VALID PASS on the current tree: the loop that went red must go green."""
    out = []
    for item in (st.get("rework") or {}).get("history", []):
        red = find(st, item.get("repro") or "")
        if not red:
            out.append(("repro missing", "MISSING_REPRO"))
            continue
        e = latest(st, red["area"], red)
        verdict = entry_verdict(st, e)[0] if e is not red else "STILL_RED"
        if verdict != "VALID":
            out.append((f"repro {red['id']} ({red['area']})", verdict))
    return out


def gaps(st, tier="broad"):
    """[(area, verdict)] for every planned check of `tier` that is not a VALID
    PASS (with valid prerequisites) on the current tree, plus — for `broad` —
    every rework repro that has not gone green. Empty when nothing was planned
    (older runs)."""
    out = repro_gaps(st) if tier == "broad" else []
    if not any(v["tier"] == tier for v in ledger(st)["plan"].values()): out.append(("evidence plan", "EMPTY"))
    for area, spec in (ledger(st)["plan"] or {}).items():
        if spec["tier"] != tier:
            continue
        _, verdict, _ = planned_verdict(st, area, spec)
        if verdict != "VALID":
            out.append((area, verdict))
    return out
