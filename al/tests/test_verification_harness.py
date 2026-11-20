"""Exact recipe accounting and conservative fixture grouping are cleanup invariants."""

import json
from pathlib import Path

import pytest

from al.installer import verification_contracts as C
from al.installer import verification_harness as H
from al.installer.storage_filesystem import StorageError, digest


def recipe():
    return json.loads((Path(__file__).parents[1] / "verification-harness.json").read_bytes())


def node(file, name, *, browser=False, fixture=None):
    return {
        "nodeid": file + "::" + name,
        "source": {"path": file, "sha256": "a" * 64},
        "fixtures": [] if fixture is None else [fixture],
        "parameterization": None,
        "markers": ["browser"] if browser else [],
        "initial_fixtures": [],
    }


def fixture(*, dynamic=False, scope="session", builtin=False, name="shared"):
    return {
        "name": name,
        "scope": scope,
        "params": "b" * 64,
        "arguments": [],
        "source": {
            "path": "external:_pytest/tmpdir.py" if builtin else "tests/conftest.py",
            "sha256": "c" * 64,
        },
        "implementation": "d" * 64,
        "dynamic": dynamic,
        "builtin": builtin,
    }


def collection(nodes):
    return {
        "schema_version": 1,
        "selected": [n for n in nodes if "browser" not in n["markers"]],
        "deselected": [n for n in nodes if "browser" in n["markers"]],
        "inventory": nodes,
        "source_digest": "e" * 64,
        "recipe_digest": digest(recipe()),
        "pytest_version": "9.1.1",
        "environment": {"pytest": "9.1.1"},
    }


def plan(nodes):
    return H.plan_recipe(recipe(), collection(nodes), {"content_digest": "e" * 64})


def test_file_groups_preserve_parameters_and_module_users():
    values = [
        node("tests/test_a.py", "test_x[a]"),
        node("tests/test_a.py", "test_x[b]"),
        node("tests/test_b.py", "test_y"),
        node("tests/test_a.py", "test_browser", browser=True),
    ]
    result = plan(values)
    assert len(result["groups"]) == 2
    assert len(result["groups"][0]["selected"]) == 2
    assert result["groups"][0]["deselected"] == [values[-1]]


def test_cross_file_session_fixture_joins_only_actual_consumers():
    shared = fixture()
    result = plan(
        [
            node("tests/test_a.py", "test_a", fixture=shared),
            node("tests/test_b.py", "test_b", fixture=shared),
            node("tests/test_c.py", "test_c"),
        ]
    )
    assert [g["files"] for g in result["groups"]] == [
        ["tests/test_a.py", "tests/test_b.py"],
        ["tests/test_c.py"],
    ]


def test_unknown_dynamic_fixture_conservatively_joins_entire_inventory():
    result = plan(
        [
            node("tests/test_a.py", "test_a", fixture=fixture(dynamic=True)),
            node("tests/test_b.py", "test_b"),
        ]
    )
    assert len(result["groups"]) == 1


def test_reviewed_builtin_session_fixture_can_be_recreated_with_bound_source():
    builtin = fixture(builtin=True, name="tmp_path_factory")
    assert (
        len(
            plan(
                [
                    node("tests/test_a.py", "test_a", fixture=builtin),
                    node("tests/test_b.py", "test_b", fixture=builtin),
                ]
            )["groups"]
        )
        == 2
    )


@pytest.mark.parametrize("change", ["duplicate", "missing", "wrong-marker"])
def test_collection_never_discards_unknown_or_duplicate_identities(change):
    value = collection(
        [node("tests/test_a.py", "test_a"), node("tests/test_a.py", "test_browser", browser=True)]
    )
    if change == "duplicate":
        value["selected"].append(value["selected"][0])
    elif change == "missing":
        value["deselected"] = []
    else:
        value["selected"], value["deselected"] = value["deselected"], value["selected"]
    with pytest.raises(StorageError):
        C.validate_collection(recipe(), value, {"content_digest": "e" * 64})


