"""Intent-level commands: `writer finish --evidence auto`, `verify` and `next [--do]`.

Each one is a composition of the existing fine-grained commands, run in one invocation. Every
step is parsed by the same argument parser and dispatched under the same lock and RUNNING rules
as when it is typed on its own, so every guard, refusal, reuse and recorded result is that
command's own. The fine-grained commands stay for exceptions and recovery. A refusal names the
blocker and the fine-grained command that resolves it."""
import shlex
import sys

import al_ledger as L
import al_store
from al_schema import refuse

_PARSER = None

# Commands that lock each read/write themselves, because they can run for minutes.
_SELF_LOCKING = {"preflight", "isolate", "verify", "next"}
# Commands that may run while the run is not RUNNING; every other one needs a running run.
_ANY_STATUS = {"rework-recovery", "start", "show", "clear", "resume", "checkpoint", "waiting", "interrupt",
               "transfer", "stopped", "writer", "check-reconcile", "retry", "agent-peer-reconcile",
               "integration", "recovery", "artifact"}
WRITING = ("IMPLEMENTATION", "FINALIZATION", "REWORK", "CORRECTION")
DONE_WITH_VERIFICATION = ("REVIEW", "FINAL_VERIFICATION", "DONE")


def self_locking(a):
    """True when the command takes the run lock itself; the dispatcher must not hold it."""
    action = getattr(a, "action", None)
    return (a.cmd in _SELF_LOCKING
            or (a.cmd in {"evidence", "gates"} and action == "run")
            or (a.cmd == "gates" and action == "discover")
            or (a.cmd == "writer" and action == "finish" and getattr(a, "evidence_mode", None) == "auto"))


def requires_running(a):
    action = getattr(a, "action", None)
    return a.cmd not in _ANY_STATUS and not (a.cmd in {"publication", "acceptance"} and action in {"packet", "status"})


def bind(parser):
    """Called by al_state with its parser: intent steps parse exactly like typed commands."""
    global _PARSER
    _PARSER = parser


def _parse(parent, argv):
    import al_evidence
    a = al_evidence.parse_arguments(_PARSER, list(argv), [], command_separator=False)
    a.command_argv = []
    a.session = getattr(parent, "session", None)
    a.recovery_file = None
    return a


def dispatch(a, sid):
    """Run one parsed fine-grained command with the dispatcher's lock and status rules."""
    if self_locking(a):
        return a.fn(a, sid)
    with al_store.locked(sid):
        if requires_running(a) and al_store.load(sid)["status"] != "RUNNING":
            refuse("run is not running; explicit resume required")
        return a.fn(a, sid)


def _text(argv):
    return shlex.join(argv)


def step(parent, sid, argv):
    """(ok, refusal text) for one fine-grained command run inside this invocation."""
    print("AL · " + parent.cmd + " · `" + _text(argv) + "`", flush=True)
    try:
        dispatch(_parse(parent, argv), sid)
    except SystemExit as exc:
        if exc.code in (None, 0):
            return True, ""
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr, flush=True)
            return False, exc.code
        return False, "exit " + str(exc.code)
    return True, ""


def _blocked(command, blocker, resolve):
    """Refuse naming the exact blocker and the fine-grained command that resolves it."""
    refuse(command + " blocked: " + blocker + "\nresolve with: " + resolve)


# ---------------------------------------------------------------- evidence selection

def _dependencies(spec):
    return ([spec["preflight"]] if spec.get("preflight") else []) + list(spec.get("needs") or [])


def dependency_order(plan, names):
    """`names` plus their planned prerequisites, every prerequisite before its dependants."""
    out = []

    def visit(name, stack):
        if name in out or name not in plan or name in stack:
            return
        for dependency in _dependencies(plan[name]):
            visit(dependency, stack + (name,))
        out.append(name)

    for name in names:
        visit(name, ())
    return out


def _covers(scope, paths):
    import al_parallel
    if any(s in {".", ""} for s in scope):
        return True
    return bool(al_parallel.overlaps(list(paths), list(scope)))


