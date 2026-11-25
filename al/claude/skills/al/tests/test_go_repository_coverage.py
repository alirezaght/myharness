"""Go discovery is static, module-bounded and never an empty-universe proof."""

import pytest
from test_service_coverage import discover, put, start

from conftest import _git


def go_repository(repo):
    (repo / "pyproject.toml").unlink(missing_ok=True)
    (repo / "svc/app.py").unlink(missing_ok=True)
    (repo / "web/app.js").unlink(missing_ok=True)


def module(repo, path=".", name="example.invalid/app"):
    prefix = "" if path == "." else path + "/"
    put(repo, prefix + "go.mod", "module " + name + "\n\ngo 1.22\n")
    put(repo, prefix + "main.go", "package main\nfunc main() {}\n")


def pinned(repo):
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "literal Go fixture")


def test_root_go_change_requires_real_verification(cli, repo, tmp_path):
    go_repository(repo)
    module(repo)
    put(repo, "Makefile", "test:\n\tgo test ./...\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    c = manifest["service_coverage"]
    a = c["assessment"]
    assert [u["id"] for u in c["semantic_requirements"]["units"]] == ["."]
    assert a["selected_services"] == ["."]
    assert a["outcome"] == "incomplete"
    assert any(g["code"] == "missing-verification-check" for g in a["gaps"])


def test_nested_modules_are_owned_and_reverse_dependencies_are_selected(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "backend/shared", "example.invalid/shared")
    module(repo, "backend/app")
    put(
        repo,
        "backend/app/go.mod",
        (
            "module example.invalid/app\nrequire example.invalid/shared v0"
            ".0.0\nreplace example.invalid/shared => ../shared\n"
        ),
    )
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["backend/shared/main.go"])
    c = manifest["service_coverage"]
    assert {u["id"] for u in c["semantic_requirements"]["units"]} == {
        "backend/shared",
        "backend/app",
    }
    assert c["assessment"]["selected_services"] == ["backend/app", "backend/shared"]