def test_duplicate_stage_cannot_overwrite_failure_with_pass():
    events = [
        {"when": stage, "outcome": "passed", "wasxfail": False, "duration": 0.01}
        for stage in ("setup", "call", "teardown")
    ]
    events.insert(1, {"when": "call", "outcome": "failed", "wasxfail": False, "duration": 0.01})
    status, errors = C.stage_outcome(events)
    assert status == "error" and errors == ["duplicate stage observation"]


@pytest.mark.parametrize("outcome", ["failed", "skipped", "xfailed", "xpassed"])
def test_known_unsuccessful_sibling_does_not_invalidate_independent_success(outcome):
    manifest = plan([node("tests/test_a.py", "test_a"), node("tests/test_b.py", "test_b")])
    receipts = []
    for group, status in zip(manifest["groups"], ["passed", outcome], strict=True):
        receipts.append(
            {
                "group_id": group["id"],
                "assignment_digest": "f" * 64,
                "entry": {"id": group["id"]},
                "publication": {"receipt": group["id"]},
                "result": {
                    "accounting_complete": True,
                    # Explicit trusted-basis fixture; this is not a platform claim.
                    "producer_closed": True,
                    "structural_errors": [],
                    "outcomes": {n["nodeid"]: status for n in group["selected"]},
                },
            }
        )
    result = H.reconcile_recipe(manifest, receipts)
    assert result["accounting_complete"] is True
    assert result["selected_success"] is False
    assert result["eligibility"] == {"group-0000": True, "group-0001": False}
    assert H.reconcile_recipe(manifest, receipts[:1])["eligibility"]["group-0000"] is False


def test_paged_assignment_rejects_changed_or_missing_inventory(tmp_path):
    parent = {"run": "actual-run", "id": "E1", "reservation": "current-reservation"}
    assignment, reference = H.assignment(
        kind="partition",
        parent=parent,
        source_digest="e" * 64,
        recipe_digest=digest(recipe()),
        group_id="group-0000",
        files=["tests/test_a.py"],
        inventory=[node("tests/test_a.py", "test_a")],
        environment={},
        directory=tmp_path,
    )
    assert H.read_assignment(reference, root=tmp_path)[0] == assignment
    page = tmp_path / assignment["inventory"]["pages"][0]["name"]
    page.write_text("{}")
    with pytest.raises(StorageError):
        H.read_assignment(reference, root=tmp_path)


def test_large_document_is_paged_without_raising_metadata_limit(tmp_path):
    value = {"records": ["x" * 1000 for _ in range(2100)]}
    reference = H.write_document(tmp_path, "large.json", value)
    assert H.read_document(reference, root=tmp_path) == value
    assert len(list(tmp_path.glob("large.json-part-*"))) > 1
    assert all(p.stat().st_size <= 1048576 for p in tmp_path.iterdir())