def changed_paths(st, writer_id):
    """The writer's recorded changed paths, or its owned paths when no contribution was recorded."""
    writer = st["writers"][writer_id]
    attribution = st.get("task_attribution") or {}
    import al_artifacts as A
    for ref in reversed(attribution.get("contributions", [])):
        try:
            value = A.read_json(ref)
        except (OSError, ValueError, KeyError, TypeError, SystemExit):
            continue
        if value.get("writer_id") == writer_id:
            return sorted(value.get("changed_records") or []) or list(writer["files"])
    return list(writer["files"])


def _checks(plan):
    """Planned evidence areas; gate checks are governed by `gates run`."""
    return [name for name in plan if not name.startswith("gate-")]


def relevant_areas(st, paths):
    plan = L.ledger(st)["plan"]
    return dependency_order(plan, [n for n in _checks(plan) if _covers(plan[n]["scope"], paths)])


def incomplete_areas(st):
    plan = L.ledger(st)["plan"]
    pending = [n for n in _checks(plan) if L.planned_verdict(st, n, plan[n])[1] != "VALID"]
    return dependency_order(plan, pending)


def run_areas(parent, sid, areas):
    """Run each area through `evidence run`; a current VALID PASS is kept as it is."""
    results = {}
    for area in areas:
        st = al_store.load(sid)
        plan = L.ledger(st)["plan"]
        if area not in plan:
            continue
        failed_dependency = [d for d in _dependencies(plan[area]) if results.get(d) in {"FAIL", "REFUSED"}]
        if failed_dependency:
            results[area] = "SKIPPED"
            print("AL · " + parent.cmd + " · " + area + " skipped: prerequisite " + ", ".join(failed_dependency) + " did not pass")
            continue
        entry, verdict, _ = L.planned_verdict(st, area, plan[area])
        if verdict == "VALID":
            results[area] = "CURRENT"
            print("AL · " + parent.cmd + " · " + area + " current · " + entry["id"] + " PASS")
            continue
        ok, why = step(parent, sid, ["evidence", "run", "--area", area])
        if ok:
            results[area] = "PASS"
        elif "overlaps unresolved writer" in why:
            results[area] = "DEFERRED"
        elif why.startswith("al_state:"):
            results[area] = "REFUSED"
        else:
            results[area] = "FAIL"
    _report_self_mutation(parent, sid, results)
    return results


def self_mutation_effects(st, areas):
    """({mutating area: files}, {affected area: mutating area}) among `areas`: checks whose latest
    run tore because their scope changed during it, and the other areas whose evidence that
    change made stale or torn."""
    plan = L.ledger(st)["plan"]
    mutating, affected = {}, {}
    for area in areas:
        entry = L.latest(st, area, plan.get(area))
        if entry and entry.get("status") == "TORN" and (entry.get("scope_changes") or {}).get("files"):
            mutating[area] = entry["scope_changes"]["files"]
    for area in areas:
        if area in mutating or area not in plan:
            continue
        entry = L.latest(st, area, plan[area])
        source = entry and L.mutation_source(st, entry)
        if source and source[0]["area"] in mutating and not L.current_pass(st, entry):
            affected[area] = source[0]["area"]
    return mutating, affected


def _report_self_mutation(parent, sid, results):
    """Say which check rewrote files in its own scope and which sibling areas it affected,
    instead of leaving a generic TORN and silent STALE siblings. Siblings are not rerun: the
    tree is not settled until the mutating check is made non-mutating (or its change kept)."""
    st = al_store.load(sid)
    mutating, affected = self_mutation_effects(st, list(results))
    for area, files in mutating.items():
        print("AL · " + parent.cmd + " · check " + area + " modified tracked file(s) " + L.files_text(files)
              + " in its own scope during its run · make it non-mutating (for example --frozen/--locked) "
              "and plan it again, or keep the change deliberately", flush=True)
    for area, source in affected.items():
        results[area] = "AFFECTED-BY-" + source
        print("AL · " + parent.cmd + " · " + area + " affected-by-" + source + " · its evidence no longer "
              "describes the tree; rerun `evidence run --area " + area + "` after " + source, flush=True)


def _summary(results):
    if not results:
        return "no planned evidence area"
    groups = {}
    for area, outcome in results.items():
        groups.setdefault(outcome, []).append(area)
    return "; ".join(k + " " + ", ".join(v) for k, v in sorted(groups.items()))


