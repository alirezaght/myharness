"""Pure bounded verification contracts shared by trusted coordinators and children."""

from __future__ import annotations

from .storage_filesystem import StorageError, _hash, digest

VERSION = 1
RECIPE = "harness-nonbrowser-v1"
MAX_NODES = 20000
MAX_GROUPS = 1024
MAX_PAGES = 30
PAGE_BYTES = 900000
OUTCOMES = {"passed", "failed", "error", "skipped", "xfailed", "xpassed"}
STAGES = {"setup", "call", "teardown"}


def closed(value, keys, code):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise StorageError(code)
    return value


def version(value):
    if type(value) is not int or value != VERSION:
        raise StorageError("unsupported_verification_version")


def text(value, maximum=1024):
    return isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value


def source_digest(value):
    # AL's existing authoritative scoped fingerprint is 16 hex; release bindings are SHA256.
    return _hash(value) or (
        isinstance(value, str) and len(value) == 16 and all(c in "0123456789abcdef" for c in value)
    )


def relative(value):
    return (
        text(value)
        and not value.startswith("/")
        and all(p not in {"", ".", ".."} for p in value.split("/"))
    )


def unique_nodes(nodes):
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        raise StorageError("invalid_node_inventory")
    result = {}
    for node in nodes:
        closed(
            node,
            {"nodeid", "source", "fixtures", "parameterization", "markers", "initial_fixtures"},
            "invalid_node_record",
        )
        if not text(node["nodeid"], 8192) or node["nodeid"] in result:
            raise StorageError("duplicate_or_invalid_node")
        closed(node["source"], {"path", "sha256"}, "invalid_node_source")
        if not relative(node["source"]["path"]) or not _hash(node["source"]["sha256"]):
            raise StorageError("invalid_node_source")
        if not isinstance(node["fixtures"], list) or len(node["fixtures"]) > 256:
            raise StorageError("invalid_fixture_inventory")
        for fixture in node["fixtures"]:
            closed(
                fixture,
                {
                    "name",
                    "scope",
                    "params",
                    "arguments",
                    "source",
                    "implementation",
                    "dynamic",
                    "builtin",
                },
                "invalid_fixture_record",
            )
            if not text(fixture["name"]) or fixture["scope"] not in (
                "function",
                "class",
                "module",
                "package",
                "session",
            ):
                raise StorageError("invalid_fixture_scope")
            closed(fixture["source"], {"path", "sha256"}, "invalid_fixture_source")
            if (
                not text(fixture["source"]["path"])
                or not _hash(fixture["source"]["sha256"])
                or not _hash(fixture["implementation"])
                or not _hash(fixture["params"])
            ):
                raise StorageError("unresolved_fixture_source")
            if type(fixture["dynamic"]) is not bool or type(fixture["builtin"]) is not bool:
                raise StorageError("invalid_fixture_flags")
            if not isinstance(fixture["arguments"], list) or any(
                not text(a) for a in fixture["arguments"]
            ):
                raise StorageError("invalid_fixture_arguments")
        for key in ("markers", "initial_fixtures"):
            if (
                not isinstance(node[key], list)
                or any(not text(n) for n in node[key])
                or len(set(node[key])) != len(node[key])
            ):
                raise StorageError("invalid_node_names")
        if node["parameterization"] is not None and not text(node["parameterization"], 8192):
            raise StorageError("invalid_parameter_identity")
        result[node["nodeid"]] = node
    return result


def validate_task_recipe(value):
    closed(
        value,
        {
            "schema_version",
            "task_id",
            "revision",
            "repository",
            "unit",
            "recipe",
            "setup_inputs",
            "tooling_binding",
            "prepared_binding",
            "area",
            "executor",
        },
        "invalid_task_recipe_descriptor",
    )
    version(value["schema_version"])
    if not text(value["task_id"], 256) or not text(value["revision"], 128):
        raise StorageError("invalid_task_recipe_binding")
    if (
        not text(value["repository"], 4096)
        or value["unit"] != "."
        or value["executor"] != "supervisor"
    ):
        raise StorageError("invalid_task_recipe_scope")
    validate_recipe_identity(value["recipe"])
    if (
        value["area"]
        != "harness-default-" + digest({"task": value["task_id"], "recipe": RECIPE})[:20]
    ):
        raise StorageError("invalid_task_recipe_area")
    if (
        not isinstance(value["setup_inputs"], dict)
        or not {"pyproject.toml", "uv.lock"} <= value["setup_inputs"].keys()
        or len(value["setup_inputs"]) > 34
    ):
        raise StorageError("invalid_task_recipe_setup")
    for name, sha in value["setup_inputs"].items():
        if (
            not relative(name)
            or (name not in {"pyproject.toml", "uv.lock"} and not name.endswith("/pyproject.toml"))
            or not _hash(sha)
        ):
            raise StorageError("invalid_task_recipe_setup")
    if not _hash(value["tooling_binding"]) or not _hash(value["prepared_binding"]):
        raise StorageError("invalid_task_recipe_tooling")
    return value


