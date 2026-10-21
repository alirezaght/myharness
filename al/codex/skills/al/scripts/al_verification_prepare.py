"""Run-bound immutable verification preparation; availability alone is no proof."""

import json

import al_artifacts as A
import al_store
import al_verification_inputs as Inputs
from al_schema import refuse


def prepare(st, *, area=None, gate=None, stage=None, no_cache=False):
    target, spec = Inputs.destination(st, area=area, gate=gate, stage=stage)
    return Inputs.reconstruct(st, target, spec, no_cache=no_cache)


def validate_preparation(st, ref, target=None):
    try:
        original = A.read_json(ref)
        from al_execution_versions import version
        version(original, "verification-preparation")
        if ref.get("kind") != original["kind"]:
            return ["verification preparation reference kind differs"]
        destination = original["destination"]
        if target is not None and destination != target:
            return ["verification preparation destination differs"]
        selected = (
            {"area": destination["area_or_gate"]}
            if destination["kind"] == "area"
            else {"gate": destination["area_or_gate"], "stage": destination["stage"]}
        )
        current = prepare(st, **selected, no_cache=not original["cache_policy"]["enabled"])
        if not preparation_equivalent(st, original, current):
            return ["verification preparation inputs changed"]
        return list(current["blockers"])
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return ["unreadable or foreign verification preparation"]


def cmd_prepare(a, sid):
    st = al_store.load(sid)
    result = prepare(st, area=a.area, gate=a.gate, stage=a.stage, no_cache=a.no_cache)
    # Reconstruct again before publishing; an artifact never blesses a moving input.
    current = prepare(st, area=a.area, gate=a.gate, stage=a.stage, no_cache=a.no_cache)
    if result != current:
        refuse("verification inputs moved during preparation")
    ref = A.snapshot_bytes(
        sid,
        json.dumps(result, sort_keys=True).encode(),
        result["kind"],
        "run-bound planned verification",
    )
    st.setdefault("verification_preparations", []).append(ref)
    al_store.save(sid, st)
    print(
        json.dumps(
            {
                "artifact": ref,
                "destination": result["destination"],
                "ready": not result["blockers"],
                "blockers": result["blockers"],
            },
            sort_keys=True,
        )
    )
    if result["blockers"]:
        raise SystemExit(15)


def register(sub):
    commands = sub.add_parser("verification").add_subparsers(dest="action", required=True)
    command = commands.add_parser("prepare")
    selected = command.add_mutually_exclusive_group(required=True)
    selected.add_argument("--area")
    selected.add_argument("--gate")
    command.add_argument("--stage", choices=["base", "candidate"])
    command.add_argument("--no-cache", action="store_true")
    command.set_defaults(fn=cmd_prepare)


def preparation_equivalent(st, original, current):
    from al_execution_versions import equivalent
    return equivalent(st, original, current, "verification-preparation")
