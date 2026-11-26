"""Fixed sealed identities cover every inspected executable, including new imports."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.policy import control_qualification
from harness.policy import control_sources_claude as claude
from harness.policy import control_sources_codex as codex
from harness.policy import control_sources_installer as installer
from harness.policy.contracts import PolicyError
from scripts import control_source_inventory as inventory

ROOT = Path(__file__).resolve().parents[1] / "al"


@pytest.mark.parametrize("host,asset", [("codex", codex), ("claude", claude)])
def test_fixed_inventory_matches_complete_current_executable_closure(host, asset):
    report = inventory.check(ROOT.parent)
    assert report["ok"], report
    assert f"{host}/skills/al/scripts/al_repo_hatch.py" in asset.EXPECTED
    for source in (installer, asset):
        assert source.NAMES.splitlines() == sorted(source.EXPECTED)
        assert len(source.NAMES.splitlines()) == len(source.EXPECTED)


@pytest.mark.parametrize("host,asset", [("codex", codex), ("claude", claude)])
@pytest.mark.parametrize("change", ["missing", "changed", "extra"])
def test_real_inventory_predicate_refuses_incomplete_changed_and_unknown_sources(
    tmp_path, host, asset, change
):
    records = {name: {"sha256": sha} for name, sha in (installer.EXPECTED | asset.EXPECTED).items()}
    name = f"{host}/skills/al/scripts/al_repo_hatch.py"
    if change == "missing":
        del records[name]
    elif change == "changed":
        records[name]["sha256"] = "0" * 64
    else:
        records[f"{host}/skills/al/scripts/uninspected.py"] = {"sha256": "0" * 64}
    (tmp_path / "manifest.json").write_text(json.dumps({"complete_file_manifest": records}))
    operation = SimpleNamespace(initialization={"pin_root": str(tmp_path)}, envelope={"host": host})
    with pytest.raises(PolicyError) as failure:
        control_qualification.prepare(
            operation, ["retry", "al-engineer", "--reason", "inspected"], {}
        )
    assert failure.value.code == "UNSUPPORTED_ENFORCEMENT_CAPABILITY"
    assert failure.value.diagnostic["cause"] == "source_inventory_mismatch"


@pytest.fixture
def inventory_source(tmp_path):
    root = tmp_path / "source"
    for group in inventory.GROUPS:
        relative = "installer" if group == "installer" else group + "/skills/al/scripts"
        directory = root / "al" / relative
        directory.mkdir(parents=True)
        (directory / "fixture.py").write_text("VALUE = 1\n")
    (root / "al/alctl.py").write_text("# fixed private source\n")
    actual = {}
    for group, paths in inventory.physical_paths(root).items():
        actual[group] = {
            p.relative_to(root / "al").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }
    maps = root / "harness/policy"
    maps.mkdir(parents=True)
    for group, records in actual.items():
        (maps / ("control_sources_" + group + ".py")).write_bytes(inventory.render_index(records))
    return root


def index_bytes(root):
    return {p.name: p.read_bytes() for p in (root / "harness/policy").glob("control_sources_*.py")}


def index_state(root):
    return {
        p.name: (p.read_bytes(), inventory.identity(p.stat()))
        for p in (root / "harness/policy").glob("control_sources_*.py")
    }


@pytest.mark.parametrize("change", ["bytes", "new-executable", "missing-executable"])
def test_offline_check_refuses_source_drift_without_rewriting_indexes(
    inventory_source, change, capsys
):
    root = inventory_source
    assert inventory.check(root)["ok"]
    original = index_state(root)
    source = root / "al/installer/fixture.py"
    if change == "bytes":
        source.write_text("VALUE = 2\n")
    elif change == "new-executable":
        (source.parent / "unreviewed.py").write_text("VALUE = 3\n")
    else:
        source.unlink()
    assert inventory.main(["--root", str(root)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert (
        report["issues"][0]["kind"]
        == {"bytes": "changed", "new-executable": "uninspected", "missing-executable": "missing"}[
            change
        ]
    )
    assert index_state(root) == original


def test_candidate_render_is_deterministic_and_does_not_approve_or_mutate(
    inventory_source, tmp_path
):
    root = inventory_source
    original = index_bytes(root)
    (root / "al/installer/new.py").write_text("VALUE = 2\n")
    first, second = tmp_path / "candidate-one", tmp_path / "candidate-two"
    for output in (first, second):
        result = inventory.render_candidates(root, output)
        assert not result["ok"] and result["qualification"] == "not established"
    assert index_bytes(root) == original
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()
    }
    parsed = inventory.parse_index(
        (first / "control_sources_installer.py").read_bytes(), "installer"
    )
    assert "installer/new.py" in parsed
    with pytest.raises(inventory.InventoryError, match="must be new"):
        inventory.render_candidates(root, first)
    with pytest.raises(inventory.InventoryError, match="outside"):
        inventory.render_candidates(root, root / "candidates")
    assert index_bytes(root) == original


@pytest.mark.parametrize("problem", ["duplicate", "hash", "unsafe", "foreign", "expression"])
def test_index_data_is_closed_and_never_executed(inventory_source, problem):
    root = inventory_source
    path = root / "harness/policy/control_sources_installer.py"
    raw = path.read_bytes()
    if problem == "duplicate":
        raw = raw.replace(b"alctl.py\n", b"alctl.py\nalctl.py\n", 1)
    elif problem == "hash":
        raw = raw.replace(b'HASHES = """', b'HASHES = """invalid', 1)
    elif problem == "unsafe":
        raw = raw.replace(b"installer/fixture.py", b"installer/../fixture.py")
    elif problem == "foreign":
        raw = raw.replace(b"installer/fixture.py", b"foreign/fixture.py")
    else:
        raw += b'raise RuntimeError("index code executed")\n'
    path.write_bytes(raw)
    with pytest.raises(inventory.InventoryError):
        inventory.check(root)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("kind", ["file", "index", "directory", "missing-root"])
def test_unsafe_or_missing_inventory_inputs_refuse(inventory_source, tmp_path, kind):
    root = inventory_source
    if kind == "missing-root":
        source = root / "al/installer/fixture.py"
        source.unlink()
        source.parent.rmdir()
    elif kind == "directory":
        directory = root / "al/installer"
        directory.rename(tmp_path / "moved")
        directory.symlink_to(tmp_path / "moved", target_is_directory=True)
    else:
        source = root / (
            "al/installer/fixture.py"
            if kind == "file"
            else "harness/policy/control_sources_installer.py"
        )
        target = tmp_path / "private-target"
        target.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(target)
    with pytest.raises((inventory.InventoryError, OSError)):
        inventory.check(root)


@pytest.mark.parametrize("change", ["bytes", "membership"])
def test_source_movement_during_observation_refuses(inventory_source, monkeypatch, change):
    root = inventory_source
    original = inventory.read_file
    changed = []

    def moving(path):
        result = original(path)
        if not changed:
            changed.append(True)
            if change == "bytes":
                path.write_bytes(result[0] + b"\n")
            else:
                (root / "al/installer/appeared.py").write_text("VALUE = 2\n")
        return result

    monkeypatch.setattr(inventory, "read_file", moving)
    with pytest.raises(inventory.InventoryError, match="changed during observation"):
        inventory.check(root)


def test_inventory_diagnostics_are_bounded(inventory_source):
    root = inventory_source
    for i in range(inventory.MAX_ISSUES + 5):
        (root / "al/installer" / f"new_{i:03}.py").write_text("VALUE = 2\n")
    result = inventory.check(root)
    assert len(result["issues"]) == inventory.MAX_ISSUES and result["additional_issues"] == 5


@pytest.mark.parametrize("name", ["bad\nname.py", 'bad"""name.py', r"bad\nname.py"])
def test_real_unsupported_source_filename_cannot_render_candidate(inventory_source, tmp_path, name):
    root = inventory_source
    original = index_state(root)
    source = root / "al/installer" / name
    source.write_text("VALUE = 2\n")
    output = tmp_path / "must-not-exist"
    with pytest.raises(inventory.InventoryError, match="unsupported source filename"):
        inventory.render_candidates(root, output)
    assert not output.exists() and index_state(root) == original
    assert source.read_text() == "VALUE = 2\n"


def test_candidate_roundtrip_refuses_statement_bearing_render_before_output(
    inventory_source, tmp_path, monkeypatch
):
    root = inventory_source
    original = index_state(root)
    output = tmp_path / "must-not-exist"
    monkeypatch.setattr(
        inventory, "render_index", lambda records: b'raise RuntimeError("not data")\n'
    )
    with pytest.raises(inventory.InventoryError, match="unsupported index statement"):
        inventory.render_candidates(root, output)
    assert not output.exists() and index_state(root) == original
