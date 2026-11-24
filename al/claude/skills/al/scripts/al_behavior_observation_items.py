"""Evidence, source assertions and exact declared scenario-item coverage."""

import hashlib
import re
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
        if any(counts.get(name, 0) for name in FAILURE_COUNTS):
            refuse("behavior evidence contains failed or errored results")
        # Skipped and expected-failure results are unverified, not failures: they never
        # refuse an observation (issue #19); unverified_results() reports them instead.
        result.append(entry)
    return result


FAILURE_COUNTS = ("failed", "errors")
UNVERIFIED_COUNTS = ("skipped", "xfailed", "xpassed", "xfail", "xpass", "todo")
# pytest -rs summary, pytest -v node lines and -rxX summaries; other runners report
# counts only, and their unverified results stay unlocated.
UNVERIFIED_LOCATION_RES = (
    re.compile(r"^\s*SKIPPED \[\d+\] (?P<path>[^\s:]+):\d+"),
    re.compile(r"^\s*(?P<path>[^\s:]+)::\S.*?\s(?:SKIPPED|XFAIL|XPASS)\b"),
    re.compile(r"^\s*(?:SKIPPED|XFAIL|XPASS)\s+(?P<path>[^\s:]+)::"),
)
LOG_LIMIT = 16 * 1024 * 1024


def unverified_locations(entry):
    """Repository-relative files of the entry's reported skips/xfails, or None if unknown."""
    try:
        log = Path(entry["log"])
        if log.stat().st_size > LOG_LIMIT:
            return None
        text = log.read_bytes().decode(errors="replace")
    except (KeyError, TypeError, OSError):
        return None
    cwd = Path(entry.get("cwd") or ".")
    found = set()
    for line in text.splitlines():
        for pattern in UNVERIFIED_LOCATION_RES:
            match = pattern.match(line)
            if match:
                raw = Path(match.group("path"))
                if not raw.is_absolute() and ".." not in raw.parts:
                    found.add((cwd / raw).as_posix().removeprefix("./"))
                break
    return found or None


def unverified_results(st, evidence_ids, assertion_paths=(), changed_paths=None):
    """Report skipped/expected-failure results of the cited evidence; never refuse.

    A skip located in an asserted or changed file leaves that part of the scenario
    unverified and is reported as related. Skips elsewhere (pre-existing, credential-
    gated or unrelated tests) are informational. Unlocated skips are reported with
    their counts so the reader can add `-rs` to locate them."""
    if changed_paths is None:
        try:
            import al_scope as S

            changed_paths = S.changed_paths(st["identity"]["merge_base"])
        except (OSError, ValueError, KeyError, TypeError, SystemExit):
            changed_paths = ()
    relevant = set(assertion_paths) | set(changed_paths)
    result = []
    for identifier in evidence_ids:
        entry = L.find(st, identifier) or {}
        counts = entry.get("counts", {})
        unverified = {name: counts[name] for name in UNVERIFIED_COUNTS if counts.get(name, 0)}
        if not unverified:
            continue
        located = unverified_locations(entry)
        row = {
            "evidence_id": identifier,
            "counts": unverified,
            "related_paths": sorted(located & relevant) if located else [],
            "located": located is not None,
        }
        if not row["located"]:
            row["note"] = "locations not reported (add -rs to list them); not blocking"
        elif row["related_paths"]:
            row["note"] = "skipped or expected-failure tests in asserted or changed files are unverified"
        else:
            row["note"] = "only in files unrelated to the change or its assertions; informational"
        result.append(row)
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
