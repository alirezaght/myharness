"""CI-derived check recipes retain their targets, dependencies, cwd and setup."""
import json
import shutil
import sys
from pathlib import Path

import al_ledger as L
import pytest
from evidence_helpers import entries
from test_al_usability import pending
from test_service_coverage import put,start,discover
from test_go_repository_coverage import go_repository,module,pinned,prepare_go,supported_go_environment

SERVICE = 'services/user-listing-service'
MYPYPATH = 'src:../../packages/pyshared/src:../../packages/contracts/python'
RECIPE_SCOPE = [
    'ci/scripts/fast-check-python.sh',
    'ci/scripts/fast-check-lib.sh',
    f'{SERVICE}/pyproject.toml',
    f'{SERVICE}/uv.lock',
    f'{SERVICE}/src',
    'packages/tooling/ruff/ruff.toml',
    'packages/tooling/mypy/mypy.ini',
    'packages/pyshared/pyproject.toml',
    'packages/pyshared/src',
    'packages/contracts/python/pyproject.toml',
    'packages/contracts/python/src',
]
PROBE = (
    'import json, os, sys; '
    'json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), '
    '"MYPYPATH": os.environ.get("MYPYPATH"), '
    '"UV_NO_SYNC": os.environ.get("UV_NO_SYNC")}, '
    'open(os.environ["RECIPE_PROBE_OUT"], "w"))'
)


@pytest.fixture
def repo(tmp_path, seed_repo):
    root = tmp_path / 'repository with spaces'
    shutil.copytree(seed_repo, root, symlinks=True)
    inputs = {
        'ci/scripts/fast-check-python.sh': '# source-backed fixture\n',
        'ci/scripts/fast-check-lib.sh': '# source-backed wrapper fixture\n',
        f'{SERVICE}/pyproject.toml': '[project]\nname="fixture-service"\n',
        f'{SERVICE}/uv.lock': 'version = 1\n',
        f'{SERVICE}/src/app.py': 'answer = 42\n',
        'packages/tooling/ruff/ruff.toml': 'line-length = 100\n',
        'packages/tooling/mypy/mypy.ini': '[mypy]\n',
        'packages/pyshared/pyproject.toml': '[project]\nname="pyshared"\n',
        'packages/pyshared/src/pyshared.py': 'shared = True\n',
        'packages/contracts/python/pyproject.toml': '[project]\nname="contracts"\n',
        'packages/contracts/python/src/contracts.py': 'contract = True\n',
    }
    for name, content in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def _plan(cli, area, command, *, kind='lint', scope=RECIPE_SCOPE, cwd=SERVICE, env=()):
    args = ['evidence', 'plan', '--area', area, '--kind', kind, '--tier', 'focused',
            '--cwd', cwd, '--scope', ','.join(scope)]
    for name in env:
        args.extend(['--env', name])
    return cli(*args, '--', *command)


def _probe_command(output, *tool_args):
    return ['env', 'UV_NO_SYNC=1', f'MYPYPATH={MYPYPATH}',
            f'RECIPE_PROBE_OUT={output}', sys.executable, '-c', PROBE, *tool_args]