def _failed(results):
    return [a for a, r in results.items() if r in {"FAIL", "REFUSED", "SKIPPED"} or r.startswith("AFFECTED-BY-")]


# ---------------------------------------------------------------- writer finish --evidence auto

def _active_writers(st):
    return sorted(w["id"] for w in st["writers"].values() if w["state"] == "ACTIVE")


def writer_finish_auto(a, sid):
    argv = ["writer", "finish", "--id", a.id, "--outcome", a.outcome]
    print("AL · writer finish · `" + _text(argv) + "`", flush=True)
    dispatch(_parse(a, argv), sid)
    if a.outcome != "success":
        print("AL · writer " + a.id + " " + a.outcome + " · no evidence run · next: `recovery next`")
        return
    st = al_store.load(sid)
    paths = changed_paths(st, a.id)
    areas = relevant_areas(st, paths)
    print("AL · writer finish · evidence auto · changed " + (", ".join(paths) or "nothing")
          + " · relevant " + (", ".join(areas) or "none"), flush=True)
    a.cmd = "writer finish"
    results = run_areas(a, sid, areas)
    st = al_store.load(sid)
    active = _active_writers(st)
    failed = _failed(results)
    print("AL · writer finish · evidence " + _summary(results))
    if failed:
        mutating, _ = self_mutation_effects(st, list(results))
        resolve = "fix the failure with a writer, then `evidence run --area <area>`; or `verify`"
        if mutating:
            resolve = ("make " + ", ".join(mutating) + " non-mutating (for example --frozen/--locked) with "
                       "`evidence plan --area <area> ...` or keep the rewritten file(s) deliberately, then "
                       "`evidence run --area <area>` for " + ", ".join(failed) + " one at a time; " + resolve)
        _blocked("writer finish --evidence auto",
                 "evidence did not pass: " + ", ".join(failed) + " (the writer finished and is recorded)",
                 resolve)
    if active:
        print("AL · next: finish the active writer(s) " + ", ".join(active)
              + " with `writer finish --id <id> --outcome success --evidence auto`, then `verify`")
    else:
        print("AL · next: `verify`")


# ---------------------------------------------------------------- verify

def _route(st):
    """The mechanical phase edges `verify` records, or a refusal naming the missing work."""
    phase = st["phase"]
    complex_route = st.get("classification") == "COMPLEX"
    if phase == "VERIFICATION":
        return []
    if phase == "IMPLEMENTATION" and complex_route:
        _blocked("verify", "a COMPLEX run requires Builder finalization before verification",
                 "`phase FINALIZATION`, dispatch AL-Builder, then `verify`")
    if phase in WRITING:
        return ["VERIFICATION"]
    if phase in {"PLANNING", "PSEUDOCODE"} and not complex_route and not (
            phase == "PLANNING" and st.get("designer_required")):
        # A verification-only run walks the documented edges without a writer.
        return ["IMPLEMENTATION", "VERIFICATION"]
    _blocked("verify", "phase " + phase + " has no mechanical route to VERIFICATION",
             "`recovery next` names the supported continuation from " + phase)


def _operation_blockers(st):
    rows = []
    for w in sorted(st["writers"].values(), key=lambda w: w["id"]):
        if w["state"] == "ACTIVE":
            rows.append(("writer " + w["id"] + " is ACTIVE",
                         "`writer finish --id " + w["id"] + " --outcome success --evidence auto`"))
        elif w["state"] == "UNRESOLVED":
            rows.append(("writer " + w["id"] + " is UNRESOLVED",
                         "`writer reconcile --id " + w["id"] + " --reason TEXT --artifact PATH`"))
    for e in st["evidence"]["entries"]:
        if (e["status"] == "RUNNING" or e.get("interrupted")) and not e.get("reconciliation"):
            rows.append(("check " + e["id"] + " (" + e["area"] + ") is unresolved",
                         "wait for it, or `check-reconcile --id " + e["id"] + " --reason TEXT --artifact PATH`"))
    return rows


def _gate_gaps(st, stage):
    import al_gates as G
    return G.candidate_gaps(st) if stage == "candidate" else G.gate_gaps(st, "base")


