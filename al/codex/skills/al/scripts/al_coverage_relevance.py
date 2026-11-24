"""One source-backed task/expansion projection; global diagnostics remain visible."""

from copy import deepcopy

from al_repository_facts import digest, within, unit_depth

BOUNDARIES = {
    "opaque-boundary",
    "symlink-boundary",
    "unsafe-boundary",
    "gitlink-boundary",
    "base-symlink",
}


def record_id(record):
    return digest({k: v for k, v in record.items() if k not in {"reason", "location", "line"}})


def task_question(facts, paths=None, ci_context=None):
    deps = facts["dependencies"]
    selected = sorted(
        set(deps["potential_services"])
        if deps["uncertainties"]
        else set(deps["known_affected"]) | set(deps.get("policy_selection", []))
    )
    changed = paths if paths is not None else facts.get("question", {}).get("changed_paths", [])
    governing = []
    for ref in facts.get("rule_sources", []):
        owners = [u["id"] for u in facts["units"] if within(u["path"], ref["path"])]
        owner = max(owners, key=unit_depth) if owners else "."
        if (
            len(ref["path"].split("/")) == 1
            or ref["path"] == ".al/repository.json"
            or owner in selected
            or (owner != "." and any(within(owner, path) for path in changed))
        ):
            governing.append(ref)
    governing.extend(
        r for r in facts.get("question", {}).get("governing_source_refs", []) if r not in governing
    )
    return {
        "kind": "task",
        "changed_paths": sorted(
            paths if paths is not None else facts.get("question", {}).get("changed_paths", [])
        ),
        "anticipated_paths": [],
        "directory_paths": sorted(facts.get("question", {}).get("directory_paths", [])),
        "affected_source_paths": facts["dependencies"].get("coverage_inputs", []),
        "selected_service_ids": selected,
        "governing_source_refs": governing,
        "ci_context_binding": ci_context
        if ci_context is not None
        else facts.get("question", {}).get("ci_context_binding"),
        "comparison_bindings": [],
    }


# Check-discovery gaps observed for one unit's own manifest/policy: they concern only that
# unit's verification, so an untouched, unselected unit's gap is not another task's obligation.
UNIT_CHECK_GAPS = {
    "missing-verification-check",
    "missing-architecture-check",
    "missing-check",
    "package-execution-pending",
    "package-semantic-unverified",
    "package-script-nonliteral",
    "package-discovery-unavailable",
    "unsupported-architecture-chain",
}


def _owner(facts, path):
    owners = [u["id"] for u in facts["units"] if within(u["path"], path)]
    return max(owners, key=unit_depth) if owners else None


# Gaps that concern the observation itself or a governing rule of the task, never one
# unit's checks: they stay in scope whatever the change reaches.
ALWAYS_IN_SCOPE = {"torn-capture", "torn-scan", "required-rule-boundary"}


def change_reach(facts, question):
    """Paths and units a change actually reaches.

    The changed, anticipated and selected-directory paths, their literal dependency closure
    (affected source paths), the units owning any of them, and the literally affected and
    policy-selected units. Dependency uncertainty widens which checks run (an exhaustive
    selection), not which coverage gaps block: a gap in a unit reached only through an
    unknown relationship is an unverified check, recorded as a verification note."""
    touched = {
        *question["changed_paths"],
        *question.get("anticipated_paths", []),
        *question.get("directory_paths", []),
    }
    deps = facts["dependencies"]
    # The literal closure, not the question's coverage inputs: those also hold every source
    # of a service selected only through dependency uncertainty.
    paths = touched | set(deps.get("affected_source_paths", []))
    by_path = {u["path"].rstrip("/") or ".": u["id"] for u in facts["units"]}

    def owner(path):
        parts = path.split("/")
        for index in range(len(parts), 0, -1):
            found = by_path.get("/".join(parts[:index]))
            if found is not None:
                return found
        return by_path.get(".")

    units = {owner(p) for p in paths} - {None}
    units |= set(deps.get("known_affected", [])) | set(deps.get("policy_selection", []))
    prefixes = set()
    for path in paths:
        parts = path.split("/")
        prefixes.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return {"paths": paths, "prefixes": prefixes, "units": units, "owner": owner}


