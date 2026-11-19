"""Execute required Knowledge imports from fresh sealed authority snapshots."""

import hashlib
import json
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from harness.execution_bootstrap import authority_snapshot, prepared_authority_snapshot

_ISOLATED_IMPORTS = r"""
import json, sys
from pathlib import Path

assert sys.flags.isolated and sys.flags.no_site
assert not any(n == "harness" or n.startswith("harness.") for n in sys.modules)
snapshot = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(snapshot))
sys.path.append(sys.argv[2])
from harness.knowledge.contribution_contract import description, extract, feedback
from harness.knowledge.contributions import current_bytes
from harness.assignment_observation import registered_assignment
from harness.execution_operations import OperationController

assert feedback(b'{"summary":"sealed import regression"}')["status"] == "ready"
assert len(extract(b'{"summary":"sealed import regression"}', "al-scout", "scout-report")) == 1
assert description()["contract_version"] == 1
modules = {}
for name, module in tuple(sys.modules.items()):
    if name == "harness" or name.startswith("harness."):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(snapshot / "harness"), (name, str(path))
        modules[name] = str(path.relative_to(snapshot))
print(json.dumps({"modules": modules}))
"""


def isolated_snapshot_child(snapshot, program, arguments, cwd):
    cwd.mkdir()
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            program,
            str(snapshot),
            sysconfig.get_paths()["purelib"],
            *arguments,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("prepared", [False, True])
def test_snapshot_contains_executable_contribution_contract(tmp_path, monkeypatch, prepared):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    if prepared:
        receipt = prepared_authority_snapshot(tmp_path / "execution")
        raw = Path(receipt["stored_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt["sha256"]
        snapshot = Path(json.loads(raw)["snapshot"])
    else:
        snapshot = Path(authority_snapshot(tmp_path / "execution"))
    result = isolated_snapshot_child(snapshot, _ISOLATED_IMPORTS, [], tmp_path / "isolated")
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert "harness.knowledge.contribution_contract" in observed["modules"]
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    module = snapshot / "harness/knowledge/contribution_contract.py"
    assert module.is_file()
    assert (
        manifest["files"]["knowledge/contribution_contract.py"]
        == hashlib.sha256(module.read_bytes()).hexdigest()
    )