def test_source_recipe_handoff_runs_src_only_with_exact_cwd_and_environment(
        cli, tmp_path, repo, monkeypatch, state_file):
    pending(cli, tmp_path, repo)
    result = cli('writer', 'start', '--id', 'recipe-writer', '--role', 'al-engineer',
                 '--files', f'{SERVICE}/src/app.py', '--degraded',
                 '--reason', 'Exercise the source-bound evidence handoff in an owned fixture.')
    assert result[0] == 0, result

    output = tmp_path / 'probe result.json'
    command = _probe_command(output, 'check', 'src', '--config',
                             '../../packages/tooling/ruff/ruff.toml')
    area = 'user-listing-ci-ruff'
    result = _plan(cli, area, command)
    assert result[0] == 0, result
    plan = cli.state()['evidence']['plan'][area]
    assert plan['argv'] == command
    assert plan['cwd'] == SERVICE
    assert plan['scope'] == RECIPE_SCOPE
    assert 'src' in plan['argv']
    assert not any(target in plan['argv'] for target in ('tests', 'alembic'))

    monkeypatch.setenv('MYPYPATH', 'ambient-wrong-value')
    monkeypatch.setenv('UV_NO_SYNC', '0')
    body = tmp_path / 'source recipe handoff.md'
    body.write_text(
        'CI source: ci/scripts/fast-check-python.sh and ci/scripts/fast-check-lib.sh.\n'
        f'Check cwd: {SERVICE}.\n'
        'Required prepared setup: uv sync --extra dev --frozen.\n'
        f'Child environment: UV_NO_SYNC=1 and MYPYPATH={MYPYPATH}.\n'
        'This component area targets src only; scope separately covers CI, config, lock, '
        'service source, and imported shared/contracts inputs.\n'
    )
    handoff = cli('writer', 'handoff', '--id', 'recipe-writer', '--body-file', str(body))
    assert handoff[0] == 0, handoff
    assert body.read_text() in handoff[1]
    assert f'Area {area}: tier=focused' in handoff[1]
    assert f'evidence run --writer recipe-writer --area {area}' in handoff[1]

    result = cli('evidence', 'run', '--writer', 'recipe-writer', '--area', area)
    assert result[0] == 0, result
    observed = json.loads(output.read_text())
    assert observed == {
        'argv': ['check', 'src', '--config', '../../packages/tooling/ruff/ruff.toml'],
        'cwd': str(repo / SERVICE),
        'MYPYPATH': MYPYPATH,
        'UV_NO_SYNC': '1',
    }
    entry = entries(state_file)[-1]
    assert entry['status'] == 'PASS' and entry['argv'] == command
    assert set(entry['scope']) == set(RECIPE_SCOPE)


def test_env_name_only_fingerprints_but_does_not_provision_missing_value(
        cli, repo, monkeypatch, state_file):
    monkeypatch.delenv('MYPYPATH', raising=False)
    result = cli('start', '--spec', 'Check that evidence env declarations do not assign child values.')
    assert result[0] == 0, result
    scope = [f'{SERVICE}/src']
    command = [sys.executable, '-c',
               'import os; assert "MYPYPATH" not in os.environ; print("unprovided")']
    result = _plan(cli, 'names-only-env', command, kind='check', scope=scope,
                   cwd=SERVICE, env=('MYPYPATH',))
    assert result[0] == 0, result
    result = cli('evidence', 'run', '--area', 'names-only-env')
    assert result[0] == 0, result
    entry = entries(state_file)[-1]
    assert entry['env'] == ['MYPYPATH'] and entry['status'] == 'PASS'
    assert 'MYPYPATH' not in entry['argv']


def test_ci_and_config_scope_changes_stale_pass_and_changed_argv_runs_again(
        cli, repo, state_file):
    result = cli('start', '--spec', 'Bind CI recipe results to declared sources and exact argv.')
    assert result[0] == 0, result
    area = 'user-listing-ci-ruff'
    old_command = _probe_command(repo / 'old.json', 'check', 'src', '--config',
                                 '../../packages/tooling/ruff/ruff.toml')
    assert _plan(cli, area, old_command)[0] == 0
    assert cli('evidence', 'run', '--area', area)[0] == 0
    original_entry = entries(state_file)[-1]
    original_log = Path(original_entry['log']).read_bytes()

    (repo / 'ci/scripts/fast-check-python.sh').write_text('# changed CI recipe source\n')
    code, out, err = cli('evidence', 'require', '--areas', area)
    assert code == 13 and 'STALE' in out + err
    assert original_entry['status'] == 'PASS'
    assert Path(original_entry['log']).read_bytes() == original_log

    assert cli('evidence', 'run', '--area', area)[0] == 0
    second_entry = entries(state_file)[-1]
    assert second_entry['id'] != original_entry['id']
    (repo / 'packages/tooling/ruff/ruff.toml').write_text('line-length = 99\n')
    code, out, err = cli('evidence', 'require', '--areas', area)
    assert code == 13 and 'STALE' in out + err

    new_command = _probe_command(repo / 'new.json', 'check', 'src', '--config',
                                 '../../packages/tooling/ruff/ruff.toml', '--output-format', 'concise')
    assert _plan(cli, area, new_command)[0] == 0
    result = cli('evidence', 'run', '--area', area)
    assert result[0] == 0 and 'reused' not in result[1]
    all_entries = entries(state_file)
    assert len(all_entries) == 3
    assert all_entries[-1]['argv'] == new_command
    assert json.loads((repo / 'new.json').read_text())['argv'] == [
        'check', 'src', '--config', '../../packages/tooling/ruff/ruff.toml',
        '--output-format', 'concise']
    assert L.entry_verdict(cli.state(), original_entry)[0] != 'VALID'