def _reaches(reach, path):
    """Whether a path is, contains, or lies inside a reached path."""
    if path in {".", ""} or "." in reach["paths"]:
        return True
    if path in reach["prefixes"]:
        return True
    parts = path.split("/")
    return any("/".join(parts[:index]) in reach["paths"] for index in range(1, len(parts)))


# Structure gaps that cannot conceal a consumer of the change: links and gitlinks (whose
# literal consumers are observed), directories without any supported language or manifest,
# and CI job universes (unverified checks). Layout, manifest and contract gaps that leave a
# unit's imports unknown keep their conservative relevance: they could hide a consumer.
SCOPED_STRUCTURE = BOUNDARIES | {"unclassified-unit", "unknown-ci-inventory", "unsupported-ci-provider"}


def in_reach(record, facts, question, reach, kind="check"):
    """Whether a relevant coverage gap lies in the change's scope (blocks) or outside it (a note)."""
    code = record.get("code")
    path = record.get("path") or "."
    if code in ALWAYS_IN_SCOPE:
        return True
    if kind == "check" or code in UNIT_CHECK_GAPS:
        # A unit's own check-discovery gap is an unverified check of that unit only.
        unit = record.get("unit")
        if unit in {u["id"] for u in facts["units"]}:
            return unit in reach["units"] or (path != "." and _reaches(reach, path))
        return _reaches(reach, path)
    if code not in SCOPED_STRUCTURE:
        return True
    if path != "." and _reaches(reach, path):
        return True
    if code == "unsupported-ci-provider":
        # Its path is the provider's definition pattern.
        from fnmatch import fnmatchcase

        return any(fnmatchcase(p, path) for p in reach["paths"])
    if code in BOUNDARIES:
        # A link or gitlink is reached only through a selected path, a governing rule,
        # or a literal consumer inside the reached scope.
        governing = {r["path"] for r in question.get("governing_source_refs", [])}
        consumers = facts["dependencies"].get("boundary_consumers", {}).get(path, [])
        return path in governing or any(
            c in reach["paths"] or reach["owner"](c) in reach["units"] for c in consumers
        )
    # A repository-wide gap of unknown extent stays conservative.
    return path == "."


def impact(record, facts, question):
    units = {u["id"] for u in facts["units"]}
    owner = record.get("unit")
    missing = UNIT_CHECK_GAPS
    unit_complete = not any(g.get("unit") == owner for g in facts["enumeration"]["gaps"])
    if owner in units and record.get("code") in missing and (unit_complete or any(u['id']==owner and 'go' in u for u in facts['units'])):
        return {
            "extent": "bounded",
            "paths": [],
            "patterns": [],
            "unit_ids": [owner],
            "source_refs": [{"path": owner, "selector": "captured-unit"}],
            "basis": "declared-independent-unit",
            "complete": True,
        }
    if owner in units and record.get("code") in {"unsupported-go-module", "duplicate-go-module", "unsupported-go-dependency"}:
        unit = next(u for u in facts['units'] if u['id'] == owner)
        return {"extent":"bounded", "paths":[unit['path']], "patterns":[], "unit_ids":[owner],
                "source_refs":[{"path":unit['path'], "selector":"captured-go-module"}],
                "basis":"captured-module-boundary", "complete":True}
    path = record.get("path", ".")
    observed = facts["dependencies"].get("runtime_consumer_dependents", {})
    if record.get("code") == "unclassified-runtime-consumer" and path in observed and all(
        _owner(facts, d) is not None or d in observed for d in observed[path]
    ):
        # A consumer outside the unit layout is no service: its literal dependents' units are
        # selected through dependency analysis, and the consumer itself is a task obligation
        # only when the task touches it (being a consumer is not touching it).
        touched = [
            *question["changed_paths"],
            *question.get("anticipated_paths", []),
            *(r["path"] for r in question.get("governing_source_refs", [])),
        ]
        return {
            "extent": "bounded",
            "paths": [path] if any(within(path, t) or within(t, path) for t in touched) else [],
            "patterns": [],
            "unit_ids": [],
            "source_refs": [{"path": path, "selector": "observed-runtime-consumer"}],
            "basis": "literal-consumer-closure",
            "complete": True,
        }
    if record.get("code") in BOUNDARIES and path != ".":
        consumers = facts["dependencies"].get("boundary_consumers", {}).get(path, [])
        required = {r["path"] for r in question.get("governing_source_refs", [])}
        consumer_units = []
        for consumer in consumers:
            owners = [u["id"] for u in facts["units"] if within(u["path"], consumer)]
            if not owners:
                break
            consumer_units.append(max(owners, key=unit_depth))
        complete = len(consumer_units) == len(consumers) and not any(
            g.get("unit") in consumer_units for g in facts["enumeration"]["gaps"]
        )
        if path in required or facts["dependencies"]["uncertainties"] or not complete:
            return {
                "extent": "global",
                "paths": [path, *consumers],
                "patterns": [],
                "unit_ids": [],
                "source_refs": [{"path": path, "selector": "observed-boundary"}],
                "basis": "literal-consumer-closure",
                "complete": complete,
            }
        return {
            "extent": "bounded",
            "paths": [path, *consumers],
            "patterns": [],
            "unit_ids": sorted(set(consumer_units)),
            "source_refs": [{"path": path, "selector": "observed-boundary"}],
            "basis": "observed-boundary",
            "complete": True,
        }
    return {
        "extent": "unknown",
        "paths": [path],
        "patterns": [],
        "unit_ids": [],
        "source_refs": [],
        "basis": "unknown-universe",
        "complete": False,
    }


