"""Named source-backed recipes coordinate ordinary independently owned evidence checks."""

from pathlib import Path

NAME = "harness-nonbrowser-v1"
PATH = "al/verification-harness.json"


def modules():
    from installer import storage_filesystem, verification_contracts, verification_harness

    return storage_filesystem, verification_contracts, verification_harness


def normalize_recipe(value):
    return None if value is None else modules()[1].validate_recipe_identity(value)


def validate_assignment_binding(value):
    return None if value is None else modules()[1].validate_assignment_binding(value)


def validate_entry_recipe(entry):
    normalize_recipe(entry.get("recipe"))
    child = validate_assignment_binding(entry.get("recipe_assignment"))
    if child is not None:
        if entry.get("recipe") is not None or child["parent"]["run"] != entry.get("run"):
            raise ValueError("foreign recipe parent")
        if entry.get("argv") != modules()[2].logical_argv(child["kind"]):
            raise ValueError("recipe child command mismatch")
        if entry.get("storage") != modules()[2].report_declaration():
            raise ValueError("recipe child storage mismatch")
    if entry.get("recipe_context") is not None:
        modules()[1].validate_task_recipe(entry["recipe_context"])
        if entry.get("recipe") != entry["recipe_context"]["recipe"]:
            raise ValueError("task recipe context does not match coordinator")
    if entry.get("recipe") is not None and entry.get("argv") != []:
        raise ValueError("recipe coordinator has no executable argv")


def load_recipe(spec):
    import al_store

    fs, contracts, _ = modules()
    normalize_recipe(spec)
    path = al_store.REPO_ROOT / PATH
    if fs.artifact(path, maximum=65536)["sha256"] != spec["source_sha256"]:
        raise ValueError("source-owned Harness recipe changed; replan it")
    recipe = contracts.validate_recipe(fs.read_json(path))
    if fs.digest(recipe) != spec["recipe_digest"]:
        raise ValueError("source-owned recipe digest changed")
    return recipe


def plan_spec(args, state):
    import al_evidence
    import al_ledger as ledger
    import al_store

    fs, contracts, _ = modules()
    if args.recipe != NAME or args.command_argv or getattr(args, "storage_file", None):
        raise ValueError("named recipe is exclusive with arbitrary argv and --storage-file")
    if (args.cwd or ".") != "." or args.kind != "test":
        raise ValueError("Harness recipe requires repository cwd and kind test")
    scope = ledger.norm_scope(args.scope)
    if not scope or any(
        not any(s == "." or name == s or name.startswith(s.rstrip("/") + "/") for s in scope)
        for name in ("tests", "harness", "al", "pyproject.toml", "uv.lock")
    ):
        raise ValueError(
            "Harness recipe scope must cover tests, harness, al, pyproject.toml and uv.lock"
        )
    path = al_store.REPO_ROOT / PATH
    recipe = contracts.validate_recipe(fs.read_json(path))
    identity = {
        "schema_version": 1,
        "name": NAME,
        "path": PATH,
        "source_sha256": fs.artifact(path, maximum=65536)["sha256"],
        "recipe_digest": fs.digest(recipe),
    }
    sources = al_evidence._sources_file(args)
    al_evidence._require_scope(scope, sources, ".")
    names, assignments = al_evidence.env_options(args.env)
    if assignments:
        raise ValueError("recipe checks take --env NAME dependencies only, not NAME=VALUE assignments")
    spec = {
        "area": args.area,
        "kind": "test",
        "tier": args.tier or "focused",
        "argv": [],
        "cwd": ".",
        "scope": scope,
        "sources": sources,
        "env": names,
        "preflight": args.preflight or None,
        "needs": al_evidence._split(args.needs),
        "recipe": identity,
    }
    descriptor_file = getattr(args, "recipe_descriptor_file", None)
    if descriptor_file:
        fs.artifact(Path(descriptor_file), maximum=65536)
        context = contracts.validate_task_recipe(fs.read_json(Path(descriptor_file)))
        validate_task_context(context, state, identity)
        if context["area"] != args.area or spec["tier"] != "broad":
            raise ValueError("task recipe area/tier does not match protected descriptor")
        spec["recipe_context"] = context
    return spec


