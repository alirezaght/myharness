"""Evidence, source assertions and exact declared scenario-item coverage."""

import hashlib
from pathlib import Path

import al_ledger as L
import al_proof_json as J
import al_store
from al_schema import refuse


def references(values):
    if not isinstance(values, list):
        refuse("behavior artifact references must be an array")
    for reference in values:
        J.reference(reference)


def evidence(st, identifiers):
    J.strings(identifiers, "behavior execution evidence", nonempty=True)
    result = []
    for identifier in identifiers:
        entry = L.find(st, identifier)
        if not entry or L.entry_verdict(st, entry)[0] != "VALID" or entry.get("status") != "PASS":
            refuse("behavior evidence is not a successful current execution: " + identifier)
        counts = entry.get("counts", {})
        if any(
            counts.get(name, 0)
            for name in ("failed", "errors", "skipped", "xfailed", "xpassed", "xfail", "xpass")
        ):
            refuse("behavior evidence contains failed, skipped or expected-failure results")
        result.append(entry)
    return result


def assertions(st, rows, required, evidence_ids):
    from al_sources import contains

    if not isinstance(rows, list) or not rows:
        refuse("behavior result lacks concrete source assertions")
    entries = evidence(st, evidence_ids)
    result = {}
    seen = set()
    for row in rows:
        J.closed(row, {"scenario_item_id", "path", "line", "source_sha256"}, "behavior assertion")
        identifier = row["scenario_item_id"]
        if not isinstance(identifier, str) or identifier not in required:
            refuse("assertion references unknown scenario item")
        path = row["path"]
        if not isinstance(path, str):
            refuse("invalid behavior assertion path")
        relative = Path(path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != path
            or path == "."
        ):
            refuse("behavior assertion path must be canonical repository-relative")
        target = al_store.REPO_ROOT / relative
        if any(
            (al_store.REPO_ROOT / Path(*relative.parts[:i])).is_symlink()
            for i in range(1, len(relative.parts) + 1)
        ):
            refuse("behavior assertion cannot traverse a symlink")
        if not target.is_file():
            refuse("behavior assertion source is absent")
        before = target.stat()
        raw = target.read_bytes()
        if before != target.stat() or hashlib.sha256(raw).hexdigest() != row["source_sha256"]:
            refuse("behavior assertion source hash is stale")
        if type(row["line"]) is not int or not 1 <= row["line"] <= len(raw.splitlines()):
            refuse("behavior assertion line is invalid")
        key = (identifier, path, row["line"])
        if key in seen:
            refuse("duplicate behavior assertion")
        seen.add(key)
        if not any(any(contains(scope, path) for scope in entry["scope"]) for entry in entries):
            refuse("behavior assertion is outside executed evidence scope")
        result.setdefault(identifier, []).append(path)
    if set(result) != required:
        refuse("behavior assertions omit declared scenario items")
    return result


def rows(st, values, expected, id_field, fields, all_evidence, source_assertions):
    from al_sources import contains

    if not isinstance(values, list):
        refuse("behavior scenario observations must be arrays")
    seen = set()
    for row in values:
        J.closed(row, {id_field, *fields}, "behavior scenario observation")
        identifier = row[id_field]
        if not isinstance(identifier, str) or identifier not in expected or identifier in seen:
            refuse("unknown or duplicate observed scenario item")
        seen.add(identifier)
        if "observation" in fields:
            J.text(row["observation"], "factual scenario observation")
        if "artifact_refs" in fields:
            references(row["artifact_refs"])
        if "evidence_ids" in fields:
            J.strings(row["evidence_ids"], "scenario item evidence", nonempty=True)
            if not set(row["evidence_ids"]) <= set(all_evidence):
                refuse("scenario item cites undeclared execution evidence")
            entries = evidence(st, row["evidence_ids"])
            for path in source_assertions[identifier]:
                if not any(
                    any(contains(scope, path) for scope in entry["scope"]) for entry in entries
                ):
                    refuse("scenario item evidence does not cover its assertions")
    if seen != set(expected):
        refuse("incomplete declared scenario observations: " + id_field)