def validate_impact(value, evidence_index):
    if (
        value.get("extent") not in {"bounded", "global", "unknown"}
        or type(value.get("complete")) is not bool
    ):
        raise ValueError("invalid relevance impact")
    for key in ("paths", "patterns", "unit_ids", "source_refs"):
        if not isinstance(value.get(key), list):
            raise ValueError("invalid relevance impact " + key)
    if value["extent"] == "bounded" and (not value["complete"] or not value["source_refs"]):
        raise ValueError("unproved bounded relevance")
    for ref in value["source_refs"]:
        if ref["path"] not in evidence_index:
            raise ValueError("foreign exclusion evidence")


def intersects(impact_value, question):
    if impact_value["extent"] != "bounded" or not impact_value["complete"]:
        return True
    paths = (
        question["changed_paths"]
        + question.get("anticipated_paths", [])
        + question.get("affected_source_paths", [])
    )
    paths += [r["path"] for r in question.get("governing_source_refs", [])]
    if set(impact_value["unit_ids"]) & set(question["selected_service_ids"]):
        return True
    return any(within(a, b) or within(b, a) for a in impact_value["paths"] for b in paths)


def project_task(facts, ci=None, question=None):
    ci = ci or facts.get("ci_observations", {"obligations": [], "deferred": []})
    question = deepcopy(question or task_question(facts))
    records = [
        ("enumeration", g) for g in facts["enumeration"]["gaps"] + facts["unsupported_boundaries"]
    ]
    records += [("check", g) for g in facts["checks"]["gaps"]]
    if not facts.get("scan", {}).get("stable", facts.get("stable", False)):
        records.append(("enumeration", {"code": "torn-capture", "path": ".", "unit": None}))
    records = list({(kind, record_id(record)): (kind, record) for kind, record in records}.values())
    relevant = []
    deferred = []
    observations = []
    evidence = {u["id"] for u in facts["units"]} | {g.get("path", ".") for _, g in records}
    reach = change_reach(facts, question)
    for category, record in records:
        identifier = record_id({"category": category, **record})
        observed = impact(record, facts, question)
        validate_impact(observed, evidence)
        observations.append(
            {"record_id": identifier, "kind": category, "record": record, "impact": observed}
        )
        if intersects(observed, question) and not in_reach(record, facts, question, reach, category):
            # Outside the paths and units the change reaches: a verification note that
            # remains a visible global diagnostic, never a task obligation.
            deferred.append(
                {
                    "record_id": identifier,
                    "reason": "outside-change-reach",
                    "proof_refs": [{"path": record.get("path") or ".", "selector": "change-reach"}],
                    "comparison_basis": "changed-path-literal-dependency-closure",
                }
            )
        elif intersects(observed, question):
            relevant.append(identifier)
        else:
            deferred.append(
                {
                    "record_id": identifier,
                    "reason": "proven-unit-disjoint"
                    if observed["unit_ids"]
                    else "proven-path-disjoint",
                    "proof_refs": observed["source_refs"],
                    "comparison_basis": "captured-parent-dependency-closure",
                }
            )
    relevant_ci = []
    ci_records = []
    # The task's path universe is the same for every job: sort it once, not per job.
    question_paths = _question_paths(question)
    for job in ci.get("obligations", []) + ci.get("deferred", []):
        identifier = ci_record_id(job)
        decision = ci_relevant(job, question, question_paths)
        ci_records.append({"record_id": identifier, "record": job, "relevant": decision})
        if decision:
            relevant_ci.append(identifier)
        else:
            deferred.append(
                {
                    "record_id": identifier,
                    "reason": "sourced-rule-exclusion",
                    "proof_refs": job.get("dependencies", []),
                    "comparison_basis": "all-definition-variants",
                }
            )
    ci_requirements = [ci_semantics(row["record"]) for row in ci_records if row["relevant"]]
    global_diagnostics = set(row["record_id"] for row in observations)
    global_diagnostics.update(
        row["record_id"]
        for row in ci_records
        if not row["record"].get("execution") or row["record"].get("applicability") != "APPLICABLE"
    )
    selected = question["selected_service_ids"]
    required_ci = set(relevant_ci)

    def required_binding(binding):
        if binding.get("global_required") and binding["category"] == "ci":
            if not binding["source_chain"]:
                return True
            source = binding["source_chain"][0]
            return digest(["ci", source["path"], source["selector"]]) in required_ci
        return bool(set(binding["units"]) & set(selected)) or binding.get("global_required", False)

    bindings = [b["id"] for b in facts["checks"]["bindings"] if required_binding(b)]
    bound_ci = {
        oid
        for b in facts["checks"]["bindings"]
        if b["id"] in bindings
        for oid in b["obligation_ids"]
    }
    obligations = [
        o["id"]
        for o in facts["checks"]["obligations"]
        if set(o["units"]) & set(selected) or o.get("global_required") and o["id"] in bound_ci
    ]
    value = {
        "schema_version": 2,
        "question": question,
        "selected_services": selected,
        "relevant_uncertainty_ids": [record_id(u) for u in facts["dependencies"]["uncertainties"]],
        "relevant_gap_ids": sorted(relevant),
        "relevant_obligation_ids": sorted(obligations),
        "required_binding_ids": sorted(bindings),
        "deferred": sorted(deferred, key=lambda r: r["record_id"]),
        "ci_requirements_sha256": digest(ci_requirements),
        "dependency_semantic_sha256": facts["dependencies"].get(
            "task_import_targets_digest", facts["dependencies"].get("import_targets_digest")
        ),
        "records": observations,
        "ci_records": ci_records,
        "relevant_ci_ids": sorted(set(relevant_ci)),
        "global_inventory": {
            "status": "incomplete" if global_diagnostics else "complete",
            "diagnostic_ids": sorted(global_diagnostics),
        },
        "task_inventory": {
            "status": "incomplete" if relevant else "complete",
            "relevant_gap_ids": sorted(relevant),
        },
        "relevance_inputs": sorted(evidence),
    }
    value["semantic_sha256"] = digest(semantic_projection(value))
    return value