def test_mistaken_wide_fail_is_retired_without_rewriting_its_receipt(
        cli, repo, state_file):
    result = cli('start', '--spec', 'Retain the wide failed check while recording its scoped replacement.')
    assert result[0] == 0, result
    scope = [f'{SERVICE}/src', 'packages/tooling/ruff/ruff.toml']
    wide = [sys.executable, '-c', 'print("1 failed"); raise SystemExit(1)']
    assert _plan(cli, 'wide-lint', wide, kind='lint', scope=scope)[0] == 0
    assert cli('evidence', 'run', '--area', 'wide-lint')[0] != 0
    failed = entries(state_file)[-1]
    assert failed['status'] == 'FAIL'
    failed_log = Path(failed['log']).read_bytes()

    component = [sys.executable, '-c', 'print("component source check passed")']
    assert _plan(cli, 'component-lint', component, kind='lint', scope=scope)[0] == 0
    assert cli('evidence', 'run', '--area', 'component-lint')[0] == 0
    replacement = entries(state_file)[-1]
    assert replacement['status'] == 'PASS'
    result = cli('evidence', 'retire', '--area', 'wide-lint',
                 '--reason', 'The original command targeted a wider set than the source-backed component recipe.',
                 '--replaced-by', replacement['id'])
    assert result[0] == 0, result
    current = cli.state()
    retained = next(item for item in current['evidence']['entries'] if item['id'] == failed['id'])
    assert retained['status'] == 'FAIL'
    assert Path(retained['log']).read_bytes() == failed_log
    assert current['evidence']['retired'][-1]['replaced_by'] == [replacement['id']]
    assert 'wide-lint' not in current['evidence']['plan']


def assert_left_to_pipeline(manifest, *, strict=True):
    """A CI job AL cannot qualify locally never certifies the service (#17): it is recorded
    under checks.pipeline_verified as an unverified obligation; a job AL can map but not
    qualify may still be a coverage gap (strict=False accepts either outcome)."""
    coverage = manifest["service_coverage"]
    assessment = coverage["assessment"]
    assert not any(b.get("conventional_verification") for b in assessment["required_bindings"])
    delegated = coverage["semantic_requirements"]["checks"].get("pipeline_verified", [])
    if delegated or strict:
        assert assessment["outcome"] == "identified"
        assert [j["job"] for d in delegated for j in d["jobs"]] == ["test"]
    else:
        assert assessment["outcome"] == "incomplete"