def validate_task_context(context, state, identity):
    import al_store
    from al_execution_tasks import scope_revision
    fs, contracts, _ = modules()
    contracts.validate_task_recipe(context)
    if context["repository"] != str(al_store.REPO_ROOT.resolve()) or context["recipe"] != identity:
        raise ValueError("task recipe repository/source changed")
    if scope_revision(state, context["task_id"]) != context["revision"]:
        raise ValueError("task recipe revision changed; prepare current task")
    recipe = load_recipe(identity)
    files = state["integration"]["tasks"][context["task_id"]]["files"]
    if contracts.task_recipe_applicability(recipe, files) != "applicable":
        raise ValueError("task recipe no longer applies")
    for name, expected in context["setup_inputs"].items():
        if fs.artifact(al_store.REPO_ROOT / name, maximum=4 * 1024 * 1024)["sha256"] != expected:
            raise ValueError("task recipe setup inputs changed")


def _fields(spec):
    return {
        key: spec[key] for key in ("area", "kind", "tier", "argv", "cwd", "scope", "sources")
    } | {
        "env_names": spec["env"],
        "preflight": spec.get("preflight"),
        "needs": spec.get("needs") or (),
    }


def _binding(entry):
    return {key: entry[key] for key in ("run", "id", "reservation")}