def test_actual_fixed_child_collects_exact_selection_and_stage_reports(tmp_path):
    import os
    import sys

    from al.installer import storage_declarations as grammar
    from al.installer.owned_process import OwnedProcess
    from al.installer.storage_filesystem import artifact

    repository = tmp_path / "repository"
    (repository / "tests").mkdir(parents=True)
    (repository / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers=["browser: browser-only"]\n'
    )
    (repository / "tests/test_pass.py").write_text("def test_pass():\n    assert True\n")
    (repository / "tests/test_skip.py").write_text(
        'import pytest\ndef test_skip():\n    pytest.skip("known skip")\n'
    )
    (repository / "tests/test_browser.py").write_text(
        "import pytest\n@pytest.mark.browser\ndef test_browser():\n    assert False\n"
    )
    parent = {"run": "actual-run", "id": "E1", "reservation": "protected-parent"}

    def run_child(name, *, kind, files, inventory, environment):
        durable = tmp_path / name / "durable"
        durable.mkdir(mode=0o700, parents=True)
        temporary = tmp_path / name / "temporary"
        temporary.mkdir(mode=0o700)
        value, reference = H.assignment(
            kind=kind,
            parent=parent,
            source_digest="e" * 64,
            recipe_digest=digest(recipe()),
            group_id=name,
            files=files,
            inventory=inventory,
            environment=environment,
            directory=durable / "assignment",
        )
        grammar.prepare_directories(H.report_declaration(), temporary)
        process_directory = durable / "process"
        process_directory.mkdir(mode=0o700)
        owner = OwnedProcess(process_directory, value["assignment_digest"])
        argv = H.materialize_child(
            temporary,
            reference,
            trusted_root=durable,
            launch_token=owner.token,
            interpreter=sys.executable,
        )
        with (durable / "log").open("wb") as log:
            try:
                process = owner.spawn(argv, cwd=repository, env={"PATH": os.defpath}, output=log)
                code = process.wait(timeout=30)
            finally:
                completion = owner.cleanup()
        assert completion["complete"] and not completion["errors"] and not completion["unresolved"]
        outputs = {
            row["name"]: artifact(temporary / "outputs" / row["filename"])
            for row in H.report_declaration()["outputs"]
            if (temporary / "outputs" / row["filename"]).is_file()
        }
        report = H.read_report(outputs)
        result = C.validate_child_report(
            value,
            report,
            {
                "returncode": code,
                "launch_token": owner.token,
                "assigned_nodes": inventory if kind == "partition" else None,
            },
        )
        assert result["accounting_complete"], (result, (durable / "log").read_text())
        H.validate_xml(outputs, result, kind=kind)
        return code, result

    code, result = run_child(
        "collect", kind="collection", files=["tests"], inventory=[], environment={}
    )
    assert code == 0 and result["selected_success"]
    assert {n["nodeid"] for n in result["selected"]} == {
        "tests/test_pass.py::test_pass",
        "tests/test_skip.py::test_skip",
    }
    assert [n["nodeid"] for n in result["deselected"]] == ["tests/test_browser.py::test_browser"]
    for filename, expected_success in [("tests/test_pass.py", True), ("tests/test_skip.py", False)]:
        inventory = [n for n in result["inventory"] if n["source"]["path"] == filename]
        code, executed = run_child(
            Path(filename).stem,
            kind="partition",
            files=[filename],
            inventory=inventory,
            environment=result["environment"],
        )
        assert code == 0 and executed["selected_success"] is expected_success


def test_successful_observations_do_not_imply_producer_closure():
    manifest = plan([node("tests/test_a.py", "test_a")])
    group = manifest["groups"][0]
    receipt = {
        "group_id": group["id"],
        "assignment_digest": "f" * 64,
        "entry": {"id": "E2"},
        "publication": {"capture": "protected"},
        "result": {
            "accounting_complete": True,
            "selected_success": True,
            "producer_closed": False,
            "structural_errors": [],
            "outcomes": {group["selected"][0]["nodeid"]: "passed"},
        },
    }
    observed = H.reconcile_recipe(manifest, [receipt])
    assert observed["selected_success"] is True and observed["accounting_complete"] is True
    assert observed["producer_closure_complete"] is False
    assert observed["eligibility"] == {group["id"]: False}