class TestGoRecipes:
    @pytest.fixture
    def repo(self, tmp_path, seed_repo):
        root = tmp_path / "go repository with spaces"
        shutil.copytree(seed_repo, root, symlinks=True)
        return root

    @pytest.mark.parametrize("operation", ["vet", "build"])
    @pytest.mark.parametrize("required_test", [False, True])
    def test_sourced_verification_purposes_do_not_invent_or_erase_tests(
        self, cli, repo, tmp_path, operation, required_test, monkeypatch
    ):
        supported_go_environment(monkeypatch)
        go_repository(repo)
        module(repo)
        definition = "verify:\n  script: go " + operation + " ./...\n"
        if required_test:
            definition += "tests:\n  script: go test ./...\n"
        put(repo, ".gitlab-ci.yml", definition)
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        c = manifest["service_coverage"]
        assert c["assessment"]["outcome"] == "identified", c["assessment"]["gaps"]
        jobs = {r["job"]: r for r in c["semantic_requirements"]["ci_observations"]["obligations"]}
        assert (
            jobs["verify"]["execution"]["verification_chain"]["requirements"][0]["purpose"]
            == operation
        )
        assert jobs["verify"]["execution"]["verification"][0]["go_operation"] == operation
        gates = {g["source"]["job"]: g for g in manifest["gates"] if g["category"] == "ci"}
        assert gates["verify"]["required"] and gates["verify"]["argv"]
        assert any(
            "verification-root" in b["obligation_ids"] for b in c["assessment"]["required_bindings"]
        )
        if required_test:
            assert gates["tests"]["required"] and gates["tests"]["argv"]
            assert (
                jobs["tests"]["execution"]["verification_chain"]["requirements"][0]["purpose"]
                == "test"
            )
            assert len(gates) == 2
        else:
            assert set(gates) == {"verify"}
            assert all(
                r["purpose"] != "test"
                for r in jobs["verify"]["execution"]["verification_chain"]["requirements"]
            )

    def test_vet_does_not_clear_unverified_required_test_or_review_guard(
        self, cli, repo, tmp_path, monkeypatch, state_file
    ):
        import json

        from hook_regression_helpers import invoke
        from launcher_context import admit_planning

        cli = cli.raw
        prepare_go(tmp_path, monkeypatch)
        go_repository(repo)
        module(repo)
        put(
            repo,
            ".gitlab-ci.yml",
            "verify:\n  script: go vet ./...\ntests:\n  script: go test -run=Only ./...\n",
        )
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        test_gate = next(
            g for g in manifest["gates"] if g["category"] == "ci" and g["source"]["job"] == "tests"
        )
        assert (
            test_gate["required"]
            and test_gate["applicability"] == "UNRESOLVED"
            and not test_gate["argv"]
        )
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(manifest))
        invoke(cli, "gates", "plan", "--file", str(path))
        cli("gates", "run", "--stage", "base")
        admit_planning(cli)
        invoke(cli, "phase", "IMPLEMENTATION")
        invoke(
            cli,
            "writer",
            "start",
            "--id",
            "verify-owner",
            "--role",
            "al-engineer",
            "--files",
            "main.go",
        )
        assert json.loads(cli("show")[1])["implementation_readiness"]["obligations"]
        invoke(cli, "writer", "finish", "--id", "verify-owner", "--outcome", "success")
        invoke(cli, "phase", "VERIFICATION")
        before = state_file().read_bytes()
        invoke(cli, "review-start", ok=False)
        assert state_file().read_bytes() == before

    def test_go_review_oneshell_is_not_an_ordinary_target(self, cli, repo, tmp_path, monkeypatch):
        supported_go_environment(monkeypatch)
        go_repository(repo)
        module(repo)
        module(repo, "a", "example.invalid/nested")
        put(repo, "Makefile", ".ONESHELL:\ntest:\n\tcd a && go test ./...\n\tgo test ./...\n")
        put(repo, ".gitlab-ci.yml", "test:\n  script: make test\n")
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        assert_left_to_pipeline(manifest)
        assert not any(g["argv"] for g in manifest["gates"] if g["category"] == "ci")

    def test_go_review_higher_priority_makefile_cannot_be_ignored(
        self, cli, repo, tmp_path, monkeypatch
    ):
        supported_go_environment(monkeypatch)
        go_repository(repo)
        module(repo)
        put(repo, "Makefile", "test:\n\tgo test ./...\n")
        put(repo, "GNUmakefile", "test:\n\tgo test -run=Only ./...\n")
        put(repo, ".gitlab-ci.yml", "test:\n  script: make test\n")
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        assert_left_to_pipeline(manifest)
        assert not any(g["argv"] for g in manifest["gates"] if g["category"] == "ci")

    @pytest.mark.parametrize("mode", ["ambient_flags", "persisted_explicit", "persisted_default"])
    def test_go_review_hidden_selectors_are_real_but_never_qualified(
        self, cli, repo, tmp_path, monkeypatch, mode
    ):
        import os
        import subprocess

        executable = prepare_go(tmp_path, monkeypatch)
        go_repository(repo)
        module(repo)
        put(
            repo,
            "main_test.go",
            'package main\nimport "testing"\nfunc TestEnvironment(t *testing.T){}\n',
        )
        put(repo, ".gitlab-ci.yml", "test:\n  script: go test -v ./...\n")
        if mode == "ambient_flags":
            monkeypatch.setenv("GOFLAGS", "-run=^$")
        else:
            monkeypatch.delenv("GOFLAGS", raising=False)
            if mode == "persisted_explicit":
                config = tmp_path / "go-environment"
                monkeypatch.setenv("GOENV", str(config))
            else:
                home = tmp_path / "private-home"
                home.mkdir()
                xdg = tmp_path / "private-config"
                xdg.mkdir()
                monkeypatch.setenv("HOME", str(home))
                monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
                monkeypatch.delenv("GOENV", raising=False)
                probe = subprocess.run(
                    [executable, "env", "GOENV"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                config = Path(probe.stdout.strip())
                assert config.is_relative_to(home) or config.is_relative_to(xdg)
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text("GOFLAGS=-run=^$\n")
        ran = subprocess.run(
            [executable, "test", "-v", "-count=1", "./..."],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        (tmp_path / "actual-selector.stdout").write_text(ran.stdout)
        (tmp_path / "actual-selector.stderr").write_text(ran.stderr)
        assert ran.returncode == 0, ran.stderr
        assert "--- PASS: TestEnvironment " not in ran.stdout
        original = {name: os.environ.get(name) for name in ("GOENV", "GOFLAGS")}
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        assert {name: os.environ.get(name) for name in original} == original
        assert_left_to_pipeline(manifest, strict=False)
        assert not any(g["argv"] for g in manifest["gates"] if g["category"] == "ci")

    def test_go_review_make_environment_inputs_are_not_hidden_includes(
        self, cli, repo, tmp_path, monkeypatch
    ):
        supported_go_environment(monkeypatch)
        go_repository(repo)
        module(repo)
        put(repo, "Makefile", "test:\n\tgo test ./...\n")
        put(repo, "extra.mk", ".ONESHELL:\n")
        put(repo, ".gitlab-ci.yml", "test:\n  script: make test\n")
        pinned(repo)
        start(cli)
        monkeypatch.setenv("MAKEFILES", "extra.mk")
        _, manifest = discover(cli, tmp_path, ["main.go"])
        assert_left_to_pipeline(manifest)
        assert not any(g["argv"] for g in manifest["gates"] if g["category"] == "ci")

    def test_go_review_default_makefile_absence_is_captured(self, cli, repo, tmp_path, monkeypatch):
        import al_gate_inventory as Inventory

        supported_go_environment(monkeypatch)
        go_repository(repo)
        module(repo)
        put(repo, "Makefile", "test:\n\tgo test ./...\n")
        put(repo, ".gitlab-ci.yml", "test:\n  script: make test\n")
        pinned(repo)
        start(cli)
        state, _ = discover(cli, tmp_path, ["main.go"])
        inventory = Inventory.inventory(state, ["main.go"])
        assert "GNUmakefile" in inventory["repository_facts"]["enumeration"]["absent_paths"]

    def test_go_review_missing_make_prerequisite_is_named_debt(
        self, cli, repo, tmp_path, monkeypatch
    ):
        supported_go_environment(monkeypatch)
        go_repository(repo)
        module(repo)
        put(repo, "Makefile", "test: missing.input\n\tgo test ./...\n")
        put(repo, ".gitlab-ci.yml", "test:\n  script: make test\n")
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        assert_left_to_pipeline(manifest)
        assert not any(g["argv"] for g in manifest["gates"] if g["category"] == "ci")

    @pytest.mark.parametrize(
        "shape",
        [
            "script",
            "script_false",
            "script_null",
            "script_collection",
            "run",
            "run_false",
            "run_null",
            "run_collection",
        ],
    )
    def test_go_review_malformed_container_commands_stay_unverified(
        self, cli, repo, tmp_path, monkeypatch, shape
    ):
        supported_go_environment(monkeypatch)
        go_repository(repo)
        module(repo)
        if shape.startswith("script"):
            value = {
                "script": "7",
                "script_false": "false",
                "script_null": "null",
                "script_collection": "[go, test]",
            }[shape]
            put(repo, ".gitlab-ci.yml", "test:\n  image: golang:1.22\n  script: [" + value + "]\n")
        else:
            put(
                repo,
                ".github/workflows/go.yml",
                "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    container: golang:1.22\n    steps:\n      - uses: actions/checkout@v4\n      - run: "
                + {
                    "run": "7",
                    "run_false": "false",
                    "run_null": "null",
                    "run_collection": "[go, test]",
                }[shape]
                + "\n",
            )
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        assert_left_to_pipeline(manifest)
        ci = [g for g in manifest["gates"] if g["category"] == "ci"]
        assert ci and all(g["required"] and not g["argv"] for g in ci)

    def test_go_review_make_include_quotes_are_not_shell_unquoted(
        self, cli, repo, tmp_path, monkeypatch
    ):
        import subprocess

        prepare_go(tmp_path, monkeypatch)
        go_repository(repo)
        module(repo)
        put(repo, "checks.mk", "unit:\n\tgo test ./...\n")
        put(repo, "Makefile", 'include "checks.mk"\ntest: unit\n')
        actual = subprocess.run(
            ["make", "test"], cwd=repo, check=False, capture_output=True, text=True, timeout=30
        )
        (tmp_path / "actual-make-include.stdout").write_text(actual.stdout)
        (tmp_path / "actual-make-include.stderr").write_text(actual.stderr)
        assert actual.returncode != 0 and "checks.mk" in actual.stderr
        put(repo, ".gitlab-ci.yml", "test:\n  script: make test\n")
        pinned(repo)
        start(cli)
        _, manifest = discover(cli, tmp_path, ["main.go"])
        assert_left_to_pipeline(manifest, strict=False)

    def test_go_review_environment_change_cannot_run_previously_qualified_gate(
        self, cli, repo, tmp_path, monkeypatch, state_file
    ):
        import al_gate_currency as Currency
        from hook_regression_helpers import invoke

        cli = cli.raw
        prepare_go(tmp_path, monkeypatch)
        go_repository(repo)
        module(repo)
        put(
            repo,
            "main_test.go",
            'package main\nimport "testing"\nfunc TestEnvironment(t *testing.T){}\n',
        )
        put(repo, ".gitlab-ci.yml", "test:\n  script: go test -v ./...\n")
        pinned(repo)
        start(cli)
        state, manifest = discover(cli, tmp_path, ["main.go"])
        assert manifest["service_coverage"]["assessment"]["outcome"] == "identified"
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(manifest))
        invoke(cli, "gates", "plan", "--file", str(path))
        assert Currency.check(state)["current"]
        monkeypatch.setenv("GOFLAGS", "-run=^$")
        assert not Currency.check(state)["current"]
        invoke(cli, "gates", "run", "--stage", "base", ok=False)
        executed = json.loads(cli("show")[1])["evidence"]["entries"]
        assert executed[-1]["status"] == "INFRA" and executed[-1]["exit"] == 126
        blocked = executed[-1]
        assert "=== RUN   TestEnvironment" not in Path(blocked["log"]).read_text()
        assert not Currency.check(state)["current"]
        monkeypatch.setenv("GOFLAGS", "")
        assert Currency.check(state)["current"]
        invoke(cli, "gates", "run", "--stage", "base")
        later = json.loads(cli("show")[1])["evidence"]["entries"]
        assert later[-1]["status"] == "PASS"
        assert "--- PASS: TestEnvironment " in Path(later[-1]["log"]).read_text()
        assert any(row["id"] == blocked["id"] and row["status"] == "INFRA" for row in later)
