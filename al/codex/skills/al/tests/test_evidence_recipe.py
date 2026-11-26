"""The named Harness recipe uses real reservations and independently owned scratch."""

import json
from pathlib import Path

import pytest


def bank(repo, *, mixed):
    source = Path(__file__).resolve().parents[4] / "verification-harness.json"
    (repo / "al").mkdir()
    (repo / "al/verification-harness.json").write_bytes(source.read_bytes())
    (repo / "al/installer").mkdir()
    (repo / "al/__init__.py").write_text(
        "raise RuntimeError('target al is not canonical execution code')\n"
    )
    (repo / "harness").mkdir()
    (repo / "harness/component.py").write_text("VALUE = 1\n")
    (repo / "uv.lock").write_text("version = 1\n")
    with (repo / "pyproject.toml").open("a") as stream:
        stream.write('\n[tool.pytest.ini_options]\nmarkers=["browser: browser-only"]\n')
    (repo / "tests").mkdir()
    (repo / "tests/test_pass.py").write_text(
        "import os\nfrom pathlib import Path\ndef test_pass():\n"
        "    Path(os.environ['TMPDIR'],'substantial-fixture').write_bytes(b'x'*1000000)\n"
    )
    (repo / "tests/test_browser.py").write_text(
        "import pytest\n@pytest.mark.browser\ndef test_browser():\n    assert False\n"
    )
    if mixed:
        (repo / "tests/test_skip.py").write_text(
            'import pytest\ndef test_skip():\n    pytest.skip("known skip")\n'
        )
        (repo / "tests/test_fail.py").write_text("def test_fail():\n    assert False\n")


def start(cli):
    result = cli(
        "start",
        "--spec",
        "Verify the fixed Harness recipe preserves exact outcomes "
        "and removes only independently successful owned scratch.",
    )
    assert result[0] == 0, result
    result = cli(
        "evidence",
        "plan",
        "--area",
        "harness-recipe",
        "--scope",
        ".",
        "--recipe",
        "harness-nonbrowser-v1",
    )
    assert result[0] == 0, result


@pytest.fixture(autouse=True)
def private_storage_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "private-harness-home"))


def allocation_state(entry):
    allocation = entry["storage_allocation"]
    registry = (
        Path(allocation["path"]).parents[1] / "records" / (allocation["allocation_id"] + ".json")
    )
    return json.loads(registry.read_bytes())["state"]


def test_public_recipe_keeps_outcomes_and_retains_unproven_producers(cli, repo):
    bank(repo, mixed=True)
    start(cli)
    result = cli("evidence", "run", "--area", "harness-recipe")
    assert result[0] == 1, result
    state = cli.state()
    parent = next(e for e in state["evidence"]["entries"] if e.get("recipe"))
    assert parent["status"] == "FAIL", result
    assert parent["exit"] is None and parent["pid"] is None
    assert (
        parent["counts"]["passed"] == parent["counts"]["failed"] == parent["counts"]["skipped"] == 1
    )
    children = [e for e in state["evidence"]["entries"] if e.get("recipe_assignment")]
    assert len(children) == 4
    for child in children:
        assert Path(child["log"]).is_file()
        assert Path(child["storage_publication"]["reference"]).is_file()
        assert allocation_state(child) == "RETAINED", result
        assert Path(child["storage_allocation"]["path"]).is_dir()
        assert child["storage_settlement"]["descendants_settled"] is None
        assert child["storage_capture_state"] == "protected_capture"
        assert Path(child["storage_outputs"]["junit"]["path"]).is_file()
    first = {e["storage_allocation"]["allocation_id"] for e in children}
    again = cli("evidence", "run", "--area", "harness-recipe", "--fresh")
    assert again[0] == 1, again
    new = [
        e
        for e in cli.state()["evidence"]["entries"]
        if e.get("recipe_assignment") and e["storage_allocation"]["allocation_id"] not in first
    ]
    assert len(new) == 4 and all(allocation_state(e) == "RETAINED" for e in new)


def test_public_successful_recipe_reuses_current_complete_aggregate(cli, repo):
    bank(repo, mixed=False)
    start(cli)
    result = cli("evidence", "run", "--area", "harness-recipe")
    assert result[0] == 0, result
    before = len(cli.state()["evidence"]["entries"])
    result = cli("evidence", "run", "--area", "harness-recipe")
    assert result[0] == 0 and "reused" in result[1], result
    assert len(cli.state()["evidence"]["entries"]) == before


def test_named_recipe_never_converts_arbitrary_commands(cli, repo):
    bank(repo, mixed=False)
    start(cli)
    before = len(cli.state()["evidence"]["entries"])
    result = cli(
        "evidence", "run", "--area", "harness-recipe", "--", "python", "-c", "raise SystemExit(0)"
    )
    assert result[0] != 0
    assert len(cli.state()["evidence"]["entries"]) == before