def test_normal_and_prepared_recipe_children_keep_target_imports_outside_sealed_code(
    tmp_path, monkeypatch
):
    import hashlib
    import os
    import sys

    import harness.storage as storage
    from al.installer import storage_declarations as grammar
    from al.installer.owned_process import OwnedProcess
    from al.installer.storage_filesystem import artifact

    repository = Path(__file__).resolve().parents[2]
    source_names = set(storage.CANONICAL_MODULES)
    assert len(source_names) == 8
    source_inventory = storage.core_sources()
    assert set(source_inventory) == source_names | {"__init__.py"}

    prepared_root = tmp_path / "prepared-runtime"
    package = prepared_root / "harness"
    sealed = package / "_storage_core"
    sealed.mkdir(mode=0o700, parents=True)
    for name, raw in source_inventory.items():
        (sealed / name).write_bytes(raw)
    manifest = {
        "files": {
            "_storage_core/" + name: hashlib.sha256(raw).hexdigest()
            for name, raw in source_inventory.items()
        }
    }
    (prepared_root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    (package / "storage.py").write_text("# prepared source marker\n")

    parent = {"run": "actual-run", "id": "E1", "reservation": "protected-parent"}
    fixed_child_files = {
        "storage_filesystem.py",
        "storage_declarations.py",
        "verification_contracts.py",
        "verification_harness.py",
        "verification_pytest.py",
        "verification_harness_child.py",
    }

    def exercise(label, compiler):
        assert set(compiler.child_source_inventory()) == fixed_child_files

        def run_child(name, *, kind, inventory, environment):
            durable = tmp_path / label / name / "durable"
            durable.mkdir(mode=0o700, parents=True)
            temporary = tmp_path / label / name / "temporary"
            temporary.mkdir(mode=0o700)
            value, reference = compiler.assignment(
                kind=kind,
                parent=parent,
                source_digest="e" * 64,
                recipe_digest=digest(recipe()),
                group_id=label + "-" + name,
                files=["tests/test_execution_bootstrap_closure.py"],
                inventory=inventory,
                environment=environment,
                directory=durable / "assignment",
            )
            grammar.prepare_directories(compiler.report_declaration(), temporary)
            process_directory = durable / "process"
            process_directory.mkdir(mode=0o700)
            owner = OwnedProcess(process_directory, value["assignment_digest"])
            argv = compiler.materialize_child(
                temporary,
                reference,
                trusted_root=durable,
                launch_token=owner.token,
                interpreter=sys.executable,
            )
            with (durable / "log").open("wb") as log:
                try:
                    process = owner.spawn(
                        argv,
                        cwd=repository,
                        env={
                            "PATH": os.defpath,
                            "PYTHONDONTWRITEBYTECODE": "1",
                            **(
                                {"HARNESS_EXPECT_SEALED_RECIPE": "1"} if kind == "partition" else {}
                            ),
                        },
                        output=log,
                    )
                    code = process.wait(timeout=60)
                finally:
                    completion = owner.cleanup()
            assert completion["complete"] and not completion["errors"]
            assert not completion["unresolved"]
            outputs = {
                row["name"]: artifact(temporary / "outputs" / row["filename"])
                for row in compiler.report_declaration()["outputs"]
                if (temporary / "outputs" / row["filename"]).is_file()
            }
            report = compiler.read_report(outputs)
            result = C.validate_child_report(
                value,
                report,
                {
                    "returncode": code,
                    "launch_token": owner.token,
                    "assigned_nodes": inventory if kind == "partition" else None,
                },
            )
            assert result["accounting_complete"], (result, (durable / "log").read_text())
            compiler.validate_xml(outputs, result, kind=kind)
            return code, result

        code, collection_result = run_child(
            "collect", kind="collection", inventory=[], environment={}
        )
        assert code == 0 and collection_result["selected_success"]
        nodes = collection_result["inventory"]
        expected_nodeids = {
            "tests/test_execution_bootstrap_closure.py::test_snapshot_contains_executable_contribution_contract[False]",
            "tests/test_execution_bootstrap_closure.py::test_snapshot_contains_executable_contribution_contract[True]",
            "tests/test_execution_bootstrap_closure.py::test_recipe_target_imports_materializer_and_observer_from_bound_repository",
        }
        assert {item["nodeid"] for item in nodes} == expected_nodeids
        code, partition = run_child(
            "partition",
            kind="partition",
            inventory=nodes,
            environment=collection_result["environment"],
        )
        assert code == 0 and partition["selected_success"]
        assert len(partition["selected"]) == len(expected_nodeids)

    exercise("normal", H)
    monkeypatch.setattr(storage, "__file__", str(package / "storage.py"))
    prepared_compiler = storage.load_core()["verification_harness"]
    assert Path(prepared_compiler.__file__).resolve().parent == sealed.resolve()
    exercise("prepared", prepared_compiler)