def _gate_resolution(gaps, stage):
    text = " ".join(gaps)
    if "manifest missing" in text:
        return "`gates discover --paths-file <json>` then `gates plan --file <manifest>`"
    if "new touched unit" in text:
        return "`gates discover --extend current --paths-file <json>` then `gates plan --file <manifest>`"
    if "coverage" in text or "discovery" in text:
        return "`gates status` names each coverage gap; `gates discover --extend current --paths-file <json>` and `gates plan`"
    if stage == "base":
        return ("`gates status`; a base failure is the baseline and never blocks, so the named gate did not "
                "finish at base: rerun it with `gates run --stage base --id <gate> --resume`")
    return ("fix the failure with a writer, then `gates run --stage candidate --resume`; "
            "a deterministic failure on the same tree reruns with `gates run --stage candidate --id <gate> --retry-failed`")


def run_gates(parent, sid, stage):
    """Run the stage's missing or stale gates; [] when the stage is ready."""
    st = al_store.load(sid)
    gaps = _gate_gaps(st, stage)
    if not gaps:
        print("AL · verify · " + stage + " gates current")
        return []
    if any("manifest missing" in g or "new touched unit" in g for g in gaps):
        return gaps
    step(parent, sid, ["gates", "run", "--stage", stage, "--resume"])
    return _gate_gaps(al_store.load(sid), stage)


def _review_resolution(message):
    if "behavior" in message:
        return "`behavior packet`, then `behavior observe --file <observations.json>`, then `verify`"
    if "evidence plan" in message and "EMPTY" in message:
        return "`evidence plan --area <name> --tier broad --scope <paths> -- <command>`, then `verify`"
    if "attribution" in message:
        return "`recovery next` names the `task reconcile-attribution` steps"
    return "`recovery next` names the supported continuation"


def cmd_verify(a, sid):
    st = al_store.load(sid)
    if st["status"] != "RUNNING":
        _blocked("verify", "the run is " + st["status"], "`recovery next`")
    if st["phase"] in DONE_WITH_VERIFICATION:
        print("AL · verify · already past verification (phase " + st["phase"] + ") · next: `next`")
        return
    rows = _operation_blockers(st)
    if rows:
        _blocked("verify", "; ".join(r[0] for r in rows), "; ".join(r[1] for r in rows))
    route = _route(st)
    if not any(spec["tier"] == "broad" for spec in L.ledger(st)["plan"].values()):
        _blocked("verify", "the evidence plan has no broad check, which review requires",
                 "`evidence plan --area <name> --tier broad --scope <paths> -- <command>`, then `verify`")
    results = run_areas(a, sid, incomplete_areas(st))
    failed = _failed(results) + [a_ for a_, r in results.items() if r == "DEFERRED"]
    if failed:
        print("AL · verify · evidence " + _summary(results))
        _blocked("verify", "evidence did not pass: " + ", ".join(failed),
                 "fix the failure with a writer (from VERIFICATION, `rework --source verification`), "
                 "then `evidence run --area <area>` or `verify`")
    remaining = L.gaps(al_store.load(sid), "broad")
    if remaining:
        _blocked("verify", "evidence incomplete: " + ", ".join(f"{k} {v}" for k, v in remaining),
                 "`evidence status`; re-run each area with `evidence run --area <area>`")
    gates = {}
    for stage in ("base", "candidate"):
        gates[stage] = run_gates(a, sid, stage)
        if gates[stage]:
            _blocked("verify", stage + " gates not ready: " + "; ".join(gates[stage]), _gate_resolution(gates[stage], stage))
    for phase in route:
        ok, why = step(a, sid, ["phase", phase])
        if not ok:
            _blocked("verify", "phase " + phase + " refused: " + why.replace("al_state: ", ""),
                     "`recovery next` names the supported continuation")
    if route:
        # A planned-rule refresh at the phase edge can renew the gate manifest: settle it again.
        for stage in ("base", "candidate"):
            gaps = run_gates(a, sid, stage)
            if gaps:
                _blocked("verify", stage + " gates not ready after the phase edge: " + "; ".join(gaps),
                         _gate_resolution(gaps, stage))
    ok, why = step(a, sid, ["review-start"])
    print("AL · verify · evidence " + _summary(results) + " · base and candidate gates ready")
    if not ok:
        _blocked("verify", "review-start refused: " + why.replace("al_state: ", ""), _review_resolution(why))
    print("AL · next: `review-packet --stage fresh` (or `next --do`), then dispatch the fresh AL-Architect review")