def test_typed_effect_adapter_rejects_forgery_foreign_parent_other_check_and_writer(
    cli, repo, launcher
):
    import copy
    import os

    import al_writers

    bank(repo, mixed=False)
    start(cli)
    assert cli("evidence", "run", "--area", "harness-recipe")[0] == 0
    actual = cli.state()
    parent = copy.deepcopy(next(e for e in actual["evidence"]["entries"] if e.get("recipe")))
    child = copy.deepcopy(
        next(e for e in actual["evidence"]["entries"] if e.get("recipe_assignment"))
    )
    parent["status"] = "RUNNING"
    parent["owner_pid"] = os.getpid()
    child["owner_pid"] = os.getpid()
    state = copy.deepcopy(actual)
    state["evidence"]["entries"] = [parent]
    session = actual["session_id"]

    def guarded(function):
        result = []
        launcher.run(repo, session, False, lambda: result.append(function()))
        return result[0]

    def current_owner_effects():
        import al_process_observation
        parent['process_observation'] = al_process_observation.allocate(parent)
        child['process_observation'] = al_process_observation.allocate(child)
        return al_writers._check_effects(state, child)

    assert guarded(current_owner_effects) == []
    forged = copy.deepcopy(parent)
    forged["argv"] = ["python", "-c", "open('unowned','w').write('x')"]
    with pytest.raises(SystemExit, match="invalid typed recipe"):
        guarded(lambda: al_writers._check_effects(state, forged))
    foreign = copy.deepcopy(child)
    foreign["recipe_assignment"]["parent"]["reservation"] = "foreign-reservation"
    with pytest.raises(SystemExit, match="exact live coordinator"):
        guarded(lambda: al_writers._check_effects(state, foreign))
    other = copy.deepcopy(child)
    for key in (
        "recipe_assignment",
        "recipe_result",
        "recipe_process",
        "recipe_log_copy",
        "storage",
    ):
        other.pop(key, None)
    other.update(
        id="E999", status="RUNNING", argv=["python", "-c", "open('persistent','w').write('x')"]
    )
    concurrent = copy.deepcopy(state)
    concurrent["evidence"]["entries"].append(other)
    with pytest.raises(SystemExit, match="shared-state check overlap E999"):
        guarded(lambda: al_writers.admit_check(concurrent, child))
    overlap = copy.deepcopy(state)
    overlap["phase"] = "IMPLEMENTATION"
    overlap["writers"]["other-writer"] = {
        "id": "other-writer",
        "state": "ACTIVE",
        "files": ["svc"],
        "commands": [],
    }
    with pytest.raises(SystemExit, match="overlaps unresolved writer"):
        guarded(lambda: al_writers.admit_check(overlap, child))
    # These are deliberately forged in-memory lab inputs, never committed as actual run state.
    assert cli.state() == actual