def task_recipe_applicability(recipe, files):
    validate_recipe(recipe)
    if recipe["schema_version"] != 2 or not isinstance(files, list) or not files:
        return "unknown"
    policy = recipe["task_applicability"]

    def covered(name, prefixes):
        return any(name == p or name.startswith(p + "/") for p in prefixes)

    if any(name == "." or covered(name, policy["paths"]) for name in files):
        return "applicable"
    if all(relative(name) and covered(name, policy["unrelated"]) for name in files):
        return "unrelated"
    return "unknown"


def validate_recipe(value):
    schema = value.get("schema_version") if isinstance(value, dict) else None
    extra = {"task_applicability"} if schema == 2 else set()
    closed(
        value,
        {
            "schema_version",
            "name",
            "roots",
            "selection",
            "config",
            "setup_inputs",
            "grouping",
            "plugin_policy",
        }
        | extra,
        "invalid_harness_recipe",
    )
    if type(schema) is not int or schema not in (1, 2):
        raise StorageError("unsupported_verification_version")
    if schema == 2:
        policy = value["task_applicability"]
        closed(policy, {"version", "paths", "unrelated"}, "invalid_recipe_applicability")
        if policy["version"] != 1:
            raise StorageError("unsupported_recipe_applicability")
        for key in ("paths", "unrelated"):
            paths = policy[key]
            if (
                not isinstance(paths, list)
                or len(paths) > 64
                or len(set(paths)) != len(paths)
                or any(not relative(p) for p in paths)
            ):
                raise StorageError("invalid_recipe_applicability")
    if (
        value["name"] != RECIPE
        or value["roots"] != ["tests"]
        or value["selection"] != "not browser"
        or value["config"] != "pyproject.toml"
        or value["setup_inputs"] != ["pyproject.toml", "uv.lock"]
    ):
        raise StorageError("unsupported_harness_recipe")
    if (
        value["grouping"]
        != {
            "version": 1,
            "policy": "file-with-fixture-unions",
            "safe_builtin_sessions": ["pytestconfig", "tmp_path_factory"],
        }
        or value["plugin_policy"] != "builtin-only-v1"
    ):
        raise StorageError("unsupported_recipe_policy")
    return value


def validate_collection(recipe, receipt, source_binding):
    validate_recipe(recipe)
    closed(
        receipt,
        {
            "schema_version",
            "selected",
            "deselected",
            "inventory",
            "source_digest",
            "recipe_digest",
            "pytest_version",
            "environment",
        },
        "invalid_collection_contract",
    )
    version(receipt["schema_version"])
    inventory = unique_nodes(receipt["inventory"])
    selected, deselected = unique_nodes(receipt["selected"]), unique_nodes(receipt["deselected"])
    if (
        not inventory
        or not selected
        or selected.keys() & deselected.keys()
        or inventory != selected | deselected
    ):
        raise StorageError("incomplete_collection_inventory")
    if any("browser" in n["markers"] for n in selected.values()) or any(
        "browser" not in n["markers"] for n in deselected.values()
    ):
        raise StorageError("unexpected_marker_selection")
    if receipt["source_digest"] != source_binding["content_digest"] or receipt[
        "recipe_digest"
    ] != digest(recipe):
        raise StorageError("collection_source_binding_mismatch")
    if not text(receipt["pytest_version"]) or not isinstance(receipt["environment"], dict):
        raise StorageError("invalid_collection_environment")
    return receipt