# ---------------------------------------------------------------- next [--do]

# recovery-descriptor commands `next --do` may run: closed, deterministic public operations.
SAFE = {"gates.run", "evidence.run", "planning.packet", "review.packet"}


def descriptor_argv(descriptor):
    """The exact public command a recovery descriptor names, as typed argv."""
    from al_recovery_guidance import COMMANDS
    recovery = descriptor["recovery"]
    command, action, _ = COMMANDS[recovery["command"]]
    argv = [command] + ([action] if action else [])
    for key, value in recovery["arguments"].items():
        if value is None or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        argv += [flag] if value is True else [flag, str(value)]
    return argv


def _describe(descriptor):
    recovery = descriptor["recovery"]
    head = descriptor["code"] + " · " + descriptor["subject"]["kind"] + " " + descriptor["subject"]["id"] + " · " + descriptor["reason"]
    if recovery["kind"] == "public_operation":
        return head, recovery["command"]
    required = recovery["required_input"]
    operation = required.get("operation")
    return head, None if operation is None else operation


def _next_without_blocker(st):
    phase = st["phase"]
    if st["status"] != "RUNNING":
        return "`recovery next`"
    if _active_writers(st):
        return "`writer finish --id <id> --outcome success --evidence auto`"
    if phase in WRITING or phase in {"PLANNING", "PSEUDOCODE", "VERIFICATION"}:
        return "`verify`"
    if phase == "FINAL_VERIFICATION" or (phase == "REVIEW" and (st.get("review") or {}).get("stage") == "comparison-saved"):
        return "`approved`"
    return "`recovery next`"


def cmd_next(a, sid):
    if getattr(a, "recovery_file", None):
        refuse("recovery descriptor is only valid for its exact guarded operation")
    from al_recovery_guidance import activate, descriptors, reset
    with al_store.locked(sid):
        st = al_store.load(sid)
        offered = descriptors(st)
    import al_comms
    for q in al_comms.unforwarded(st):
        # An answered question goes back to the same warm agent first (#30, #31).
        print("AL · next · " + al_comms.forward_line(st, q))
    if not offered:
        print("AL · next · no recovery blocker · next: " + _next_without_blocker(st))
        return
    descriptor = offered[0]
    head, operation = _describe(descriptor)
    recovery = descriptor["recovery"]
    executable = recovery["kind"] == "public_operation" and recovery["command"] in SAFE
    argv = descriptor_argv(descriptor) if executable else None
    if not a.do:
        print("AL · next · " + head)
        if executable:
            print("AL · next · `next --do` runs `" + _text(argv) + "`")
        else:
            print("AL · next · needs input: " + recovery["required_input"]["kind"] + " — "
                  + recovery["required_input"]["description"]
                  + (" (fine-grained operation: " + operation + ")" if operation else ""))
        return
    if not executable:
        resolve = (recovery["required_input"]["description"] if recovery["kind"] == "supply_input"
                   else "run `" + recovery["command"] + "` with its required input")
        _blocked("next --do", head + " — not a safe deterministic operation"
                 + (" (needs " + recovery["required_input"]["kind"] + ")" if recovery.get("required_input") else ""),
                 resolve + (" · fine-grained operation: " + operation if operation else ""))
    sub = _parse(a, argv)
    print("AL · next --do · " + head + " · `" + _text(argv) + "`", flush=True)
    # The same exact binding a `--recovery-file` invocation checks; every command guard still runs.
    activate(al_store.load(sid), descriptor, sub)
    try:
        dispatch(sub, sid)
    finally:
        reset()


def register(sub):
    p = sub.add_parser("verify", help="run the missing evidence and the base and candidate gates, then enter "
                                      "VERIFICATION and start review; or name the exact blocker")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("next", help="show recovery next's first suggestion; --do runs it when it is a safe "
                                    "deterministic operation")
    p.add_argument("--do", action="store_true")
    p.set_defaults(fn=cmd_next)
