# ruff: noqa: F811
"""Real macOS boundary used by the exact managed start path."""

import sys
import pytest

from al.tests.runtime_run_helpers import launched  # noqa: F401
from al.tests.test_runtime_successor import prepare
from tests.conftest import conn, controller, db_path, isolated_home  # noqa: F401
from tests.test_template_classification_integration import operations, registered  # noqa: F401


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS prepared-start containment")

def test_contained_capture_keeps_real_env_out_of_retained_artifacts(registered):  # noqa: F811
    receipt, _ = prepare(registered)
    import json
    from pathlib import Path

    state = json.loads(Path(receipt["successor_state_ref"]["stored_path"]).read_bytes())
    runtime = Path(receipt["successor_transaction"]["runtime_directory"])
    assert all(
        b"NEVER_PRINT_SYNTHETIC_SECRET" not in path.read_bytes()
        for path in runtime.rglob("*")
        if path.is_file()
    )
    assert state["proof_admission"]["status"] == "pending"


def test_start_boundary_allows_exact_template_but_refuses_secret_and_product_registry_effects(
    registered,
):
    import subprocess
    import sys
    from pathlib import Path

    from al.installer.runtime_start_isolation import launch

    _, _, _, _, _, _, envelope = registered[0]
    root = Path(envelope["repository"])
    home = Path.home()
    synthetic_credentials = [home / '.docker/config.json', home / '.azure/fixture.json', home / '.kube/config']
    assert home.is_relative_to(root.parent)
    for credential in synthetic_credentials:
        credential.parent.mkdir(parents=True,exist_ok=True)
        credential.write_text('SYNTHETIC_CREDENTIAL_SENTINEL')

    def probe(value, notification):
        descriptor = value["isolation_descriptor"]
        scratch = Path(descriptor["scratch"])
        registry = Path(notification["storage"]) / "runs/forbidden.json"
        denied = [(str(path), "rb") for path in synthetic_credentials] + [
            (str(root / ".env"), "rb"),
            (str(root / "app/.env.example"), "wb"),
            (str(root / "forbidden-product"), "wb"),
            (str(registry), "wb"),
        ]
        code = (
            "from pathlib import Path\n"
            + "assert (Path("
            + repr(str(root / "app/.env.example"))
            + ").read_bytes())\n"
            + "for name,mode in "
            + repr(denied)
            + ":\n"
            + " try:\n  with open(name,mode): pass\n"
            + " except PermissionError: pass\n"
            + ' else: raise SystemExit("boundary permitted forbidden effect")\n'
            + "Path("
            + repr(str(scratch / "owned-scratch"))
            + ').write_bytes(b"fixture")\n'
            + 'print("start boundaries verified")\n'
        )
        result = subprocess.run(
            launch(descriptor, sys.executable, code), cwd=root, capture_output=True, timeout=20
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == b"start boundaries verified\n"

    prepare(registered, change=probe)