def validate_assignment(value):
    closed(
        value,
        {
            "schema_version",
            "recipe",
            "kind",
            "run",
            "parent",
            "source_digest",
            "recipe_digest",
            "group_id",
            "files",
            "inventory",
            "environment",
            "assignment_digest",
        },
        "invalid_recipe_assignment",
    )
    version(value["schema_version"])
    if (
        value["recipe"] != RECIPE
        or value["kind"] not in ("collection", "partition")
        or not text(value["run"])
        or not text(value["group_id"])
    ):
        raise StorageError("invalid_recipe_assignment")
    closed(value["parent"], {"run", "id", "reservation"}, "invalid_recipe_parent")
    if any(not text(v) for v in value["parent"].values()) or value["parent"]["run"] != value["run"]:
        raise StorageError("invalid_recipe_parent")
    if (
        not source_digest(value["source_digest"])
        or not _hash(value["recipe_digest"])
        or value["assignment_digest"]
        != digest({k: v for k, v in value.items() if k != "assignment_digest"})
    ):
        raise StorageError("assignment_digest_mismatch")
    if (
        not isinstance(value["files"], list)
        or not value["files"]
        or len(value["files"]) > MAX_NODES
        or len(set(value["files"])) != len(value["files"])
        or any(not relative(p) for p in value["files"])
    ):
        raise StorageError("invalid_assignment_files")
    if not isinstance(value["environment"], dict):
        raise StorageError("invalid_assignment_environment")
    closed(value["inventory"], {"pages", "node_count", "digest"}, "invalid_assignment_inventory")
    if (
        not isinstance(value["inventory"]["pages"], list)
        or len(value["inventory"]["pages"]) > MAX_PAGES
        or type(value["inventory"]["node_count"]) is not int
        or not 0 <= value["inventory"]["node_count"] <= MAX_NODES
        or not _hash(value["inventory"]["digest"])
    ):
        raise StorageError("invalid_assignment_inventory")
    return value


def stage_outcome(events):
    if not isinstance(events, list) or not events:
        return "error", ["missing stage observations"]
    stages = {}
    errors = []
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event) != {"when", "outcome", "wasxfail", "duration"}
            or event.get("when") not in STAGES
            or event.get("outcome") not in ("passed", "failed", "skipped")
            or type(event.get("wasxfail")) is not bool
            or type(event.get("duration")) not in (int, float)
            or event["duration"] < 0
        ):
            errors.append("invalid stage observation")
            continue
        if event["when"] in stages:
            errors.append("duplicate stage observation")
        stages[event["when"]] = event
    if errors:
        return "error", errors
    if any(r["wasxfail"] for r in stages.values()):
        return (
            "xfailed" if any(r["outcome"] == "skipped" for r in stages.values()) else "xpassed"
        ), []
    if any(r["outcome"] == "failed" for r in stages.values()):
        return "failed", []
    if any(r["outcome"] == "skipped" for r in stages.values()):
        return "skipped", []
    if set(stages) == STAGES and all(r["outcome"] == "passed" for r in stages.values()):
        return "passed", []
    return "error", ["incomplete stage observations"]


