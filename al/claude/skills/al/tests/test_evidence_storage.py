"""Historical absence, explicit routes and logical declarations preserve check meaning."""

import copy
import json
from pathlib import Path

import pytest
from evidence_helpers import PY


@pytest.fixture(autouse=True)
def private_storage_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOME", str(tmp_path / "private-storage-home"))


def test_historical_absent_plan_stays_unmanaged_and_reusable(cli, repo, launcher, tmp_path):
    import al_store

    assert (
        cli(
            "start",
            "--spec",
            "Verify historical evidence plan absence retains "
            "its original unmanaged command semantics.",
        )[0]
        == 0
    )
    state = cli.state()
    command = [PY, "-c", "print('1 passed')"]
    legacy = {
        "area": "legacy",
        "kind": "test",
        "tier": "focused",
        "argv": command,
        "cwd": ".",
        "scope": ["svc"],
        "sources": [],
        "env": [],
        "preflight": None,
        "needs": [],
    }

    def historical_fixture():
        observed = al_store.load(state["session_id"])
        observed["evidence"]["plan"]["legacy"] = copy.deepcopy(legacy)
        al_store.save(state["session_id"], observed)

    # Historical fixture construction; no phase, completion or claimed pass is manufactured.
    launcher.run(repo, state["session_id"], False, historical_fixture)
    result = cli(
        "evidence",
        "run",
        "--area",
        "legacy",
        "--kind",
        "test",
        "--tier",
        "focused",
        "--scope",
        "svc",
        "--",
        *command,
    )
    assert result[0] == 0, result
    entry = cli.state()["evidence"]["entries"][-1]
    assert "storage" not in entry and "storage_allocation" not in entry
    assert not (tmp_path / "private-storage-home/managed-storage").exists()
    result = cli("evidence", "run", "--area", "legacy")
    assert result[0] == 0 and "reused" in result[1], result
    assert cli.state()["evidence"]["plan"]["legacy"] == legacy


def test_default_respects_explicit_env_routes_without_adopting_them(cli, tmp_path):
    assert (
        cli(
            "start",
            "--spec",
            "Verify explicit temporary output routes retain precedence "
            "over default managed scratch.",
        )[0]
        == 0
    )
    external = tmp_path / "explicit-temporary"
    external.mkdir()
    command = [
        "env",
        "TMPDIR=" + str(external),
        PY,
        "-c",
        "import os;from pathlib import Path;"
        "Path(os.environ['TMPDIR'],'explicit').write_text('kept');print('1 passed')",
    ]
    result = cli("evidence", "run", "--area", "explicit", "--scope", "svc", "--", *command)
    assert result[0] == 0, result
    entry = cli.state()["evidence"]["entries"][-1]
    assert entry["argv"] == command
    assert Path(entry["storage_allocation"]["path"]).is_dir()
    assert entry["storage_settlement"]["descendants_settled"] is None
    assert (external / "explicit").read_text() == "kept"


def test_explicit_null_storage_refuses_before_reservation(cli, tmp_path):
    assert (
        cli(
            "start",
            "--spec",
            "Verify malformed explicit storage declarations cannot allocate a ledger reservation.",
        )[0]
        == 0
    )
    path = tmp_path / "null-storage.json"
    path.write_text("null")
    before = cli.state()
    result = cli(
        "evidence",
        "run",
        "--area",
        "null",
        "--scope",
        "svc",
        "--storage-file",
        str(path),
        "--",
        PY,
        "-c",
        "print('1 passed')",
    )
    assert result[0] != 0
    after = cli.state()
    assert after["evidence"] == before["evidence"]


def test_changing_declared_outputs_changes_command_identity(cli, tmp_path):
    assert (
        cli(
            "start",
            "--spec",
            "Verify logical storage contract changes cannot reuse a previous evidence receipt.",
        )[0]
        == 0
    )
    value = {
        "schema_version": 1,
        "scratch": [{"name": "operation", "class": "operation-scratch"}],
        "outputs": [],
        "operands": [],
    }
    path = tmp_path / "storage.json"
    path.write_text(json.dumps(value))
    assert (
        cli(
            "evidence",
            "plan",
            "--area",
            "declared",
            "--scope",
            "svc",
            "--storage-file",
            str(path),
            "--",
            PY,
            "-c",
            "print('1 passed')",
        )[0]
        == 0
    )
    assert cli("evidence", "run", "--area", "declared")[0] == 0
    value["outputs"] = [
        {"name": "optional", "filename": "optional.xml", "required": False, "max_bytes": 1024}
    ]
    path.write_text(json.dumps(value))
    result = cli(
        "evidence",
        "run",
        "--area",
        "declared",
        "--scope",
        "svc",
        "--storage-file",
        str(path),
        "--",
        PY,
        "-c",
        "print('1 passed')",
    )
    assert result[0] != 0
    assert len(cli.state()["evidence"]["entries"]) == 1