def _scope_frontier(paths, directory_paths):
    directories = set(directory_paths)

    def covered(path):
        if "." in directories and path != ".":
            return True
        parts = path.split("/")
        return any("/".join(parts[:index]) in directories for index in range(1, len(parts)))

    return [path for path in sorted(set(paths)) if not covered(path)]


def semantic_projection(projection):
    # Byte-sensitive proof remains in declaration provenance. Body-only changes
    # do not change task requirements when reconstructed dependency semantics agree.
    q = projection["question"]
    semantic_question = {k: v for k, v in q.items() if k != "governing_source_refs"}
    # A captured directory already covers each descendant. Keep the raw paths
    # in the projection while comparing the same effective scope for currency.
    directories = _scope_frontier(q.get("directory_paths", []), q.get("directory_paths", []))
    semantic_question["directory_paths"] = directories
    semantic_question["changed_paths"] = _scope_frontier(q["changed_paths"], directories)
    return {
        k: projection[k]
        for k in (
            "schema_version",
            "selected_services",
            "relevant_uncertainty_ids",
            "relevant_gap_ids",
            "relevant_obligation_ids",
            "required_binding_ids",
            "task_inventory",
            "relevant_ci_ids",
            "dependency_semantic_sha256",
            "ci_requirements_sha256",
        )
    } | {
        "question": semantic_question,
        "governing_sources": sorted(r["path"] for r in q.get("governing_source_refs", [])),
    }