def validate_child_report(assignment, report, process):
    """Validate recipe observations; actual process facts are supplied by the owner."""
    validate_assignment(assignment)
    closed(
        report,
        {
            "schema_version",
            "assignment_digest",
            "source_digest",
            "recipe_digest",
            "launch_token",
            "finalized",
            "exitstatus",
            "pytest_version",
            "environment",
            "selected",
            "deselected",
            "inventory",
            "reports",
            "errors",
        },
        "invalid_recipe_child_report",
    )
    version(report["schema_version"])
    errors = []
    for key in ("assignment_digest", "source_digest", "recipe_digest"):
        if report[key] != assignment[key]:
            errors.append("child binding mismatch: " + key)
    if report["launch_token"] != process["launch_token"] or not text(report["launch_token"]):
        errors.append("child launch token mismatch")
    if (
        report["finalized"] is not True
        or type(report["exitstatus"]) is not int
        or report["exitstatus"] != process["returncode"]
    ):
        errors.append("child termination mismatch")
    if not isinstance(report["errors"], list) or report["errors"]:
        errors.append("child collection errors")
    inventory, selected, deselected = (
        unique_nodes(report[k]) for k in ("inventory", "selected", "deselected")
    )
    if selected.keys() & deselected.keys() or inventory != selected | deselected:
        errors.append("incomplete child collection accounting")
    if any("browser" in n["markers"] for n in selected.values()) or any(
        "browser" not in n["markers"] for n in deselected.values()
    ):
        errors.append("unexpected child marker selection")
    expected_environment = assignment["environment"]
    if expected_environment and report["environment"] != expected_environment:
        errors.append("child environment drift")
    expected = process.get("assigned_nodes")
    if expected is not None and inventory != unique_nodes(expected):
        errors.append("assigned collection/source/fixture drift")
    stages = report["reports"]
    if not isinstance(stages, dict):
        raise StorageError("invalid_stage_inventory")
    outcomes = {}
    if assignment["kind"] == "collection":
        if stages or process["returncode"] != 0 or not selected:
            errors.append("incomplete collection-only execution")
    else:
        if set(stages) != selected.keys():
            errors.append("missing/extra executed nodes")
        for node in selected:
            status, node_errors = stage_outcome(stages.get(node))
            outcomes[node] = status
            errors.extend(node + ": " + e for e in node_errors)
        if process["returncode"] == 0 and any(v in {"failed", "error"} for v in outcomes.values()):
            errors.append("successful process conceals failed stages")
        if process["returncode"] not in (0, 1):
            errors.append("unexpected pytest exit status")
        if process["returncode"] == 1 and not any(
            v in {"failed", "error", "xpassed"} for v in outcomes.values()
        ):
            errors.append("failed process without attributable failing stages")
    return {
        "accounting_complete": not errors,
        "selected_success": bool(selected)
        and not errors
        and (assignment["kind"] == "collection" or all(v == "passed" for v in outcomes.values())),
        "structural_errors": errors,
        "outcomes": outcomes,
        "counts": {
            status: sum(v == status for v in outcomes.values()) for status in sorted(OUTCOMES)
        },
        "selected": list(selected.values()),
        "deselected": list(deselected.values()),
        "inventory": list(inventory.values()),
        "environment": report["environment"],
        "pytest_version": report["pytest_version"],
    }


def validate_group_manifest(value):
    closed(
        value,
        {
            "schema_version",
            "recipe",
            "source_digest",
            "recipe_digest",
            "collection",
            "groups",
            "manifest_digest",
        },
        "invalid_recipe_group_manifest",
    )
    version(value["schema_version"])
    if (
        value["recipe"] != RECIPE
        or not source_digest(value["source_digest"])
        or not _hash(value["recipe_digest"])
        or value["manifest_digest"]
        != digest({k: v for k, v in value.items() if k != "manifest_digest"})
    ):
        raise StorageError("invalid_recipe_group_manifest")
    if not isinstance(value["groups"], list) or not 1 <= len(value["groups"]) <= MAX_GROUPS:
        raise StorageError("recipe_group_capacity_exceeded")
    names, nodes = set(), set()
    for group in value["groups"]:
        closed(group, {"id", "files", "selected", "deselected"}, "invalid_recipe_group")
        if (
            not text(group["id"])
            or group["id"] in names
            or not group["files"]
            or len(set(group["files"])) != len(group["files"])
            or any(not relative(f) for f in group["files"])
        ):
            raise StorageError("invalid_recipe_group")
        names.add(group["id"])
        selected, deselected = unique_nodes(group["selected"]), unique_nodes(group["deselected"])
        if (
            not selected
            or nodes & (selected.keys() | deselected.keys())
            or selected.keys() & deselected.keys()
        ):
            raise StorageError("duplicate_or_empty_recipe_group")
        if any(
            n["source"]["path"] not in group["files"]
            for n in [*selected.values(), *deselected.values()]
        ):
            raise StorageError("group_source_membership_mismatch")
        nodes.update(selected.keys() | deselected.keys())
    collection = value["collection"]
    closed(
        collection,
        {"selected", "deselected", "environment", "pytest_version"},
        "invalid_manifest_collection",
    )
    if any(
        not isinstance(collection[k], list)
        or len(set(collection[k])) != len(collection[k])
        or any(not text(n, 8192) for n in collection[k])
        for k in ("selected", "deselected")
    ):
        raise StorageError("invalid_manifest_collection")
    selected_ids = [n["nodeid"] for g in value["groups"] for n in g["selected"]]
    deselected_ids = [n["nodeid"] for g in value["groups"] for n in g["deselected"]]
    if (
        set(selected_ids) != set(collection["selected"])
        or not set(deselected_ids) <= set(collection["deselected"])
        or set(collection["selected"]) & set(collection["deselected"])
    ):
        raise StorageError("incomplete_manifest_collection")
    return value


