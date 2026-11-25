#!/usr/bin/env python3
"""Checkpoint functional proof, preserving the unchanged AL runner's raw timing verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from al.installer import storage_filesystem as F
from al.installer.owned_process import execute
from al.verification_affected import select
from al.verification_budget import violations
from al.verification_plan import SUITES, build_plan, pytest_argv, reconcile
from al.verification_profile import capture_source_binding, digest, write_json
from al.verification_runner import validate_child_report
from al.verify_changed import changed_files

AMENDMENT_SHA256 = "8cdf41f2fb8582c37cab8fbb53bab7aebf571f76a30a18d623260eda48f797db"
LIMIT = 64 * 1024 * 1024


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def capture(path, *, keep_bytes=False):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "unsafe artifact path")
    parent = F._open_directory(path.parent)
    fd = None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= LIMIT,
            "unsafe or oversized artifact",
        )
        hasher, parts, total = hashlib.sha256(), [], 0
        while block := os.read(fd, min(65536, LIMIT + 1 - total)):
            total += len(block)
            require(total <= LIMIT, "artifact limit")
            hasher.update(block)
            if keep_bytes:
                parts.append(block)
        require(
            F.unchanged(before, os.fstat(fd))
            and F.unchanged(before, os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
            "artifact changed",
        )
        return b"".join(parts), {
            "path": str(path),
            "identity": F.identity(before),
            "size": before.st_size,
            "sha256": hasher.hexdigest(),
        }
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def artifact(path):
    return capture(path)[1]


def read(path, *, within=None):
    path = Path(path)
    if within is not None:
        require(path.is_relative_to(within), "foreign invocation artifact")
    raw, reference = capture(path, keep_bytes=True)
    value = json.loads(raw, object_pairs_hook=F._pairs, parse_constant=lambda _: F._invalid())
    json.dumps(value, allow_nan=False)
    return value, reference


def clean(value, *, producer=False):
    if not producer:
        require(value.get("complete") is True, "incomplete owned cleanup")
        require(
            all(value.get(k, []) == [] for k in ("errors", "unresolved", "secondary_signals")),
            "unresolved owned cleanup",
        )
    require(
        value.get("leader_reaped") is True and value.get("owned_group_settled") is True,
        "unreaped owned launch",
    )
    require(
        value.get("descendant_scope") == "group"
        and value.get("descendants_settled") is None
        and value.get("settlement_basis") == "owned-process-group-only",
        "unexpected custody basis",
    )


def ownership(record, directory, assignment):
    require(
        record["assignment"] == assignment and type(record["pid"]) is int and record["pid"] > 0,
        "launch assignment mismatch",
    )
    path = directory / ("ownership-" + record["token"] + ".json")
    require(record["receipt"] == str(path), "foreign ownership receipt")
    value, _ = read(path, within=directory)
    require(
        value["token"] == record["token"]
        and value["assignment"] == assignment
        and value["identity"] == record["identity"],
        "launch identity mismatch",
    )
    require(
        value["identity"]["pid"] == record["pid"]
        and value["identity"]["pgid"] == record["pid"]
        and value["identity"]["sid"] == record["pid"],
        "invalid launch identity",
    )


def journal(path, assignment, directory):
    value, _ = read(path, within=directory)
    require(value["status"] == "FINISHED" and value["cause"] is None, "interrupted launch")
    clean(value["cleanup"])
    require(
        value["cleanup"]["settlement_evidence"]["launch_identity"]
        == value["ownership"]["identity"],
        "cleanup belongs to another launch",
    )
    ownership(value["ownership"], path.parent, assignment)
    return value


def storage(reference, directory, assignment, raw_artifact, owner):
    require(
        isinstance(reference, dict) and "storage_error" not in reference,
        "unverified storage result",
    )
    require(
        reference.get("reference") == str(directory / "storage-result.json"),
        "foreign storage result",
    )
    value, _ = read(Path(reference["reference"]), within=directory)
    require(
        set(reference) == {"schema_version", "reference", "sha256"}
        and type(reference["schema_version"]) is int
        and reference["schema_version"] == 1
        and F._hash(reference["sha256"])
        and F.digest(value) == reference["sha256"],
        "storage reference changed",
    )
    require(
        value["schema_version"] == 2 and value["capture_state"] == "protected_capture",
        "invalid storage capture",
    )
    binding = value["binding"]
    allocation = binding["allocation"]
    require(
        binding["assignment"] == assignment
        and allocation["owner"]
        == {"adapter_kind": "release", "identity": str(directory), "reservation": assignment}
        and allocation["declaration_digest"]
        == digest({"schema_version": 1, "assignment": assignment}),
        "storage owner mismatch",
    )
    clean(value["producer"], producer=True)
    require(
        value["producer"]["settlement_evidence"]["launch_identity"] == owner["identity"],
        "producer belongs to another launch",
    )
    require(
        value["completion"]
        == {
            "outcome": "passed",
            "settled": False,
            "accounting_complete": True,
            "classification_current": True,
        },
        "invalid completion",
    )
    names = (
        ["collection.json", "collection.log", "process-journal.json"]
        if assignment.startswith("collection:")
        else ["report.json", "output.log", "assignment.json"]
    )
    require(
        [a["path"] for a in value["artifacts"]]
        == [str(directory / n) for n in names] + [owner["receipt"]],
        "incomplete storage capture",
    )
    for retained in value["artifacts"]:
        require(Path(retained["path"]).is_relative_to(directory), "foreign captured artifact")
        require(artifact(retained["path"]) == retained, "captured artifact changed")
    publication, _ = read(directory / "storage-publication.json", within=directory)
    expected = {
        "schema_version": 2,
        "producer": value["producer"],
        "capture_state": "protected_capture",
        "binding": {
            "owner": allocation["owner"],
            "allocation_id": allocation["allocation_id"],
            "declaration_digest": allocation["declaration_digest"],
        },
        "completion": value["completion"] | {"source_current": True},
        "artifacts": [*value["artifacts"], raw_artifact],
    }
    require(publication == expected, "missing or inconsistent storage publication")
    record, _ = read(Path(binding["manager"]) / "records" / (allocation["allocation_id"] + ".json"))
    require(
        record["schema_version"] == 2
        and record["allocation_id"] == allocation["allocation_id"]
        and record["owner"] == allocation["owner"]
        and record["identity"] == allocation["identity"]
        and record["declaration_digest"] == allocation["declaration_digest"]
        and record["owner_reference"] == owner
        and record["state"] == "RETAINED"
        and record["retention_reason"] == "descendant_settlement_unproven"
        and record["terminal_outcome"] == "passed"
        and record["consumers"] == []
        and record["completion_reference"]
        == {
            "schema_version": 1,
            "reference": str(directory / "storage-publication.json"),
            "sha256": F.digest(publication),
        },
        "unexpected or unverified storage retention",
    )
    return value


def expected_assignments(root, collections, binding, selection):
    declarations, _ = read(root / "al/verification-obligations.json")
    plan = build_plan(collections, declarations, tier="full", changed_paths=[], binding=binding)
    candidates = plan["execution_assignments"]
    if declarations.get("acceptance"):
        candidates += build_plan(
            collections, declarations, tier="acceptance", changed_paths=[], binding=binding
        )["execution_assignments"]
    wanted = {(suite, path) for suite, paths in selection.items() for path in paths}
    assignments, offsets = [], {}
    for row in candidates:
        if (row["suite"], row["node"]["source"]["path"]) not in wanted:
            continue
        key = row["suite"] + ":" + row["node"]["source"]["path"]
        source = Path(collections[row["suite"]]["cwd"]) / row["node"]["source"]["path"]
        whole = "run_state(" in source.read_text()
        offset = offsets.get(key, 0)
        offsets[key] = offset + 1
        assignments.append(
            row
            | {
                "group": key + ":dev-chunk-" + str(0 if whole else offset // 8),
                "serialization_group": None,
            }
        )
    return assignments, plan


def validate(root, directory, binding, changed, base, selection, returncode):
    report, raw_artifact = read(directory / "raw.json", within=directory)
    require(type(returncode) is int and returncode in (0, 1), "unexpected runner exit")
    require(
        report["schema_version"] == 1
        and report["contract"] == "al-development-changed-v1"
        and report["release_claim"] is False
        and report["source_stable"] is True
        and report["strict_isolation"] is False,
        "invalid runner contract",
    )
    require(
        report["changed"] == changed
        and report["base"] == base
        and report["selection"] == selection,
        "caller selection changed",
    )
    result = report["verification"]
    require(
        result["cause"] is None
        and result["cleanup"]["complete"] is True
        and all(
            result["cleanup"].get(k, []) == []
            for k in ("errors", "unresolved", "secondary_signals")
        ),
        "runner cleanup incomplete",
    )
    require(
        result["cache_mode"] == "cold" and result["workers"] == min(8, os.cpu_count() or 1),
        "runner configuration changed",
    )
    plan_path = Path(result["plan_path"])
    plan, plan_artifact = read(plan_path, within=directory)
    require(
        plan_path.name == "plan.json"
        and plan_path.parent.parent == directory
        and plan_artifact["sha256"] == result["plan_sha256"],
        "plan binding mismatch",
    )
    run = plan_path.parent
    require(
        plan["run_id"] == result["run_id"]
        and plan["source_binding"] == binding
        and plan["tier"] == "development-changed"
        and plan["complete_required"] is False
        and plan["shared_equivalents"] == [],
        "foreign plan",
    )
    collections = {}
    custodys = []
    require(set(plan["collections"]) == set(SUITES), "incomplete suite inventory")
    for suite in SUITES:
        meta = plan["collections"][suite]
        location = run / ("collect-" + suite)
        require(
            meta["cwd"] == str(root / SUITES[suite][0])
            and meta["relative"] == SUITES[suite][0]
            and meta["suite"] == suite,
            "foreign suite root",
        )
        collected, _ = read(location / "collection.json", within=run)
        j = journal(location / "process-journal.json", "collection:" + suite, run)
        require(
            collected["launch_token"] == j["ownership"]["token"]
            and collected["exitstatus"] == 0
            and collected["finalized"] is True
            and collected["errors"] == []
            and collected["deselected"] == [],
            "invalid collection",
        )
        require(
            artifact(location / "collection.log")["sha256"] == meta["log_sha256"],
            "collection log changed",
        )
        nodes = collected["nodes"]
        require(
            nodes and len({n["nodeid"] for n in nodes}) == len(nodes), "duplicate collection node"
        )
        collections[suite] = meta | {"nodes": nodes}
        custodys.append(
            storage(
                meta["storage_result"],
                location,
                "collection:" + suite,
                raw_artifact,
                j["ownership"],
            )
        )
    expected, expected_plan = expected_assignments(root, collections, binding, selection)
    require(
        expected
        and plan["execution_assignments"] == expected
        and plan["collected_obligations"] == expected_plan["collected_obligations"]
        and plan["collection_digest"] == expected_plan["collection_digest"]
        and plan["declarations_digest"] == expected_plan["declarations_digest"]
        and plan["intentionally_unselected"]
        == sorted(set(plan["collected_obligations"]) - {a["id"] for a in expected}),
        "planned obligations reduced or changed",
    )
    execution_journal, _ = read(run / "execution/journal.json", within=run)
    require(
        result["journal"] == str(run / "execution/journal.json")
        and execution_journal["run_id"] == plan["run_id"]
        and execution_journal["source_digest"] == binding["content_digest"]
        and execution_journal["status"] == "FINISHED"
        and execution_journal["cause"] is None
        and execution_journal["cleanup"] == result["cleanup"],
        "execution journal mismatch",
    )
    groups = {}
    for row in expected:
        groups.setdefault(row["group"], []).append(row)
    remaining = dict(groups)
    checked, duration = [], {}
    launches = execution_journal["children"]
    require(len(launches) == len(result["shards"]) == len(groups), "missing or duplicate launch")
    for shard in result["shards"]:
        ref = shard["assignment"]
        location = run / "execution" / ref["shard_id"]
        assignment, _ = read(location / "assignment.json", within=run)
        require(
            ref["path"] == str(location / "assignment.json")
            and digest({k: v for k, v in assignment.items() if k != "assignment_digest"})
            == assignment["assignment_digest"]
            == ref["assignment_digest"],
            "assignment hash mismatch",
        )
        group = next(
            (
                g
                for g, rows in remaining.items()
                if [r["node"] for r in rows] == assignment["nodes"]
                and rows[0]["suite"] == assignment["suite"]
            ),
            None,
        )
        require(group is not None, "duplicate or foreign shard")
        remaining.pop(group)
        require(
            assignment["shard_id"]
            == ref["shard_id"]
            == uuid.uuid5(uuid.NAMESPACE_URL, plan["run_id"] + group).hex
            and assignment["serialization_group"] is None,
            "foreign shard identity",
        )
        require(
            assignment["run_id"] == plan["run_id"]
            and assignment["source_digest"] == ref["source_digest"] == binding["content_digest"]
            and assignment["collection_digest"] == plan["collection_digest"]
            and assignment["suite"] == ref["suite"],
            "foreign shard source",
        )
        process = shard["process"]
        require(
            process["returncode"] == 0 and process["pid"] == process["ownership"]["pid"],
            "failed or unknown child exit",
        )
        ownership(process["ownership"], location, assignment["assignment_digest"])
        require(
            sum(
                c
                == {
                    "shard_id": ref["shard_id"],
                    "assignment_digest": ref["assignment_digest"],
                    **process["ownership"],
                }
                for c in launches
            )
            == 1,
            "unknown launch record",
        )
        child, child_artifact = read(Path(shard["report"]), within=location)
        require(
            child_artifact["sha256"] == shard["report_sha256"]
            and artifact(shard["log"])["sha256"] == shard["log_sha256"],
            "child artifact changed",
        )
        validated = validate_child_report(assignment, child, process)
        require(
            not validated["errors"] and all(shard[k] == v for k, v in validated.items()),
            "invalid child accounting",
        )
        require(
            all(
                type(t) in (int, float) and math.isfinite(t) and t >= 0
                for t in validated["durations"].values()
            ),
            "invalid duration",
        )
        require(not duration.keys() & validated["durations"].keys(), "duplicate node")
        duration.update(validated["durations"])
        checked.append(validated)
        capture = storage(
            shard["storage_result"],
            location,
            assignment["assignment_digest"],
            raw_artifact,
            process["ownership"],
        )
        allocation = capture["binding"]["allocation"]
        argv = pytest_argv(collections[assignment["suite"]]["cwd"]) + [
            "--basetemp",
            str(Path(allocation["path"]) / "pytest-tmp"),
            "--al-assignment",
            str(location / "assignment.json"),
            "--al-child-report",
            str(location / "report.json"),
            *[n["nodeid"] for n in assignment["nodes"]],
        ]
        require(process["argv"] == argv, "child argv changed")
        custodys.append(capture)
    require(
        sorted(result["assignments"], key=lambda a: a["shard_id"])
        == sorted(
            [
                {
                    **{k: s["assignment"][k] for k in ("shard_id", "suite", "assignment_digest")},
                    "serialization_group": None,
                }
                for s in result["shards"]
            ],
            key=lambda a: a["shard_id"],
        ),
        "assignment summary mismatch",
    )
    rec = reconcile(plan, checked)
    require(
        not remaining
        and rec == result["reconciliation"]
        and rec["selected_success"] is True
        and rec["executed_passed"] == len(expected)
        and all(v == "passed" for v in rec["executed_outcomes"].values()),
        "incomplete functional accounting",
    )
    over = violations(duration)
    require(over == report["over_budget"], "inconsistent timing reason")
    require(
        (returncode == 0 and report["status"] == "PASS" and not over)
        or (returncode == 1 and report["status"] == "FAIL" and bool(over)),
        "not timing-only failure",
    )
    require(capture_source_binding(root) == binding, "source drift after runner")
    return {
        "schema_version": 1,
        "functional_assertions": "PASS",
        "raw_runner_status": report["status"],
        "raw_runner_exit": returncode,
        "raw_report": raw_artifact,
        "plan": plan_artifact,
        "selection": selection,
        "executed_passed": rec["executed_passed"],
        "over_budget": over,
        "timing_disposition": "accepted checkpoint limitation"
        if over
        else "within unchanged budget",
        "owned_process_cleanup": "complete",
        "descendant_settlement": "unknown",
        "storage": "retained; descendant settlement unproven",
        "producer_closure_claim": "none",
        "producer_receipts": [v["producer"] for v in custodys],
    }


def run(root, output, amendment, *, paths=(), base=None, timeout=7200):
    root, output = Path(root).resolve(), Path(output).absolute()
    require(
        not output.is_relative_to(root) and ".." not in output.parts,
        "output must be outside checkout",
    )
    amendment_value, amendment_artifact = read(Path(amendment).absolute())
    require(
        amendment_artifact["sha256"] == AMENDMENT_SHA256
        and amendment_value["id"] == "timing-acceptance-20261125",
        "unaccepted timing amendment",
    )
    require(bool(paths) != bool(base), "supply repeated --path or --base")
    require(
        all(
            not Path(p).is_absolute() and ".." not in Path(p).parts and Path(p).as_posix() == p
            for p in paths
        ),
        "invalid caller path",
    )
    changed, resolved_base = (sorted(set(paths)), None) if paths else (None, None)
    if not paths:
        resolved_base, changed = changed_files(root, base)
    selection = select(root, changed, depth=1)["tests"]
    require(any(selection.values()), "empty selection")
    binding = capture_source_binding(root)
    parent = F._open_directory(output.parent)
    try:
        os.mkdir(output.name, 0o700, dir_fd=parent)
    finally:
        os.close(parent)
    argv = [sys.executable, str(root / "al/verify_changed.py"), "--tier", "all"]
    argv += [v for p in paths for v in ("--path", p)] if paths else ["--base", base]
    argv += ["--report", str(output / "raw.json")]
    request = {
        "schema_version": 1,
        "argv": argv,
        "source_binding": binding,
        "amendment": amendment_artifact,
        "selection": selection,
    }
    write_json(output / "invocation.json", request)
    with (output / "runner.log").open("wb") as log:
        owner = execute(
            argv,
            cwd=root,
            env=dict(os.environ),
            output=log,
            directory=output / "process",
            assignment=digest(request),
            timeout=timeout,
        )
    j = journal(output / "process/process-journal.json", digest(request), output)
    require(j["ownership"] == owner.record(), "outer invocation mismatch")
    result = validate(
        root, output, binding, changed, resolved_base, selection, owner.process.returncode
    )
    result.update(
        amendment=amendment_artifact,
        invocation=artifact(output / "invocation.json"),
        runner_log=artifact(output / "runner.log"),
        outer_cleanup=owner.last_cleanup,
    )
    write_json(output / "functional.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--path", action="append", default=[])
    selectors.add_argument("--base")
    parser.add_argument("--output", required=True)
    parser.add_argument("--amendment", required=True)
    args = parser.parse_args(argv)
    try:
        result = run(
            Path(__file__).resolve().parents[1],
            args.output,
            args.amendment,
            paths=args.path,
            base=args.base,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("FAIL: functional verification: " + str(error))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