def test_public_recipe_over_actual_private_harness_channel(
    cli, launcher, repo, tmp_path, monkeypatch
):
    """Exercise private recipe storage/transport through a protected AL child.

    The process-context IDs below are fixture metadata for this channel protocol test, not
    evidence of native Harness enrollment or a genuine observed principal.
    """
    import os
    import sys
    import tempfile

    from al.installer.owned_process import OwnedProcess
    from harness.execution_check_channel import CheckChannel
    from harness.execution_check_ownership import CheckOwnership
    from harness.execution_contract import digest
    from harness.storage import ExecutionStorage
    from harness.task_tooling import TaskTooling
    from harness.reliability.process_context import CURRENT, ProcessContext
    from tests.native_model_fixture import operation_sandbox_unavailable

    if operation_sandbox_unavailable():
        pytest.skip("actual operation sandbox unavailable on this platform")
    bank(repo, mixed=True)
    source_root = Path(__file__).resolve().parents[5]
    # This development interpreter was installed from these exact locked inputs;
    # reuse it as a source-bound prepared-environment fixture, not a new setup claim.
    for name in ("pyproject.toml", "uv.lock"):
        (repo / name).write_bytes((source_root / name).read_bytes())
    start(cli)
    initial = cli.state()
    operations = tmp_path / "host/handoff/operations"
    operations.mkdir(parents=True)
    temporary = tmp_path / "host/temporary"
    temporary.mkdir()
    tooling = TaskTooling(tmp_path / "host/tooling", repo, {"epoch": "fixture-epoch"})
    tooling.ready(
        ".",
        {
            "kind": "python",
            "inputs": {n: digest((repo / n).read_bytes()) for n in ("pyproject.toml", "uv.lock")},
            "environment": str(Path(sys.executable).parent.parent),
            "sources": [str(repo)],
            "tools": digest(tooling.catalog()["tools"]),
        },
    )
    import al_store

    publication_root = al_store.RUNTIME_DIR / initial["session_id"] / "logs/storage"
    context = {
        "handoff": str(operations.parent / "handoff.json"),
        "repository": str(repo),
        "temporary": str(temporary),
        "al_session": str(al_store.RUNTIME_DIR),
        "protected": [str(operations.parent), str(al_store.RUNTIME_DIR)],
        "hidden": [],
        "tooling_directory": str(tooling.directory),
        "manager_root": str(tmp_path / "broker-storage"),
        "publication_root": str(publication_root),
        "session": initial["session_id"],
        "execution": "actual-fixture-execution",
        "epoch": "fixture-epoch",
        "authority_generation": "1",
        "principal": "actual-fixture-supervisor",
    }
    context_path = operations / "context.json"
    context_path.write_text(json.dumps(context))

    def validate(binding, declaration, *, final, allocation=False):
        current = cli.state()
        assert current["run_id"] == initial["run_id"] and current["status"] == "RUNNING"
        entry = next(e for e in current["evidence"]["entries"] if e["id"] == binding["id"])
        assert all(entry[k] == v for k, v in binding.items())
        if not allocation:
            assert entry.get("storage") == declaration
        if final is False:
            assert entry["status"] == "RUNNING"
        elif final is True:
            assert entry["status"] != "RUNNING"
        else:
            assert entry["status"] == "RUNNING"
        return entry

    ownership = CheckOwnership()

    def validate_allocation(binding, *, coordinator):
        entry = validate(
            binding,
            None,
            final=None if coordinator is None else False,
            allocation=True,
        )
        if coordinator is not None:
            assert type(coordinator) is bool
            assert bool(entry.get("recipe")) == coordinator
        return entry

    storage = ExecutionStorage(context, validate)
    # Keep the ProcessContext scoped to channel construction. Its identity fields are controlled
    # fixture values and the observer is None, so no native principal or database event is claimed.
    process_context = ProcessContext(
        database=Path(context["manager_root"]) / "unused-fixture-observer.sqlite",
        execution_id=context["execution"],
        epoch_id=context["epoch"],
        authority_generation=int(context["authority_generation"]),
        observation_id="fixture-only-not-native-observation",
        principal_id=context["principal"],
    )
    context_token = CURRENT.set(process_context)
    try:
        channel = CheckChannel(
            context_path,
            lambda: None,
            storage=storage,
            ownership=ownership,
            validate_allocation=validate_allocation,
        )
    finally:
        CURRENT.reset(context_token)
    monkeypatch.setenv("HARNESS_CHECK_CONTEXT", str(context_path))
    monkeypatch.setenv("HARNESS_CHECK_FD", str(channel.descriptor))
    owned = OwnedProcess(tmp_path / "owned-recipe-runner", "private-recipe-channel-test")
    output = tempfile.TemporaryFile()
    cleanup = None
    try:
        argv = launcher.child_argv(
            repo,
            initial["session_id"],
            False,
            ["evidence", "run", "--area", "harness-recipe"],
        )
        process = owned.spawn(
            argv,
            cwd=repo,
            env=os.environ.copy(),
            output=output,
            pass_fds=(channel.descriptor,),
        )
        channel.bind_owner(process, observer=None)
        assert channel.owner_ready.wait(timeout=5), "actual AL child owner binding was not ready"
        channel.spawned()
        try:
            process.wait(timeout=240)
        finally:
            cleanup = owned.cleanup()
        output.seek(0)
        stdout = output.read().decode("utf-8", errors="replace")
        result = (process.returncode, stdout, "")
        assert result[0] == 1, result
        state = cli.state()
        parent = next(e for e in state["evidence"]["entries"] if e.get("recipe"))
        assert parent["status"] == "FAIL" and parent["counts"]["passed"] == 1, result
        children = [e for e in state["evidence"]["entries"] if e.get("recipe_assignment")]
        assert len(children) == len(channel.records) == 4
        for child in children:
            actual = next(
                v
                for v in storage.records.values()
                if v["recipe_input"]["assignment_digest"]
                == child["recipe_assignment"]["assignment_digest"]
            )
            assert actual.get("kernel_completion", {}).get("complete") is True
            assert actual.get("cleanup", {}).get("state") == "RETAINED", result
            assert actual["cleanup"]["reason"] == "descendant_settlement_unproven"
            assert Path(child["storage_allocation"]["path"]).is_dir()
            assert child["storage_settlement"]["descendants_settled"] is None
            assert Path(child["storage_outputs"]["junit"]["path"]).is_file()
    finally:
        try:
            if cleanup is None:
                cleanup = owned.cleanup()
        finally:
            try:
                channel.close()
            finally:
                output.close()
        assert cleanup["complete"], cleanup