def validate_reconciliation(manifest, receipts):
    validate_group_manifest(manifest)
    if not isinstance(receipts, list) or len(receipts) > MAX_GROUPS:
        raise StorageError("invalid_partition_receipts")
    groups = {g["id"]: g for g in manifest["groups"]}
    seen, errors, outcomes, eligibility = set(), [], {}, {}
    for receipt in receipts:
        closed(
            receipt,
            {"group_id", "assignment_digest", "entry", "result", "publication"},
            "invalid_partition_receipt",
        )
        group_id = receipt["group_id"]
        if not isinstance(group_id, str) or group_id not in groups or group_id in seen:
            errors.append("duplicate/foreign partition receipt")
            continue
        seen.add(group_id)
        result = receipt["result"]
        expected = unique_nodes(groups[group_id]["selected"])
        if not isinstance(result, dict) or set(result.get("outcomes", {})) != expected.keys():
            errors.append("partition node coverage mismatch")
            eligibility[group_id] = False
            continue
        structural = result.get("accounting_complete") is True and not result.get(
            "structural_errors"
        )
        if not structural:
            errors.append("partition accounting incomplete: " + group_id)
        for node, outcome in result["outcomes"].items():
            if node in outcomes or outcome not in OUTCOMES:
                errors.append("duplicate/invalid aggregate outcome")
            outcomes[node] = outcome
        eligibility[group_id] = (
            structural
            and bool(expected)
            and all(v == "passed" for v in result["outcomes"].values())
            and result.get("producer_closed") is True
        )
    if seen != groups.keys():
        errors.append("missing partition receipts")
    complete = not errors
    return {
        "schema_version": 2,
        "manifest_digest": manifest["manifest_digest"],
        "accounting_complete": complete,
        "producer_closure_complete": complete
        and all(r["result"].get("producer_closed") is True for r in receipts),
        "capture_state": "protected_capture",
        "selected_success": complete
        and bool(outcomes)
        and all(v == "passed" for v in outcomes.values()),
        "structural_errors": errors,
        "outcomes": outcomes,
        "eligibility": {k: v and complete for k, v in eligibility.items()},
        "counts": {
            status: sum(v == status for v in outcomes.values()) for status in sorted(OUTCOMES)
        },
    }


RECIPE_PATH = "al/verification-harness.json"


def validate_recipe_identity(value):
    closed(
        value,
        {"schema_version", "name", "path", "source_sha256", "recipe_digest"},
        "invalid_recipe_specification",
    )
    version(value["schema_version"])
    if (
        value["name"] != RECIPE
        or value["path"] != RECIPE_PATH
        or not _hash(value["source_sha256"])
        or not _hash(value["recipe_digest"])
    ):
        raise StorageError("invalid_recipe_specification")
    return dict(value)


def validate_assignment_binding(value):
    from pathlib import Path

    closed(
        value,
        {"schema_version", "parent", "assignment", "assignment_digest", "kind", "group_id"},
        "invalid_recipe_assignment_binding",
    )
    version(value["schema_version"])
    closed(value["parent"], {"run", "id", "reservation"}, "invalid_recipe_parent")
    if (
        any(not text(v) for v in value["parent"].values())
        or not _hash(value["assignment_digest"])
        or value["kind"] not in ("collection", "partition")
        or not text(value["group_id"])
    ):
        raise StorageError("invalid_recipe_assignment_binding")
    closed(
        value["assignment"],
        {"schema_version", "reference", "sha256"},
        "invalid_recipe_assignment_reference",
    )
    version(value["assignment"]["schema_version"])
    if (
        not isinstance(value["assignment"]["reference"], str)
        or not Path(value["assignment"]["reference"]).is_absolute()
        or not _hash(value["assignment"]["sha256"])
    ):
        raise StorageError("invalid_recipe_assignment_reference")
    return value


