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

ROOT = Path(__file__).resolve().parents[1] / "al"


@pytest.mark.parametrize("host,asset", [("codex", codex), ("claude", claude)])
def test_fixed_inventory_matches_complete_current_executable_closure(host, asset):
    paths = [ROOT / "alctl.py", *sorted((ROOT / "installer").glob("*.py"))]
    paths += sorted((ROOT / host / "skills/al/scripts").glob("*.py"))
    actual = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    assert installer.EXPECTED | asset.EXPECTED == actual
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