def test_unowned_go_source_is_not_complete_empty_universe(cli, repo, tmp_path):
    go_repository(repo)
    put(repo, "tools/main.go", "package main\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["tools/main.go"])
    assert manifest["service_coverage"]["assessment"]["outcome"] == "incomplete"
    assert any(
        g["code"] == "unowned-go-source" for g in manifest["service_coverage"]["assessment"]["gaps"]
    )


@pytest.mark.parametrize(
    "text",
    [
        (
            "module example.invalid/m\nrequire (\nexample.invalid/a v1.2.3 "
            "// comment\n)\nreplace example.invalid/a => ../a\n"
        ),
        (
            'module "example.invalid/m"\ngo 1.22.1\ntoolchain go1.22.1\nexcl'
            "ude example.invalid/a v1.2.3\n"
        ),
    ],
)
def test_literal_go_manifest(text):
    from al_repo_go import parse_mod

    assert parse_mod(text)["module"] == "example.invalid/m"


@pytest.mark.parametrize(
    "text",
    [
        "module a\nmodule b",
        "module a\nrequire (\na v1.2.3",
        "module a\nrequire b $(shell curl)",
        "module a\nreplace a => /external",
        "module a\nunknown thing",
        "module a\nrequire b latest",
        "module a\ngo 1.22\ngo 1.23",
        "module a\nreplace a => ../b\nreplace a => ../c",
    ],
)
def test_unsupported_go_manifest_closed(text):
    from al_repo_go import parse_mod

    with pytest.raises(ValueError):
        parse_mod(text)


@pytest.mark.parametrize(
    "args,complete",
    [
        (["go", "test", "./..."], True),
        (["go", "test", "."], False),
        (["go", "test", "./sub/..."], False),
        (["go", "vet", "./..."], True),
        (["go", "build", "./..."], True),
        (["go", "test", "-run=Only", "./..."], False),
        (["go", "test", "-list=Only", "./..."], False),
        (["go", "test", "-count=0", "./..."], False),
        (["gotestsum", "--format=testname", "--", "-race", "./..."], True),
        (["gotestsum", "--", "-run=Only", "./..."], False),
    ],
)
def test_go_package_and_recursive_semantics(args, complete):
    from al_go_checks import covers, observed

    records = observed(args)
    assert any(covers({"path": "."}, r) for r in records) == complete
    assert not any(covers({"path": "nested"}, r) for r in records)


def test_workspace_base_edges_survive_deleted_dependency(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "a", "example.invalid/a")
    module(repo, "b", "example.invalid/b")
    put(repo, "a/go.mod", "module example.invalid/a\nrequire example.invalid/b v0.0.0\n")
    put(repo, "go.work", "go 1.22\nuse (\n./a\n./b\n)\n")
    pinned(repo)
    start(cli)
    (repo / "a/go.mod").write_text("module example.invalid/a\n")
    _, manifest = discover(cli, tmp_path, ["b/main.go"])
    c = manifest["service_coverage"]
    assert c["assessment"]["selected_services"] == ["a", "b"]
    assert ["a", "b"] in c["semantic_requirements"]["dependencies"]["go_module_edges"]


def test_workspace_absent_member_is_explicit(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "a")
    put(repo, "go.work", "use ./hidden\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["a/main.go"])
    assert any(
        g["code"] == "unsupported-go-workspace"
        for g in manifest["service_coverage"]["assessment"]["gaps"]
    )


@pytest.mark.parametrize(
    "recipe",
    [
        "test:\n\tgo test ./...\n",
        ".PHONY: test unit\ntest: unit\nunit:\n\tgo test -race ./...\n",
        "include checks.mk\ntest: unit\n",
    ],
)
def test_literal_make_recipe_is_source_bound_without_execution(repo, monkeypatch, recipe):
    import subprocess

    from al_go_checks import resolve

    go_repository(repo)
    module(repo)
    put(repo, "Makefile", recipe)
    put(repo, "checks.mk", "unit:\n\tgo test ./...\n")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("discovery executed a process")
    )
    result = resolve(repo, ["make", "test"], lambda p: (repo / p).read_bytes())
    assert result["records"][0]["go_operation"] == "test"
    assert {"Makefile", "go.mod"} <= set(result["dependencies"])
    if recipe.startswith("include"):
        assert "checks.mk" in result["dependencies"]


@pytest.mark.parametrize(
    "recipe",
    [
        "X := $(shell curl bad)\ntest:\n\tgo test ./...\n",
        "test:\n\t-go test ./...\n",
        "test:\n\tgo test ./... || true\n",
        "test:\n\t$(MAKE) unit\n",
        "include $(FILES)\n",
        "test: loop\nloop: test\n",
        "test:\n\tgo test -run=One ./...\n",
    ],
)
def test_unfaithful_make_is_not_a_verification_proof(repo, recipe):
    from al_ci_yaml import Unsupported
    from al_go_checks import resolve

    go_repository(repo)
    module(repo)
    put(repo, "Makefile", recipe)
    with pytest.raises(Unsupported):
        resolve(repo, ["make", "test"], lambda p: (repo / p).read_bytes())


@pytest.mark.parametrize("provider", ["gitlab", "github", "environment"])
def test_public_ci_go_recipe_is_bound_without_tool_execution(cli, repo, tmp_path, provider):
    go_repository(repo)
    module(repo)
    put(repo, "Makefile", "test:\n\tgo test ./...\n")
    if provider == "github":
        put(
            repo,
            ".github/workflows/go.yml",
            (
                "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    step"
                "s:\n      - uses: actions/checkout@v4\n      - run: make test\n"
            ),
        )
    else:
        extra = (
            "  environment:\n    name: checks\n    action: prepare\n"
            if provider == "environment"
            else ""
        )
        put(repo, ".gitlab-ci.yml", "test:\n" + extra + "  script: make test\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    coverage = manifest["service_coverage"]
    assert coverage["assessment"]["outcome"] == "identified", coverage["assessment"]["gaps"]
    assert coverage["assessment"]["selected_services"] == ["."]
    bindings = coverage["assessment"]["required_bindings"]
    assert bindings and any("verification-root" in b["obligation_ids"] for b in bindings)
    assert any(any(r["path"] == "Makefile" for r in b["source_chain"]) for b in bindings)


def test_docs_do_not_select_unrelated_go_module(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "backend/a")
    put(repo, "docs/readme.md", "public documentation\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["docs/readme.md"])
    c = manifest["service_coverage"]
    assert c["assessment"]["selected_services"] == []
    assert c["assessment"]["outcome"] == "identified"
    assert c["global_inventory"]["status"] == "incomplete"


def test_go_source_symlink_never_reads_target(cli, repo, tmp_path):
    go_repository(repo)
    module(repo)
    external = tmp_path / "external"
    external.mkdir()
    (external / "go.mod").write_text("module secret\n")
    (repo / "linked").symlink_to(external, target_is_directory=True)
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["linked"])
    assert manifest["service_coverage"]["assessment"]["outcome"] == "incomplete"
    assert not any(
        u["id"] == "linked" for u in manifest["service_coverage"]["semantic_requirements"]["units"]
    )


def test_mixed_python_go_shared_artifact_consumers(cli, repo, tmp_path):
    from test_service_coverage import unit

    go_repository(repo)
    unit(repo, "services/python")
    module(repo, "backend/go")
    put(repo, "shared/schema.json", '{"type":"object"}\n')
    put(repo, "services/python/src/app.py", 'SCHEMA="schema.json"\n')
    put(repo, "backend/go/main.go", 'package main\nconst schema="schema.json"\nfunc main(){}\n')
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["shared/schema.json"])
    c = manifest["service_coverage"]
    assert c["assessment"]["selected_services"] == ["backend/go", "services/python"]
    assert not c["assessment"]["dependency_uncertainties"]


def test_malformed_unrelated_module_is_deferred_not_erased(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "a")
    module(repo, "b", "example.invalid/b")
    put(repo, "b/go.mod", "module example.invalid/b\nunsupported dynamic\n")
    put(repo, "docs/readme.md", "documentation\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["docs/readme.md"])
    c = manifest["service_coverage"]
    assert c["assessment"]["outcome"] == "identified", c["assessment"]["gaps"]
    assert c["global_inventory"]["status"] == "incomplete"
    assert any(
        g["record"].get("code") == "unsupported-go-module" for g in c["projection"]["records"]
    )


def test_go_versions_cannot_be_laundered_into_v1(cli, repo, tmp_path):
    from copy import deepcopy

    from al_repository_facts import validate_facts

    go_repository(repo)
    module(repo)
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    facts = deepcopy(manifest["service_coverage"]["semantic_requirements"])
    facts["scan"] = {"stable": True}
    facts.update(schema_version=1, policy_version=1)
    with pytest.raises(ValueError):
        validate_facts(facts)
    facts["adapter"] = {"id": "python", "version": 1}
    with pytest.raises(ValueError):
        validate_facts(facts)


def test_real_go_public_planning_and_pending_proof_guards(
    cli, repo, tmp_path, monkeypatch, state_file
):
    import json
    from pathlib import Path

    from hook_regression_helpers import invoke
    from launcher_context import admit_planning

    cli = cli.raw
    executable = prepare_go(tmp_path, monkeypatch)
    go_repository(repo)
    module(repo)
    put(
        repo,
        "main_test.go",
        (
            'package main\nimport "testing"\nfunc TestActual(t *testing.T) '
            '{ if 1+1 != 2 { t.Fatal("arithmetic") } }\n'
        ),
    )
    put(repo, "Makefile", "test:\n\tgo test -v -count=1 ./...\n")
    put(repo, ".gitlab-ci.yml", "test:\n  script: make test\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    p = tmp_path / "actual-plan.json"
    p.write_text(json.dumps(manifest))
    invoke(cli, "gates", "plan", "--file", str(p))
    # Discovery does not manufacture an authored proof admission.
    before = state_file().read_bytes()
    invoke(cli, "planning", "admit", ok=False)
    assert state_file().read_bytes() == before
    invoke(cli, "gates", "run", "--stage", "base")
    admit_planning(cli)
    state = json.loads(cli("show")[1])
    assert state["proof_admission"]["status"] == "admitted"
    assert any(
        e["status"] == "PASS" and "--- PASS: TestActual " in Path(e["log"]).read_text()
        for e in state["evidence"]["entries"]
    )
    invoke(cli, "phase", "IMPLEMENTATION")
    invoke(
        cli, "writer", "start", "--id", "go-owner", "--role", "al-engineer", "--files", "main.go"
    )
    invoke(cli, "writer", "finish", "--id", "go-owner", "--outcome", "success")
    invoke(cli, "gates", "run", "--stage", "candidate")
    invoke(cli, "phase", "VERIFICATION")
    from al_go_checks import GO_ENV

    invoke(
        cli,
        "evidence",
        "plan",
        "--area",
        "actual-go-suite",
        "--tier",
        "broad",
        "--kind",
        "check",
        "--scope",
        ".",
        *[arg for name in GO_ENV for arg in ("--env", name)],
        "--",
        executable,
        "test",
        "-v",
        "-count=1",
        "./...",
    )
    invoke(cli, "evidence", "run", "--area", "actual-go-suite")
    from launcher_context import observe_behavior

    observe_behavior(cli, path="main.go")
    invoke(cli, "review-start")


def test_missing_go_recipe_blocks_actual_planning_admission(cli, repo, tmp_path, state_file):
    import json

    from hook_regression_helpers import invoke
    from launcher_context import _reports

    cli = cli.raw
    go_repository(repo)
    module(repo)
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    p = tmp_path / "missing-plan.json"
    p.write_text(json.dumps(manifest))
    invoke(cli, "gates", "plan", "--file", str(p))
    packet = json.loads(invoke(cli, "planning", "packet")[1])
    p = tmp_path / "report.json"
    p.write_text(json.dumps(_reports().report_for(packet)))
    response = json.loads(invoke(cli, "planning", "report", "--file", str(p))[1])
    # A missing recipe is verification debt: admission records it, final proof refuses.
    invoke(cli, "planning", "admit", "--file", response["materialized_obligations"]["stored_path"])
    state = json.loads(cli("show")[1])
    assert state["proof_admission"]["status"] == "admitted"
    obligations = state["implementation_readiness"]["obligations"]
    assert any(o.startswith("coverage incomplete") for o in obligations)
    import al_service_coverage as Coverage

    assert Coverage.admission_gaps(state, current=True)


@pytest.mark.parametrize("provider", ["gitlab", "github"])
def test_container_go_identification_has_no_host_tool_requirement_or_local_authority(
    cli, repo, tmp_path, monkeypatch, provider
):
    import shutil

    go_repository(repo)
    module(repo)
    if provider == "gitlab":
        put(repo, ".gitlab-ci.yml", "test:\n  image: golang:1.22\n  script: go test ./...\n")
    else:
        put(
            repo,
            ".github/workflows/go.yml",
            (
                "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    cont"
                "ainer: golang:1.22\n    steps:\n      - uses: actions/checkout"
                "@v4\n      - run: go test ./...\n"
            ),
        )
    pinned(repo)
    start(cli)
    monkeypatch.setenv("GOENV", str(tmp_path / "unobserved-host-go-config"))
    monkeypatch.setenv("GOFLAGS", "-run=^$")
    monkeypatch.setattr(
        shutil, "which", lambda *a, **k: pytest.fail("discovery queried executable availability")
    )
    _, manifest = discover(cli, tmp_path, ["main.go"])
    facts = manifest["service_coverage"]["semantic_requirements"]
    job = facts["ci_observations"]["obligations"][0]
    observed = job["verification_observations"][0]
    assert observed["host_go_required"] is False and observed["execution_status"] == "unverified"
    assert job["execution"] is None
    # A job AL cannot run locally is pipeline-verified (#17): an unverified obligation, never
    # a certified check, never a local CI gate.
    assessment = manifest["service_coverage"]["assessment"]
    assert assessment["outcome"] == "identified"
    assert not any(b.get("conventional_verification") for b in assessment["required_bindings"])
    delegated = facts["checks"]["pipeline_verified"]
    assert [[j["job"] for j in d["jobs"]] for d in delegated] == [["test"]]
    assert not any(g["argv"] for g in manifest["gates"] if g["category"] == "ci")


def test_missing_actual_go_is_infra_and_review_refuses_with_recorded_debt(
    cli, repo, tmp_path, monkeypatch, state_file
):
    import json
    import os
    import shutil

    from hook_regression_helpers import invoke

    cli = cli.raw
    monkeypatch.setenv("PATH", os.defpath)
    if shutil.which("go"):
        pytest.skip("standard host PATH supplies Go; missing-tool fixture unavailable")
    go_repository(repo)
    module(repo)
    put(repo, ".gitlab-ci.yml", "test:\n  script: go test ./...\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(manifest))
    assert manifest["service_coverage"]["assessment"]["outcome"] == "identified"
    invoke(cli, "gates", "plan", "--file", str(p))
    cli("gates", "run", "--stage", "base")
    st = json.loads(cli("show")[1])
    assert st["evidence"]["entries"]
    assert any(e["status"] == "INFRA" for e in st["evidence"]["entries"])
    assert not any(e["status"] == "PASS" for e in st["evidence"]["entries"])
    from launcher_context import admit_planning

    admit_planning(cli)
    invoke(cli, "phase", "IMPLEMENTATION")
    invoke(
        cli, "writer", "start", "--id", "blocked-go", "--role", "al-engineer", "--files", "main.go"
    )
    assert json.loads(cli("show")[1])["implementation_readiness"]["obligations"]
    invoke(cli, "writer", "finish", "--id", "blocked-go", "--outcome", "success")
    invoke(cli, "phase", "VERIFICATION")
    before = state_file().read_bytes()
    invoke(cli, "review-start", ok=False)
    assert state_file().read_bytes() == before


def test_go_manifest_parser_bounds_are_closed():
    from al_repo_go import MAX_BYTES, MAX_ROWS, parse_mod, relative

    with pytest.raises(ValueError):
        parse_mod("module x\n" + " " * MAX_BYTES)
    with pytest.raises(ValueError):
        parse_mod("module x\n" + "\n" * MAX_ROWS)
    with pytest.raises(ValueError):
        relative(".", "../outside")


def test_go_capture_retries_one_manifest_race(repo, monkeypatch):
    import al_repo_go as Go
    from al_repository_facts import collect

    go_repository(repo)
    module(repo)
    pinned(repo)
    original = Go.parse_mod
    calls = []

    def changed(text):
        result = original(text)
        calls.append(text)
        if len(calls) == 1:
            put(repo, "go.mod", "module example.invalid/new\ngo 1.22\n")
        return result

    monkeypatch.setattr(Go, "parse_mod", changed)
    import subprocess

    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    facts = collect(repo, base_oid=base, paths=["main.go"], opaque=[])
    assert facts["capture"]["attempts"] == 2
    assert facts["units"][0]["go"]["snapshots"]["current"]["module"] == "example.invalid/new"


def test_workspace_opaque_member_has_gap_without_body_read(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "a")
    put(repo, "go.work", "use (\n./a\n./hidden\n)\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["a/main.go"])
    assert manifest["service_coverage"]["assessment"]["outcome"] == "incomplete"
    assert any(
        g["code"] == "unsupported-go-workspace"
        for g in manifest["service_coverage"]["assessment"]["gaps"]
    )


def test_ignored_workspace_member_is_not_read_or_promoted(cli, repo, tmp_path, monkeypatch):
    from al_repository_facts import Snapshot

    go_repository(repo)
    module(repo, "a")
    module(repo, "hidden", "example.invalid/hidden")
    put(repo, ".gitignore", "hidden/\n")
    put(repo, "go.work", "use ./hidden\n")
    pinned(repo)
    start(cli)
    read = Snapshot.read_observed

    def no_hidden(self, path):
        if path.startswith("hidden/"):
            pytest.fail("ignored workspace target body was read")
        return read(self, path)

    monkeypatch.setattr(Snapshot, "read_observed", no_hidden)
    _, manifest = discover(cli, tmp_path, ["a/main.go"])
    c = manifest["service_coverage"]
    assert c["assessment"]["outcome"] == "incomplete"
    assert any(g["code"] == "unsupported-go-workspace" for g in c["assessment"]["gaps"])
    assert "hidden" not in {u["id"] for u in c["semantic_requirements"]["units"]}


def test_duplicate_go_identity_blocks_either_changed_module(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "a")
    module(repo, "b")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["a/main.go"])
    assert any(
        g["code"] == "duplicate-go-module" and g["unit"] == "a"
        for g in manifest["service_coverage"]["assessment"]["gaps"]
    )


def test_new_go_module_changes_are_current_scope_and_invalidate_currency(cli, repo, tmp_path):
    import al_gate_currency as Currency

    go_repository(repo)
    module(repo, "backend/a")
    pinned(repo)
    start(cli)
    state, manifest = discover(cli, tmp_path, ["docs/readme.md"])
    assert manifest["service_coverage"]["assessment"]["outcome"] == "identified"
    assert Currency.check(state)["current"]
    module(repo, "backend/unrelated", "example.invalid/unrelated")
    current = Currency.check(state)
    assert not current["current"]
    assert any("new touched unit" in reason for reason in current["reasons"])


def test_go_dependency_semantics_change_invalidates_currency(cli, repo, tmp_path):
    import al_gate_currency as Currency

    go_repository(repo)
    module(repo, "a")
    put(repo, ".gitlab-ci.yml", "test:\n  script: cd a && go test ./...\n")
    pinned(repo)
    start(cli)
    state, manifest = discover(cli, tmp_path, ["a/main.go"])
    assert manifest["service_coverage"]["assessment"]["outcome"] == "identified"
    assert Currency.check(state)["current"]
    put(repo, "a/go.mod", "module example.invalid/app\nrequire example.invalid/external v1.2.3\n")
    assert not Currency.check(state)["current"]


def test_go_recipe_binds_ancestor_workspace_and_lock(repo):
    from al_go_checks import resolve

    go_repository(repo)
    module(repo, "a")
    put(repo, "go.work", "go 1.22\nuse ./a\n")
    put(repo, "go.work.sum", "")
    result = resolve(repo, ["go", "test", "./..."], lambda p: (repo / p).read_bytes(), cwd="a")
    assert {"a/go.mod", "go.work", "go.work.sum"} <= set(result["dependencies"])


def test_make_include_count_is_bounded(repo):
    from al_ci_yaml import Unsupported
    from al_go_checks import resolve

    go_repository(repo)
    module(repo)
    put(
        repo,
        "Makefile",
        "".join("include c" + str(n) + ".mk\n" for n in range(65)) + "test:\n\tgo test ./...\n",
    )
    for n in range(65):
        put(repo, "c" + str(n) + ".mk", "# literal empty include\n")
    with pytest.raises(Unsupported, match="include count"):
        resolve(repo, ["make", "test"], lambda p: (repo / p).read_bytes())


def test_single_letter_nested_module_owns_its_source(cli, repo, tmp_path):
    go_repository(repo)
    module(repo)
    module(repo, "a", "example.invalid/nested")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["a/main.go"])
    assert manifest["service_coverage"]["assessment"]["selected_services"] == ["a"]
    assert manifest["units"] == ["a"]
    put(repo, "a/schema.json", "{}\n")
    put(repo, "a/main.go", 'package main\nconst schema="schema.json"\nfunc main(){}\n')
    _, artifact = discover(cli, tmp_path, ["a/schema.json"])
    assert artifact["service_coverage"]["assessment"]["selected_services"] == ["a"]


def prepare_go(tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path

    executable = os.environ.get("AL_TEST_GO_EXECUTABLE") or shutil.which("go")
    if not executable:
        pytest.skip("real Go toolchain unavailable; positive admission remains unverified")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    monkeypatch.setenv("PATH", str(Path(executable).parent) + os.pathsep + os.defpath)
    monkeypatch.setenv("GOTOOLCHAIN", "local")
    monkeypatch.setenv("GOPROXY", "off")
    monkeypatch.setenv("GOSUMDB", "off")
    monkeypatch.setenv("CGO_ENABLED", "0")
    monkeypatch.setenv("GOENV", "off")
    monkeypatch.setenv("GOFLAGS", "")
    monkeypatch.setenv("GOWORK", "")
    monkeypatch.setenv("GOROOT", str(Path(executable).parent.parent))
    monkeypatch.delenv("GOOS", raising=False)
    monkeypatch.delenv("GOARCH", raising=False)
    monkeypatch.setenv("GOPATH", str(tmp_path / "go-path"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "go-temp"))
    (tmp_path / "go-temp").mkdir()
    monkeypatch.setenv("GOCACHE", str(tmp_path / "go-cache"))
    monkeypatch.setenv("GOMODCACHE", str(tmp_path / "go-mod-cache"))
    return executable


def test_script_cwd_cannot_falsely_cover_root_module(cli, repo, tmp_path):
    go_repository(repo)
    module(repo)
    module(repo, "a", "example.invalid/nested")
    put(
        repo,
        ".gitlab-ci.yml",
        "test:\n  script:\n    - cd a && go test ./...\n    - go test ./...\n",
    )
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    c = manifest["service_coverage"]
    assert c["assessment"]["outcome"] == "incomplete"
    assert any(
        g["code"] == "missing-verification-check" and g["unit"] == "."
        for g in c["assessment"]["gaps"]
    )


def test_make_recipe_shells_actually_run_root_and_nested_tests(repo, tmp_path, monkeypatch):
    import subprocess

    from al_go_checks import execution

    prepare_go(tmp_path, monkeypatch)
    go_repository(repo)
    module(repo)
    module(repo, "a", "example.invalid/nested")
    put(repo, "main_test.go", 'package main\nimport "testing"\nfunc TestRoot(t *testing.T){}\n')
    put(repo, "a/main_test.go", 'package main\nimport "testing"\nfunc TestNested(t *testing.T){}\n')
    put(
        repo,
        "Makefile",
        "test:\n\tcd a && go test -v -count=1 ./...\n\tgo test -v -count=1 ./...\n",
    )
    result = execution(["make test"], repo, lambda p: (repo / p).read_bytes())
    ran = subprocess.run(
        result["argv"], cwd=repo, check=False, capture_output=True, text=True, timeout=90
    )
    (tmp_path / "actual-go-make.stdout").write_text(ran.stdout)
    (tmp_path / "actual-go-make.stderr").write_text(ran.stderr)
    assert ran.returncode == 0, ran.stderr
    assert "--- PASS: TestNested " in ran.stdout
    assert "--- PASS: TestRoot " in ran.stdout


def test_make_option_is_not_a_literal_target(repo):
    from al_ci_yaml import Unsupported
    from al_go_checks import resolve

    go_repository(repo)
    module(repo)
    put(repo, "Makefile", "default:\n\tgo vet ./...\n-k:\n\tgo test ./...\n")
    with pytest.raises(Unsupported):
        resolve(repo, ["make", "-k"], lambda p: (repo / p).read_bytes())


def test_cd_option_is_not_a_literal_directory(repo):
    from al_ci_yaml import Unsupported
    from al_go_checks import resolve

    go_repository(repo)
    module(repo, "-P")
    with pytest.raises(Unsupported):
        resolve(repo, ["cd", "-P", "&&", "go", "test", "./..."], lambda p: (repo / p).read_bytes())


def test_unreplaced_external_requirement_does_not_create_local_impact(cli, repo, tmp_path):
    go_repository(repo)
    module(repo, "a", "example.invalid/a")
    module(repo, "b", "example.invalid/b")
    put(repo, "a/go.mod", "module example.invalid/a\nrequire example.invalid/b v1.2.3\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["b/main.go"])
    assert manifest["service_coverage"]["assessment"]["selected_services"] == ["b"]


@pytest.mark.parametrize("same_step", [True, False])
def test_github_run_step_shell_boundaries_are_preserved(cli, repo, tmp_path, same_step):
    go_repository(repo)
    module(repo)
    module(repo, "a", "example.invalid/nested")
    runs = (
        "      - run: |\n          cd a && go test ./...\n          go test ./...\n"
        if same_step
        else "      - run: cd a && go test ./...\n      - run: go test ./...\n"
    )
    put(
        repo,
        ".github/workflows/go.yml",
        (
            "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    step"
            "s:\n      - uses: actions/checkout@v4\n"
        )
        + runs,
    )
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    assert manifest["service_coverage"]["assessment"]["outcome"] == (
        "incomplete" if same_step else "identified"
    )
    job = manifest["service_coverage"]["semantic_requirements"]["ci_observations"]["obligations"][0]
    assert [r["go_cwd"] for r in job["execution"]["verification"]] == (
        ["a", "a"] if same_step else ["a", "."]
    )


def test_actual_workspace_import_without_require_is_shared_resolution_context(
    cli, repo, tmp_path, monkeypatch
):
    import subprocess

    executable = prepare_go(tmp_path, monkeypatch)
    go_repository(repo)
    module(repo, "a", "example.invalid/a")
    module(repo, "b", "example.invalid/b")
    put(
        repo,
        "a/main.go",
        (
            'package main\nimport b "example.invalid/b"\nfunc value() int {'
            "return b.Value()}\nfunc main(){}\n"
        ),
    )
    put(repo, "b/main.go", "package b\nfunc Value() int {return 1}\n")
    put(
        repo,
        "a/main_test.go",
        (
            'package main\nimport "testing"\nfunc TestWorkspace(t *testing.'
            'T){if value()!=1 {t.Fatal("workspace")}}\n'
        ),
    )
    put(repo, "go.work", "go 1.22\nuse (\n./a\n./b\n)\n")
    ran = subprocess.run(
        [executable, "test", "-v", "-count=1", "./..."],
        cwd=repo / "a",
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    (tmp_path / "actual-workspace.stdout").write_text(ran.stdout)
    (tmp_path / "actual-workspace.stderr").write_text(ran.stderr)
    assert ran.returncode == 0, ran.stderr
    assert "--- PASS: TestWorkspace " in ran.stdout
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["b/main.go"])
    c = manifest["service_coverage"]
    assert c["assessment"]["selected_services"] == ["a", "b"]
    assert any(
        row["members"] == ["a", "b"]
        for row in c["semantic_requirements"]["dependencies"]["go_workspace_contexts"]
    )


@pytest.mark.parametrize("environment", [False, True])
def test_malformed_go_command_remains_named_ci_obligation(cli, repo, tmp_path, environment):
    import json

    go_repository(repo)
    module(repo)
    extra = "  environment:\n    name: checks\n    action: prepare\n" if environment else ""
    command = 'go test "'
    put(repo, ".gitlab-ci.yml", "test:\n" + extra + "  script: " + json.dumps(command) + "\n")
    pinned(repo)
    start(cli)
    _, manifest = discover(cli, tmp_path, ["main.go"])
    c = manifest["service_coverage"]
    # The malformed job stays a named obligation, left to the pipeline (#17), never certified.
    assert c["assessment"]["outcome"] == "identified"
    assert not any(b.get("conventional_verification") for b in c["assessment"]["required_bindings"])
    assert [[j["job"] for j in d["jobs"]] for d in c["semantic_requirements"]["checks"]["pipeline_verified"]] == [["test"]]
    assert c["semantic_requirements"]["ci_observations"]["obligations"]
    assert not any(g["argv"] for g in manifest["gates"] if g["category"] == "ci")


def supported_go_environment(monkeypatch):
    monkeypatch.setenv("GOENV", "off")
    monkeypatch.delenv("GOFLAGS", raising=False)
    monkeypatch.delenv("GOWORK", raising=False)
    for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def go_environment(monkeypatch):
    supported_go_environment(monkeypatch)