def run_recipe(sid, spec, *, writer, timeout, fresh):
    import json
    import os
    import time

    import al_evidence as evidence
    import al_ledger as ledger
    import al_store

    fs, contracts, compiler = modules()
    recipe = load_recipe(spec["recipe"])
    state = al_store.load(sid)
    if spec.get("recipe_context") is not None:
        if writer is not None:
            raise ValueError("prepared Harness default requires Supervisor execution")
        validate_task_context(spec["recipe_context"], state, spec["recipe"])
    evidence._require_scope(spec["scope"], spec.get("sources", []), ".")
    missing = ledger.unmet(state, spec)
    if missing:
        raise ValueError("; ".join(missing))
    previous = ledger.latest(state, spec["area"], spec)
    if previous and not fresh and ledger.entry_verdict(state, previous)[0] == "VALID":
        from al_execution_tasks import admit_check

        admit_check(state, spec)
        print("AL · evidence " + previous["id"] + " reused · recipe " + NAME)
        return previous
    entry = evidence._create(
        sid, state["run_id"], **_fields(spec), recipe=spec["recipe"], recipe_context=spec.get("recipe_context"), own_writer=writer
    )
    parent = _binding(entry)
    directory = (
        ledger.log_dir(sid) / "storage" / ("recipe-" + entry["id"] + "-" + entry["reservation"])
    )
    directory.mkdir(parents=True, mode=0o700)
    publication_root = ledger.log_dir(sid) / "storage"
    log = directory / "coordinator.log"
    entry["log"] = str(log)
    contexts, children, receipts = {}, [], []
    aggregate_reference = None

    def progress(stage, **facts):
        with log.open("a") as stream:
            stream.write(
                json.dumps(
                    {"schema_version": 1, "recipe": NAME, "stage": stage, **facts}, sort_keys=True
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        print("AL · recipe " + entry["id"] + " · " + stage, flush=True)

    def reserve(kind, group_id, files, inventory, environment):
        assignment, reference = compiler.assignment(
            kind=kind,
            parent=parent,
            source_digest=entry["fp_before"],
            recipe_digest=spec["recipe"]["recipe_digest"],
            group_id=group_id,
            files=files,
            inventory=inventory,
            environment=environment,
            directory=directory / group_id,
        )
        area = spec["area"] + "-" + entry["id"] + "-" + group_id
        current = al_store.load(sid)
        if area in ledger.ledger(current)["plan"]:
            raise ValueError("generated recipe area conflicts with a registered plan")
        child = evidence._create(
            sid,
            entry["run"],
            **(
                _fields(spec)
                | {
                    "area": area,
                    "kind": "check" if kind == "collection" else "test",
                    "argv": compiler.logical_argv(kind),
                }
            ),
            own_writer=writer,
            storage=compiler.report_declaration(),
            recipe_assignment={
                "schema_version": 1,
                "parent": parent,
                "assignment": reference,
                "assignment_digest": assignment["assignment_digest"],
                "kind": kind,
                "group_id": group_id,
            },
        )
        children.append(child)
        if child["fp_before"] != entry["fp_before"]:
            raise ValueError("recipe source changed during reservation")
        return child, assignment, inventory

    def execute(child, assignment, inventory):
        context = {}
        contexts[child["id"]] = context

        def validate(current, outputs, prepared):
            report = compiler.read_report(outputs)
            process = current["recipe_process"]
            result = contracts.validate_child_report(
                assignment,
                report,
                {
                    "returncode": current["exit"],
                    "launch_token": process["launch_token"],
                    "assigned_nodes": inventory if assignment["kind"] == "partition" else None,
                },
            )
            compiler.validate_xml(outputs, result, kind=assignment["kind"])
            result.update(
                producer_closed=contracts.producer_settled(prepared.cleanup),
                capture_state="protected_capture",
            )
            reference = compiler.write_document(prepared.directory, "validation.json", result)
            current["recipe_result"] = {"schema_version": 2, "validation": reference}
            current["counts"] = result["counts"]
            current["status"] = (
                "PASS"
                if result["selected_success"]
                else "FAIL"
                if result["accounting_complete"]
                else "INFRA"
            )
            current["note"] = (
                "exact structured recipe accounting"
                if result["accounting_complete"]
                else "incomplete structured recipe accounting"
            )
            context["result"] = result
            prepared.recipe_result = result

        context["validate"] = validate
        evidence._execute(sid, child, timeout, recipe_context=context)
        if (
            not context.get("publication")
            or "result" not in context
            or not context["result"]["accounting_complete"]
        ):
            raise ValueError("recipe child output or complete accounting unavailable")
        result = {
            "group_id": assignment["group_id"],
            "assignment_digest": assignment["assignment_digest"],
            "entry": _binding(child),
            "result": compiler.result_summary(context["result"]),
            "publication": context["publication"],
        }
        return result, context["result"]

    def binding_row(child):
        assignment = child["recipe_assignment"]
        return {
            k: assignment[k] for k in ("group_id", "assignment", "assignment_digest", "kind")
        } | {"entry": _binding(child)}

    def finish_pending(reason):
        for child in children:
            if child["status"] != "RUNNING":
                continue
            path = directory / ("unexecuted-" + child["id"] + ".log")
            fs.publish_json(
                path,
                {
                    "schema_version": 1,
                    "status": "NOT_EXECUTED",
                    "reason": reason,
                    "entry": _binding(child),
                },
            )
            child.update(
                status="INFRA",
                note="not executed: " + reason,
                ended_at=time.time(),
                exit=None,
                log=str(path),
                log_sha256=ledger.file_sha(path),
                fp_after=evidence._fingerprint(child),
            )
            evidence._commit(sid, child)

    try:
        progress("collecting")
        collect = reserve("collection", "collection", recipe["roots"], [], {})
        collection_receipt, collected = execute(*collect)
        if not collected["selected_success"]:
            raise ValueError("complete recipe collection unavailable")
        collection_contract = {
            "schema_version": 1,
            "selected": collected["selected"],
            "deselected": collected["deselected"],
            "inventory": collected["inventory"],
            "source_digest": entry["fp_before"],
            "recipe_digest": spec["recipe"]["recipe_digest"],
            "pytest_version": collected["pytest_version"],
            "environment": collected["environment"],
        }
        manifest = compiler.plan_recipe(
            recipe, collection_contract, {"content_digest": entry["fp_before"]}
        )
        if len(manifest["groups"]) + 1 > contracts.MAX_GROUPS + 1:
            raise ValueError("recipe exceeds private channel handle capacity")
        manifest_reference = compiler.write_document(directory, "manifest.json", manifest)
        partitions = []
        for group in manifest["groups"]:
            partitions.append(
                reserve(
                    "partition",
                    group["id"],
                    group["files"],
                    [*group["selected"], *group["deselected"]],
                    collected["environment"],
                )
            )
        bindings = {
            "schema_version": 1,
            "parent": parent,
            "manifest_digest": manifest["manifest_digest"],
            "children": [binding_row(c) for c in children],
        }
        bindings_reference = compiler.write_document(directory, "bindings.json", bindings)
        progress(
            "partitions reserved",
            partitions=len(partitions),
            selected=len(collected["selected"]),
            deselected=len(collected["deselected"]),
        )
        for number, partition in enumerate(partitions):
            if evidence._fingerprint(entry) != entry["fp_before"]:
                raise ValueError("recipe source changed before partition execution")
            progress(
                "partition " + str(number + 1) + "/" + str(len(partitions)),
                group=partition[1]["group_id"],
            )
            receipt, _ = execute(*partition)
            receipts.append(receipt)
        result = compiler.reconcile_recipe(manifest, receipts)
        aggregate = {
            "schema_version": 2,
            "kind": "harness-recipe-aggregate-v2",
            "parent": parent,
            "recipe": recipe,
            "manifest": manifest_reference,
            "bindings": bindings_reference,
            "collection": collection_receipt,
            "receipts": receipts,
            "result": result,
        }
        aggregate_reference = compiler.write_document(directory, "aggregate.json", aggregate)
        entry["recipe_execution"] = {
            "schema_version": 1,
            "session": sid,
            "aggregate": aggregate_reference,
        }
        entry["counts"] = result["counts"]
        entry["fp_after"] = evidence._fingerprint(entry)
        entry["status"] = (
            "TORN"
            if entry["fp_before"] != entry["fp_after"]
            else "PASS"
            if result["selected_success"]
            else "FAIL"
            if result["accounting_complete"]
            else "INFRA"
        )
        entry["note"] = (
            "exact independent partition reconciliation; monolithic CI obligations unchanged"
        )
        progress("reconciled", status=entry["status"], counts=entry["counts"])
    except BaseException as error:
        finish_pending(type(error).__name__)
        entry.update(
            status="INFRA",
            note="recipe execution incomplete: " + type(error).__name__,
            fp_after=evidence._fingerprint(entry),
        )
        import traceback

        frames = [
            {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)[-8:]
        ]
        code = getattr(error, "code", None) if isinstance(error, fs.StorageError) else None
        progress("incomplete", reason=type(error).__name__, code=code, frames=frames)
        for context in contexts.values():
            if context.get("prepared"):
                context["prepared"].retain("recipe_reconciliation_missing")
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            entry["interrupted"] = True
        failure = error
    else:
        failure = None
    entry["ended_at"] = time.time()
    entry["log_sha256"] = ledger.file_sha(log)
    evidence._commit(sid, entry)
    if entry.get("rejected"):
        raise ValueError("recipe coordinator reservation was not committed")

    def closure(reference):
        current = al_store.load(sid)
        if current["run_id"] != entry["run"]:
            raise ValueError("recipe run changed before acknowledgement")
        completions = {}
        for child in children:
            prepared = contexts[child["id"]]["prepared"]
            completions[child["id"]] = {
                "completion": prepared.cleanup,
                "launch_token": prepared.proc.handle if prepared.remote else prepared.owner.token,
            }
        return compiler.reconcile_closure(
            reference,
            root=publication_root,
            entries={e["id"]: e for e in ledger.ledger(current)["entries"]},
            completions=completions,
            current_source=evidence._fingerprint(entry),
        )

    cleanup = []
    if failure is None and aggregate_reference is not None and entry["status"] != "TORN":
        for child in children:
            prepared = contexts[child["id"]]["prepared"]
            prepared.closure_verifier = closure
            try:
                outcome = prepared.acknowledge(reconciliation=aggregate_reference)
            except (OSError, ValueError, KeyError, TypeError):
                prepared.retain("recipe_acknowledgment_unverified")
                outcome = {"state": "RETAINED", "reason": "recipe_acknowledgment_unverified"}
            cleanup.append({"entry": child["id"], "cleanup": outcome})
        fs.publish_json(
            directory / "cleanup.json",
            {"schema_version": 1, "aggregate": aggregate_reference, "results": cleanup},
        )
    print(
        "AL · evidence "
        + entry["id"]
        + " · "
        + entry["area"]
        + " · "
        + entry["status"]
        + " · "
        + ledger.counts_text(entry)
    )
    if isinstance(failure, (KeyboardInterrupt, SystemExit)):
        raise failure
    return entry


def assess_recipe(entry):
    import al_evidence
    import al_ledger
    import al_store

    _, _, compiler = modules()
    load_recipe(entry["recipe"])
    execution = entry.get("recipe_execution")
    if (
        not isinstance(execution, dict)
        or set(execution) != {"schema_version", "session", "aggregate"}
        or execution["schema_version"] != 1
    ):
        raise ValueError("recipe aggregate reference unavailable")
    state = al_store.load(execution["session"])
    if state["run_id"] != entry["run"]:
        raise ValueError("recipe belongs to another run")
    root = al_ledger.log_dir(execution["session"]) / "storage"
    closure = compiler.reconcile_closure(
        execution["aggregate"],
        root=root,
        entries={e["id"]: e for e in state["evidence"]["entries"]},
        current_source=al_evidence._fingerprint(dict(entry)),
    )
    if closure["result"]["counts"] != entry["counts"] or not closure["result"]["selected_success"]:
        raise ValueError("recipe aggregate is not successful")
    return closure
