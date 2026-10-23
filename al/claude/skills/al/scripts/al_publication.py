"""Nonterminal publication facts; this module performs no remote transport."""

from pathlib import Path
import hashlib

import al_proof_json as J
import al_publication_checks as C
import al_publication_records as R
import al_store
from al_schema import refuse


def history(st):
    authorizations = st.get("publication_authorizations", [])
    records = st.get("publication_records", [])
    for registry in (authorizations, records):
        if not isinstance(registry, list):
            refuse("invalid publication fact registry")
        if len({(item.get("kind"), item.get("sha256")) for item in registry}) != len(registry):
            refuse("duplicate publication history artifact")
    for reference in authorizations:
        if reference.get("kind") != "publication-authorization-v1":
            refuse("unknown publication authorization artifact")
        R.authorize(st, J.read(reference), current=False)
    values = []
    for reference in records:
        if reference.get("kind") != "publication-record-v1":
            refuse("unknown publication record artifact")
        values.append((reference, R.record(st, J.read(reference), current=False)))
    return values


def prepare_save(st, previous):
    if previous and previous.get("run_id") == st["run_id"]:
        for key in ("publication_authorizations", "publication_records"):
            old = previous.get(key, [])
            if st.get(key, [])[: len(old)] != old:
                refuse("publication history is immutable")
    history(st)


def publication_readiness(st, request=None):
    retained = history(st)
    source = C.source_revision(st)
    reference, latest = retained[-1] if retained else (None, None)
    selected = request or latest
    policy_gaps = [] if selected else ["No publication authorization has been selected."]
    areas = []
    if selected:
        try:
            policy = R.policy(st, selected["policy_refs"], selected["remote"])
            areas = policy["required_areas"]
            R.authorize(st, J.read(selected["authorization_ref"]))
        except (SystemExit, OSError, ValueError, KeyError, TypeError) as exc:
            policy_gaps.append(str(exc))
    code = C.checks(st, areas)
    if selected and policy_gaps:
        code["gaps"].append("publication policy/check requirements are unresolved")
    code["ready"] = not code["gaps"]
    current_source = bool(selected and selected.get("source_revision") == source)
    if selected and not current_source:
        code["gaps"].append("publication source revision is historical")
        code["ready"] = False
    independent = C.independent_readiness(st)
    status = "not_requested"
    if selected:
        status = selected["result"]
        if not current_source:
            status = "pending"
        elif status == "published" and not independent["review"]["ready"]:
            status = "published_review_pending"
    return {
        "schema_version": 1,
        "kind": "publication-readiness-v1",
        **R.identity(st),
        "source_revision": source,
        "status": status,
        "provenance": selected["observation"]["provenance"] if selected else None,
        "current_source": current_source,
        "record_ref": reference,
        "authorization": {"ready": bool(selected) and not policy_gaps, "gaps": policy_gaps},
        "ready_to_publish": bool(selected) and not policy_gaps and code["ready"],
        "code": code,
        **independent,
    }


def validate_publication_record(st, record):
    try:
        R.record(st, record)
    except (SystemExit, OSError, ValueError, KeyError, TypeError) as exc:
        return [str(exc)]
    return []


def packet(st):
    path = al_store.REPO_ROOT.resolve() / R.POLICY_PATH
    policy = None
    if path.resolve() == path and path.is_file() and not path.is_symlink():
        raw = path.read_bytes()
        policy = {
            "path": R.POLICY_PATH,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": J.loads(raw),
        }
    return {
        "schema_version": 1,
        "kind": "publication-packet-v1",
        **R.identity(st),
        "source_revision": C.source_revision(st),
        "policy": policy,
        "authorizations": st.get("publication_authorizations", []),
        "readiness": publication_readiness(st),
        "limitations": [
            "No remote publication, merge or deployment operation is performed.",
            "Only reported publication observation intake is currently supported; independent observer profiles remain blocking.",
        ],
    }


def cmd_publication(a, sid):
    st = al_store.load(sid)
    from al_task_attribution import observe

    before = observe(st)
    if a.action in {"packet", "status"}:
        value = packet(st) if a.action == "packet" else publication_readiness(st)
        if before != observe(st, expected=before):
            refuse("source moved during publication observation")
        print(J.raw(value).decode())
        return
    value = J.loads(Path(a.file).read_bytes())
    if a.action == "authorize":
        R.authorize(st, value)
        policy_path = al_store.REPO_ROOT.resolve() / R.POLICY_PATH
        if (
            policy_path.resolve() != policy_path
            or policy_path.is_symlink()
            or not policy_path.is_file()
        ):
            refuse("literal repository publication policy is required")
        raw = policy_path.read_bytes()
        import al_artifacts as A

        policy_ref = A.snapshot_bytes(sid, raw, "publication-policy-v1", R.POLICY_PATH)
        R.policy(st, [policy_ref], value["remote"])
        reference = J.snapshot(
            sid,
            value,
            "publication-authorization-v1",
            "Explicit publication authorization provenance",
        )
        registry = "publication_authorizations"
        output = {"authorization_ref": reference, "policy_refs": [policy_ref]}
    else:
        gaps = validate_publication_record(st, value)
        if gaps:
            refuse("publication record refused: " + str(gaps))
        reference = J.snapshot(sid, value, "publication-record-v1")
        registry = "publication_records"
        output = {"record_ref": reference}
    if any(item["sha256"] == reference["sha256"] for item in st.get(registry, [])):
        refuse("duplicate publication fact")
    if before != observe(st, expected=before):
        refuse("source moved before publication fact admission")
    st.setdefault(registry, []).append(reference)
    history(st)
    if a.action == "record":
        projection = publication_readiness(st)
        from al_events import queue

        queue(
            st,
            "delivery.updated",
            {"projection_version": 2, "status": projection["status"], "publication": projection},
        )
        output["readiness"] = projection
    al_store.save(sid, st)
    print(J.raw(output).decode())


def register(sub):
    children = sub.add_parser("publication").add_subparsers(dest="action", required=True)
    for name in ("packet", "status", "authorize", "record"):
        p = children.add_parser(name)
        if name in {"authorize", "record"}:
            p.add_argument("--file", required=True)
        p.set_defaults(fn=cmd_publication)