def validate_release_report(assignment, report, process):
    """Compatibility contract for the existing strict three-suite AL release runner."""
    errors = []
    for key in (
        "schema_version",
        "run_id",
        "shard_id",
        "suite",
        "assignment_digest",
        "source_digest",
    ):
        if report.get(key) != assignment[key]:
            errors.append("child binding mismatch: " + key)
    if process.get("ownership") and report.get("launch_token") != process["ownership"]["token"]:
        errors.append("child launch token mismatch")
    if report.get("finalized") is not True:
        errors.append("child report not finalized")
    if report.get("exitstatus") != process["returncode"]:
        errors.append("process/report exit mismatch")
    nodes = report.get("nodes")
    expected_nodes = assignment["nodes"]
    if (
        not isinstance(nodes, list)
        or len(nodes) != len(expected_nodes)
        or len({n.get("nodeid") for n in nodes if isinstance(n, dict)}) != len(nodes)
        or {n.get("nodeid"): n for n in nodes if isinstance(n, dict)}
        != {n["nodeid"]: n for n in expected_nodes}
    ):
        errors.append("assigned collection/source/fixture drift")
    if report.get("deselected") or report.get("errors"):
        errors.append("child collection errors/deselection")
    expected = {n["nodeid"] for n in expected_nodes}
    stages = report.get("reports", {})
    if not isinstance(stages, dict):
        stages = {}
        errors.append("invalid stage inventory")
    if set(stages) != expected:
        errors.append("missing/extra executed nodes")
    outcomes, durations = {}, {}
    for node in expected:
        results = stages.get(node, {})
        observed = report.get("stage_events", {}).get(node)
        if observed is None:
            observed = (
                [
                    {
                        "when": stage,
                        "outcome": r.get("outcome"),
                        "wasxfail": bool(r.get("wasxfail", False)),
                        "duration": r.get("duration", 0),
                    }
                    for stage, r in results.items()
                    if isinstance(r, dict)
                ]
                if isinstance(results, dict)
                else []
            )
        status, stage_errors = stage_outcome(observed)
        qualified = assignment["suite"] + ":" + node
        outcomes[qualified] = status
        if stage_errors:
            errors.extend(qualified + ": " + error for error in stage_errors)
        durations[qualified] = sum(
            r["duration"]
            for r in observed
            if isinstance(r, dict)
            and type(r.get("duration")) in (int, float)
            and r["duration"] >= 0
        )
    if process["returncode"] == 0 and any(v in {"failed", "error"} for v in outcomes.values()):
        errors.append("successful process conceals failing stages")
    if process["returncode"] != 0:
        errors.append("child process failed")
    return {
        "outcomes": outcomes,
        "errors": errors,
        "subprocesses": report.get("subprocesses", 0),
        "runtime_profile": report.get("runtime_profile", {}),
        "durations": durations,
    }


def settlement_observation(proof):
    """Describe trusted adapter facts; group completion never establishes ancestry."""
    proof = proof if isinstance(proof, dict) else {}
    scope = proof.get("descendant_scope", "group")
    if scope not in ("group", "pid_namespace", "closed_owned_launches", "unknown"):
        scope = "unknown"
    descendants = proof.get("descendants_settled")
    if type(descendants) is not bool or scope in ("group", "unknown"):
        descendants = None
    return {
        "schema_version": 1,
        "leader_reaped": proof.get("leader_reaped") is True,
        "owned_group_settled": proof.get("owned_group_settled") is True,
        "descendant_scope": scope,
        "descendants_settled": descendants,
        "settlement_basis": proof.get("settlement_basis", "legacy-group-only"),
        "settlement_evidence": proof.get("settlement_evidence"),
    }


def producer_settled(proof):
    """Only a separately qualified trusted launch adapter may supply a positive basis."""
    observed = settlement_observation(proof)
    if (
        not observed["leader_reaped"]
        or not observed["owned_group_settled"]
        or observed["descendants_settled"] is not True
    ):
        return False
    # No general closed-launch adapter is qualified. It must not be enabled by a caller label.
    if (
        observed["descendant_scope"] != "pid_namespace"
        or observed["settlement_basis"] != "qualified-bwrap-pidns-teardown-v1"
    ):
        return False
    evidence = observed["settlement_evidence"]
    return (
        isinstance(evidence, dict)
        and set(evidence)
        == {"schema_version", "kind", "launch_sha256", "teardown_sha256", "namespace_identity"}
        and type(evidence["schema_version"]) is int
        and evidence["schema_version"] == 1
        and evidence["kind"] == "qualified-bwrap-private-lifecycle-v1"
        and _hash(evidence["launch_sha256"])
        and _hash(evidence["teardown_sha256"])
        and isinstance(evidence["namespace_identity"], dict)
        and bool(evidence["namespace_identity"])
    )
