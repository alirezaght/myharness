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
import re
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
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    names = []
    for value in values:
        for name in (value or "").replace("\n", ",").split(","):
            name = name.strip()
            if name and name not in names:
                names.append(name)
    return names


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def env_options(raw):
    """Every --env occurrence: (dependency names, ordered NAME=VALUE assignments).

    `--env NAME[,NAME...]` declares environment dependencies that are fingerprinted, not set.
    `--env NAME=VALUE` assigns a non-secret literal: one assignment per occurrence (the value may
    contain commas), applied to the check through an `env NAME=VALUE ...` argv prefix so the
    planned command shows it. Every occurrence is applied; none is silently dropped."""
    names, assignments = [], {}
    for value in raw if isinstance(raw, (list, tuple)) else [raw]:
        if not value:
            continue
        if "=" in value:
            name, _, literal = value.partition("=")
            name = name.strip()
            if not _ENV_NAME.fullmatch(name):
                sys.exit(f"al_state: --env {value!r}: {name!r} is not an environment variable name")
            if name in assignments and assignments[name] != literal:
                sys.exit(f"al_state: --env assigns {name} twice with different values")
            assignments[name] = literal
            continue
        for name in _split(value):
            if not _ENV_NAME.fullmatch(name):
                sys.exit(f"al_state: --env {name!r} is not an environment variable name")
            if name not in names:
                names.append(name)
    return [n for n in names if n not in assignments], assignments


def with_env(argv, assignments, storage_file=None):
    """The argv with its environment assignments made visible and applied."""
    if not assignments:
        return list(argv)
    if storage_file:
        sys.exit("al_state: --env NAME=VALUE cannot be combined with --storage-file (its operand indices count "
                 "argv); put the assignments in the command as `env NAME=VALUE PROGRAM ...` instead")
    return ["env", *(f"{name}={value}" for name, value in assignments.items()), *argv]


def env_text(spec):
    """The environment a planned check runs with, for its one-line summary."""
    prefix = []
    argv = spec.get("argv") or []
    if argv[:1] == ["env"]:
        for token in argv[1:]:
            if "=" not in token or token.startswith("-"):
                break
            prefix.append(token)
    parts = []
    if prefix:
        parts.append("sets " + " ".join(prefix))
    if spec.get("env"):
        parts.append("depends on " + ",".join(spec["env"]))
    return "env " + "; ".join(parts) if parts else "env inherited (no --env)"


def _command_example(action):
    return f"Example: evidence {action} --area unit --scope svc -- python3 -m pytest -q"


def parse_arguments(parser, argv, command_argv, *, command_separator):
    """Keep argparse's option grammar and explain the separately captured argv."""
    args, unknown = parser.parse_known_args(argv)
    check = args.cmd == 'evidence' and args.action in {'plan', 'run'}
    if unknown:
        message = 'unrecognized arguments: ' + ' '.join(unknown)
        if check and not command_separator:
            message += '\nTo supply a check, put `--` before the check command; AL options go before `--`.\n' + _command_example(args.action)
        parser.error(message)
    if check and getattr(args, 'recipe_descriptor_file', None) and not getattr(args, 'recipe', None):
        parser.error('--recipe-descriptor-file requires an explicit named --recipe')
    if check and getattr(args, 'recipe', None) and (command_argv or command_separator):
        parser.error('a named --recipe cannot be combined with an arbitrary command separator')
    if check and not command_argv:
        if command_separator:
            parser.error(f'evidence {args.action}: check command after `--` is empty.\n' + _command_example(args.action))
        if args.action == 'plan' and not getattr(args, 'recipe', None):
            parser.error('evidence plan requires a check command introduced by `--`.\n' + _command_example('plan'))
    return args


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


def _create(sid, expected_run, *, gate_context=None, own_writer=None, **fields):
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
        al_writers.admit_check(st, spec, gate_context=destination, own_writer=own_writer)
        entry = L.new_entry(st, **fields)
        if destination is not None: entry['gate_reservation'] = destination
        entry["reservation"] = secrets.token_hex(16)
        entry["fp_before"] = _fingerprint(entry)
        entry["inputs_before"] = L.inputs_fingerprint(entry)
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
    if hasattr(proc, 'cancel'):
        code = proc.cancel();proof = proc.completion()
        return {'exit':code, 'errors':proof['errors'], 'unresolved':proof['unresolved']}
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