def validate_projection(value, facts, ci=None, question=None):
    if value != project_task(facts, ci, question):
        raise ValueError("relevance projection contradicts captured facts")


def project_expansion(observation, requested_paths):
    relevant = []
    deferred = []
    for record in observation["diagnostics"]:
        path = record["path"]
        bounded = record["code"] in BOUNDARIES and path != "."
        overlap = any(within(p, path) or within(path, p) for p in requested_paths)
        if not bounded or overlap:
            relevant.append(record)
        else:
            deferred.append(
                {
                    "record": record,
                    "reason": "proven-path-disjoint",
                    "proof_refs": [{"path": path, "selector": "observed-boundary"}],
                }
            )
    return {
        "schema_version": 2,
        "question": {"kind": "expansion", "requested_paths": sorted(requested_paths)},
        "gaps": relevant,
        "deferred": deferred,
        "global_inventory": {
            "status": "incomplete" if observation["diagnostics"] else "complete",
            "diagnostics": observation["diagnostics"],
        },
    }


def ci_record_id(job):
    return digest(["ci", job["source_path"], job.get("job", "<workflow>")])


def _question_paths(question):
    return sorted(set(question["changed_paths"] + question.get("affected_source_paths", [])))


def ci_relevant(job, question, paths=None):
    import al_ci_rules as Rules
    from al_ci_yaml import Unsupported

    paths = _question_paths(question) if paths is None else paths
    if "trigger_definition" in job:
        from al_ci_github import applicability

        try:
            return (
                applicability(
                    job["trigger_definition"],
                    question.get("ci_context_binding"),
                    paths,
                    question.get("directory_paths", []),
                )
                is not False
            )
        except (Unsupported, ValueError, TypeError):
            return True
    variants = job.get("definition_variants")
    if not isinstance(variants, list) or not variants:
        return True
    for variant in variants:
        if not variant.get("present"):
            continue
        if variant.get("unsupported") or not variant.get("universe_complete", False):
            return True
        definition = variant.get("job")
        if not isinstance(definition, dict) or "rules" not in definition:
            return True
        decision = Rules.evaluate_rules(
            definition["rules"],
            paths,
            question.get("ci_context_binding"),
            question.get("directory_paths", []),
        )
        if decision["selected"] is not False:
            return True
    return False


def ci_semantics(job):
    from pathlib import PurePosixPath

    def semantic(value):
        if isinstance(value, dict):
            return {
                k: semantic(v) for k, v in value.items() if k not in {"sha256", "source_sha256"}
            }
        if isinstance(value, list):
            return [semantic(v) for v in value]
        return value

    execution = job.get("execution")
    policy_paths = set()
    if execution:
        for step in execution.get("verification_chain", {}).get("requirements", []):
            argv = step.get("argv", [])
            if not argv:
                continue
            tool = PurePosixPath(argv[0]).name
            if "/" in argv[0]:
                policy_paths.add(PurePosixPath(argv[0]).as_posix())
            if (
                tool in {"python", "python3", "sh", "bash", "node", "ruby"}
                and len(argv) > 1
                and not argv[1].startswith("-")
            ):
                policy_paths.add(PurePosixPath(argv[1]).as_posix())
    return {
        "id": ci_record_id(job),
        "applicability": job.get("applicability"),
        "variants": semantic(job.get("definition_variants")),
        "workflow_definition": job.get("workflow_definition"),
        "trigger_definition": job.get("trigger_definition"),
        "execution": semantic(execution),
        "policy_sources": [r for r in job.get("dependencies", []) if r["path"] in policy_paths],
    }