def _execute(sid, entry, timeout, commit=True, *, recipe_context=None):
    """Finalize every handled outcome before publishing its reservation receipt."""
    name = f"{entry.get('run') or 'run'}-{entry['id']}-{entry['area']}-{entry['kind']}.log"
    log = L.log_dir(sid) / name.replace('/', '_')
    entry['log'] = str(log)
    code, timed_out, interrupted, failure = None, False, None, None
    cleanup, proc, previous, prepared = None, None, {}, None
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
                import al_harness_process
                if entry.get('storage') is not None:
                    import al_evidence_storage
                    prepared = al_evidence_storage.PreparedStorage(sid, entry)
                    proc = prepared.launch(entry['argv'], cwd=al_store.REPO_ROOT.resolve() / relative, output=out, recipe_input=entry.get('recipe_assignment'))
                else:
                    proc = al_harness_process.popen(entry['argv'], cwd=al_store.REPO_ROOT.resolve() / relative,
                                            stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
                entry['pid'] = proc.pid
                entry['process'] = {'pid': proc.pid, 'pgid': proc.pid, 'ownership': 'live-Popen-start_new_session'}
                if commit:
                    _commit(sid, entry)
                try:
                    code = proc.wait(timeout=timeout)
                    if prepared is not None:
                        cleanup = prepared.complete()
                        if cleanup.get('secondary_signals'):raise KeyboardInterrupt()
                    elif hasattr(proc, 'completion'):
                        cleanup = proc.completion()
                except subprocess.TimeoutExpired:
                    timed_out = True
                    cleanup = prepared.stop() if prepared is not None else _stop(proc); code = cleanup['exit']
            except KeyboardInterrupt as exc:
                interrupted = exc
                if proc is not None:
                    cleanup = prepared.stop() if prepared is not None else _stop(proc); code = cleanup['exit']
            except FileNotFoundError as exc:
                code = 127; failure = str(exc)
            except (OSError, ValueError, Sources.SourceError, L.FingerprintError) as exc:
                code = 126; failure = str(exc)
                if proc is None and prepared is not None:proc = prepared.proc
                if proc is not None and proc.poll() is None:
                    cleanup = prepared.stop() if prepared is not None else _stop(proc)
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
    entry['inputs_after'] = L.inputs_fingerprint(entry)
    status, note = L.classify(entry['kind'], code, entry['counts'], timed_out, entry['fp_before'], entry['fp_after'], output=raw[start:end].decode(errors='replace') if entry.get('log_sha256') else '')
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
    if prepared is not None:
        try: prepared.before_commit(validate=recipe_context['validate'] if recipe_context else None)
        except (OSError, ValueError, KeyError, TypeError):
            entry['storage_error'] = 'output_or_settlement_unverified'
            if recipe_context:
                entry['status'], entry['note'] = 'INFRA', 'structured recipe output or settlement unverified'
            prepared.retain('publication_unverified')
    if commit:
        _commit(sid, entry)
    if prepared is not None and not entry.get('rejected'):
        try:
            if recipe_context:
                recipe_context['prepared'] = prepared
                recipe_context['publication'] = prepared.publish(committed=commit)
                print('AL · storage · CAPTURED · awaiting recipe reconciliation')
            else:
                result = prepared.finalize(committed=commit)
                print('AL · storage · ' + result['state'] + ' · ' + result.get('reason', ''))
        except (OSError, ValueError, KeyError, TypeError):
            prepared.retain('publication_unverified')
            print('AL · storage · RETAINED · publication_or_cleanup_unverified')
    if entry.get('rejected'):
        if prepared is not None:prepared.retain('reservation_rejected')
        sys.exit(f"al_state: {entry['id']} not recorded — {entry['rejected']}; re-run it in the current run")
    if interrupted is not None:
        raise interrupted
    return text


def reusable(st, prev):
    """A VALID PASS is reused only while its inputs are unchanged (issue #21)."""
    if L.entry_verdict(st, prev)[0] != "VALID":
        return False
    if L.inputs_current(prev):
        return True
    package = L.inputs_root(prev.get("cwd", "."))
    print(f"AL · evidence {prev['id']} not reused · {prev['area']} · files in its package "
          f"({package}) or scope changed since that run, or its inputs were not recorded · running again")
    return False


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



def _storage_file(a, argv):
    if not getattr(a, 'storage_file', None):return None
    import al_evidence_storage
    try:return al_evidence_storage.read_declaration(a.storage_file, argv)
    except (OSError, ValueError, TypeError, KeyError):sys.exit('al_state: invalid managed storage declaration')


SHARD_TOKEN = "{shard}"


def shard_area(area, index, count):
    return f"{area}.shard-{index}-of-{count}"


def expand_shards(st, names):
    """Area names with each sharded area replaced by its shard areas, in order."""
    sets = L.ledger(st).get("shard_sets", {})
    out = []
    for name in names:
        for area in sets.get(name, {}).get("areas", [name]):
            if area not in out:
                out.append(area)
    return out


def _drop_shard_set(st, area):
    """Re-planning an area replaces its previous shard set; runs stay in the ledger."""
    ev = L.ledger(st)
    previous = ev.get("shard_sets", {}).pop(area, None)
    for name in (previous or {}).get("areas", []):
        ev["plan"].pop(name, None)
    if "shard_sets" in ev and not ev["shard_sets"]:
        del ev["shard_sets"]


def _plan_shards(st, a, spec):
    """One planned check per shard; the area is evidenced by all of its current shard PASSes."""
    count = a.shards
    if count < 2:
        sys.exit("al_state: --shards needs a count of at least 2")
    if not any(SHARD_TOKEN in token for token in spec["argv"]):
        sys.exit("al_state: --shards needs the check command to contain {shard} (and optionally {shards}), "
                 "for example `-- npx playwright test --shard={shard}/{shards}`")
    ev = L.ledger(st)
    _drop_shard_set(st, a.area)
    ev["plan"].pop(a.area, None)
    areas = []
    for index in range(1, count + 1):
        name = shard_area(a.area, index, count)
        argv = [token.replace(SHARD_TOKEN, str(index)).replace("{shards}", str(count)) for token in spec["argv"]]
        ev["plan"][name] = {**spec, "area": name, "argv": argv}
        areas.append(name)
    ev.setdefault("shard_sets", {})[a.area] = {"count": count, "areas": areas}
    return areas


def cmd_evidence_plan(a, sid):
    st = al_store.load(sid)
    if getattr(a, 'shards', None) and getattr(a, 'recipe', None):
        sys.exit('al_state: --shards applies to an ordinary check command, not a named --recipe')
    if getattr(a, 'recipe', None):
        import al_evidence_recipe
        try: spec = al_evidence_recipe.plan_spec(a, st)
        except (OSError, ValueError, KeyError, TypeError) as exc:sys.exit('al_state: '+str(exc))
        existing = L.ledger(st)['plan'].get(a.area)
        if spec.get('recipe_context') is not None and existing is not None:
            if existing != spec:sys.exit('al_state: task recipe plan conflicts; explicit replanning required; existing plan retained')
            print('AL · evidence plan reused · ' + a.area)
            return
        L.ledger(st)['plan'][a.area] = spec
        al_store.save(sid, st)
        print('AL · evidence planned · ' + a.area + ' · recipe ' + spec['recipe']['name'])
        return
    argv, scope = a.command_argv, L.norm_scope(a.scope)
    if not argv:
        sys.exit('al_state: evidence plan requires a check command after `--`.\n' + _command_example('plan'))
    env_names, assignments = env_options(a.env)
    argv = with_env(argv, assignments, getattr(a, "storage_file", None))
    if not scope:
        sys.exit('al_state: evidence plan requires --scope PATH[,PATH...].\n' + _command_example('plan'))
    if not a.area:
        sys.exit('al_state: evidence plan requires a nonempty --area.')
    if a.tier not in ("focused", "broad"):
        sys.exit("al_state: --tier must be focused or broad")
    sources=_sources_file(a)
    if getattr(a, 'storage_file', None):
        storage=_storage_file(a, argv)
    else:
        import al_evidence_storage
        storage=al_evidence_storage.default_declaration()
    _require_scope(scope,sources,a.cwd or ".")
    spec = {"area": a.area, "kind": a.kind, "tier": a.tier, "argv": argv, "cwd": a.cwd or ".",
            "scope": scope, "sources": sources, "env": env_names, "preflight": a.preflight or None,
            "needs": _split(a.needs)}
    if storage is not None:spec["storage"] = storage
    if getattr(a, "shards", None):
        areas = _plan_shards(st, a, spec)
        al_store.save(sid, st)
        print(f"AL · evidence planned · {a.area} · {len(areas)} shards ({', '.join(areas)}) · {a.tier} · "
              f"`{' '.join(argv)}` · scope {', '.join(scope)} · {env_text(spec)}\n"
              f"AL · run each shard with `evidence run --area <shard>`; the area counts as evidenced "
              f"when every shard is a current VALID PASS")
        return
    _drop_shard_set(st, a.area)
    L.ledger(st)["plan"][a.area] = spec
    al_store.save(sid, st)
    print(f"AL · evidence planned · {a.area} · {a.tier} · `{' '.join(argv)}` · scope {', '.join(scope)}"
          f" · {env_text(L.ledger(st)['plan'][a.area])}")


def cmd_evidence_retire(a, sid):
    """Withdraw a planned check that was a mistake (wrong command or scope), with a recorded reason.

    Its runs stay in the ledger exactly as they were; the retirement and its replacements are part
    of the evidence the reviewers read. Gate checks are never retired this way."""
    # The dispatcher already holds the run lock for this short load-modify-save.
    st = al_store.load(sid)
    ev = L.ledger(st)
    shards = ev.get("shard_sets", {}).get(a.area)
    if shards:
        if not a.reason or not a.reason.strip():sys.exit("al_state: retiring a planned check needs --reason")
        replaced = _split(a.replaced_by)
        for eid in replaced:
            entry = L.find(st, eid)
            if not entry or entry["status"] != "PASS" or L.entry_verdict(st, entry)[0] != "VALID":
                sys.exit("al_state: replacement " + eid + " is not a current passing run")
        for name in shards["areas"]:
            if name in ev["plan"]:
                ev.setdefault("retired", []).append({"area": name, "reason": a.reason.strip(), "replaced_by": replaced,
                                                     "plan": ev["plan"].pop(name), "at": time.time()})
        _drop_shard_set(st, a.area)
        al_store.save(sid, st)
        print(f"AL · evidence area {a.area} retired with its {len(shards['areas'])} shards · {a.reason.strip()}")
        return
    if a.area not in ev["plan"]:sys.exit("al_state: no planned evidence area " + a.area)
    if a.area.startswith("gate-"):sys.exit("al_state: gate checks are governed by gates decisions, not retired")
    if not a.reason or not a.reason.strip():sys.exit("al_state: retiring a planned check needs --reason")
    replaced = _split(a.replaced_by)
    for eid in replaced:
        entry = L.find(st, eid)
        if not entry or entry["status"] != "PASS" or L.entry_verdict(st, entry)[0] != "VALID":
            sys.exit("al_state: replacement " + eid + " is not a current passing run")
    ev.setdefault("retired", []).append({"area": a.area, "reason": a.reason.strip(), "replaced_by": replaced,
                                         "plan": ev["plan"].pop(a.area), "at": time.time()})
    al_store.save(sid, st)
    print(f"AL · evidence area {a.area} retired · {a.reason.strip()}" + (f" · replaced by {', '.join(replaced)}" if replaced else ""))


def cmd_evidence_run(a, sid):
    st = al_store.load(sid)
    guidance = sys.modules.get("al_recovery_guidance")
    if guidance is not None:
        guidance.check_active(st)
    planned = L.ledger(st)["plan"].get(a.area)
    sharded = L.ledger(st).get("shard_sets", {}).get(a.area)
    if sharded and not planned and not a.command_argv:
        sys.exit(f"al_state: {a.area} is planned as {sharded['count']} shards; run each one: "
                 + "; ".join(f"evidence run --area {name}" for name in sharded["areas"]))
    if getattr(a, 'recipe', None) or planned and planned.get('recipe'):
        import al_evidence_recipe
        try:
            spec = al_evidence_recipe.plan_spec(a, st) if getattr(a, 'recipe', None) else dict(planned)
            if a.command_argv or getattr(a, 'storage_file', None):raise ValueError('stored recipe is exclusive with ordinary argv/storage overrides')
            entry = al_evidence_recipe.run_recipe(sid, spec, writer=getattr(a, 'writer', None), timeout=a.timeout, fresh=a.fresh)
        except (OSError, ValueError, KeyError, TypeError) as exc:sys.exit('al_state: recipe unavailable: '+str(exc))
        if entry['status'] != 'PASS':sys.exit(1)
        return
    if a.command_argv:
        scope = L.norm_scope(a.scope)
        if not scope:
            sys.exit("al_state: an ad-hoc evidence run needs --scope (what this command depends on)")
        sources=_sources_file(a)
        env_names, assignments = env_options(a.env)
        argv = with_env(a.command_argv, assignments, getattr(a, "storage_file", None))
        spec = {"area": a.area, "kind": a.kind, "tier": a.tier or "focused", "argv": argv,
                "cwd": a.cwd or ".", "scope": scope, "sources": sources, "env": env_names,
                "preflight": a.preflight or None, "needs": _split(a.needs)}
        if not getattr(a, "storage_file", None):
            import al_evidence_storage
            retained = dict(spec)
            if planned and "storage" in planned:retained["storage"] = planned["storage"]
            if planned and L.same_command(retained, planned):
                spec = retained
            else:
                spec["storage"] = al_evidence_storage.default_declaration()
    elif planned:
        spec = dict(planned)
        if a.env:
            # Never drop a supplied environment silently: it must match what the area was planned with.
            env_names, assignments = env_options(a.env)
            if (sorted(env_names) != sorted(planned.get("env") or ())
                    or with_env(planned["argv"][len(with_env([], assignments)):], assignments) != planned["argv"]):
                sys.exit(f"al_state: --env differs from the planned area {a.area} ({env_text(planned)}); "
                         "plan the area again with the intended --env")
        if getattr(a,"sources_file",None):spec["sources"]=_sources_file(a)
    else:
        sys.exit(f"al_state: no planned check named {a.area}; plan it or pass `-- <argv>`")
    if getattr(a, "storage_file", None):spec["storage"]=_storage_file(a, spec["argv"])
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
    if prev and not a.fresh and reusable(st, prev):
        from al_execution_tasks import admit_check as execution_check
        execution_check(st, spec)
        print(f"AL · evidence {prev['id']} reused · {spec['area']} · PASS · {L.counts_text(prev)} · "
              f"scope fingerprint unchanged ({prev['fp_after']}) · log {prev['log']}")
        return
    entry = _create(sid, st.get("run_id"), area=spec["area"], kind=spec["kind"], tier=spec["tier"], argv=spec["argv"],
                    cwd=spec["cwd"], scope=spec["scope"], env_names=spec["env"],
                    preflight=spec.get("preflight"), needs=spec.get("needs") or (), sources=spec.get("sources", []),
                    own_writer=getattr(a, "writer", None), storage=spec.get("storage"))
    text = _execute(sid, entry, a.timeout)
    tail = text.splitlines()[-a.tail:] if a.tail else []
    if tail:
        print("\n".join(tail))
    note = f" · {entry['note']}" if entry["note"] else ""
    print(f"AL · evidence {entry['id']} · {entry['area']} · {entry['status']} · {L.counts_text(entry)}"
          f"{note} · log {entry['log']}")
    if entry["status"] != "PASS":
        sys.exit(1)


def _later_pass_note(st, e):
    """Mark a FAIL/INFRA whose identical command and scope later passed and is current.

    Display only: the latest-attempt rule already makes the later PASS the area's evidence; this
    keeps an earlier failure (for example a timeout on a busy host) from reading as open."""
    if e["status"] not in {"FAIL", "INFRA", "TORN"}:
        return ""
    spec = L.ledger(st)["plan"].get(e["area"]) or e
    later = L.latest(st, e["area"], spec)
    if (later is None or later is e or int(later["id"][1:]) <= int(e["id"][1:]) or later["status"] != "PASS"
            or not L.same_command(e, later) or L.entry_verdict(st, later)[0] != "VALID"):
        return ""
    return f" · superseded by later PASS {later['id']} (same command and scope)"


def cmd_evidence_status(a, sid):
    st = al_store.load(sid)
    entries = L.ledger(st)["entries"]
    if not entries:
        print("AL · evidence ledger empty")
    for e in entries:
        print(L.row(e, *L.entry_verdict(st, e)) + _later_pass_note(st, e))
    for area, shards in L.ledger(st).get("shard_sets", {}).items():
        plan = L.ledger(st)["plan"]
        done = [name for name in shards["areas"] if L.planned_verdict(st, name, plan.get(name))[1] == "VALID"]
        print(f"AL · sharded area {area} · {len(done)}/{shards['count']} shards VALID"
              + ("" if len(done) == shards["count"] else " · pending " + ", ".join(
                  name for name in shards["areas"] if name not in done)))
    for ref in L.ledger(st).get("supersessions", []):
        import al_artifacts as A
        value = A.read_json(ref)
        print(f"AL · superseded {value['original']['id']} → {value['replacement']['id']} · {value['reason']} · original preserved")
    for area, spec in L.ledger(st)["plan"].items():
        if not L.latest(st, area, spec):
            print(f"--   {area:<18} {spec['tier']:<9} NOT_RUN     planned `{' '.join(spec['argv'])}`")


def cmd_evidence_require(a, sid):
    """Exit 13 unless every planned check of the tier is a VALID PASS today."""
    st = al_store.load(sid)
    plan = L.ledger(st)["plan"]
    areas = expand_shards(st, _split(a.areas)) or [k for k, v in plan.items() if a.tier in (None, v["tier"])]
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
        p.add_argument("--sources-file")
        p.add_argument("--storage-file")
        p.add_argument("--recipe", choices=['harness-nonbrowser-v1'])
        p.add_argument("--recipe-descriptor-file")
        p.add_argument("--env", action="append", metavar="NAME[=VALUE]",
                       help="repeatable; NAME declares a fingerprinted dependency, NAME=VALUE sets a non-secret literal")
        p.add_argument("--preflight")
        p.add_argument("--needs")
    ev = sub.add_parser("evidence").add_subparsers(dest="action", required=True)
    p = ev.add_parser("plan", usage='%(prog)s --area AREA --scope SCOPE [options] -- PROGRAM [ARG ...]',
                      description='Register a check without executing it. Put all AL options before `--`.',
                      epilog=_command_example('plan'))
    common(p); p.set_defaults(fn=cmd_evidence_plan, tier="broad")
    p.add_argument("--shards", type=int, metavar="N",
                   help="plan the area as N shard checks AREA.shard-I-of-N; {shard} and {shards} in the "
                        "command are replaced by I and N; the area is evidenced by all current shard PASSes")
    p = ev.add_parser("run", usage='%(prog)s --area AREA [options] [-- PROGRAM [ARG ...]]',
                      description='Run the planned area, or supply an ad-hoc check after `--`. Put all AL options before it.',
                      epilog=_command_example('run'))
    common(p); p.add_argument("--fresh", action="store_true")
    p.add_argument("--writer", help="the active writer this check belongs to; its own files may overlap the scope")
    p.add_argument("--timeout", type=float); p.add_argument("--tail", type=int, default=40)
    p.add_argument("--recovery-file",dest="operation_recovery_file")
    p.set_defaults(fn=cmd_evidence_run)
    p = ev.add_parser("retire"); p.add_argument("--area", required=True); p.add_argument("--reason", required=True)
    p.add_argument("--replaced-by", help="comma-separated ledger ids of the current passing runs that replace it")
    p.set_defaults(fn=cmd_evidence_retire)
    from al_evidence_supersession import cmd_supersede
    p = ev.add_parser("supersede"); p.add_argument("--id", required=True)
    p.add_argument("--replaced-by", required=True); p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_supersede)
    ev.add_parser("status").set_defaults(fn=cmd_evidence_status)
    p = ev.add_parser("require"); p.add_argument("--tier", choices=["focused", "broad"])
    p.add_argument("--areas"); p.set_defaults(fn=cmd_evidence_require)
    p = ev.add_parser("validate"); p.add_argument("--claims", required=True)
    p.set_defaults(fn=cmd_evidence_validate)
