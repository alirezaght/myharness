"""Actual tiny shared-runner invocations; mutation companions never become candidate proof."""

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from al.tests.verification_helpers import declarations, source
from scripts import verify_al_functional as V

AMENDMENT = (
    Path(__file__).resolve().parents[1]
    / "docs/reliability-platform/publication-20261125/timing-acceptance.json"
)
PATHS = [
    "al/tests/test_a.py",
    "al/codex/skills/al/tests/test_a.py",
    "al/claude/skills/al/tests/test_a.py",
]


def workspace(tmp_path, body=None):
    root = source(tmp_path, {"installer": body} if body else None)
    repository = Path(V.__file__).resolve().parents[1]
    # The real, unchanged runner exposes root= for its genuine tiny-tree conformance tests.
    (root / "al/verify_changed.py").write_text(
        "import sys\nsys.path.insert(0," + repr(str(repository)) + ")\n"
        "from al.verify_changed import main\nraise SystemExit(main(root=" + repr(str(root)) + "))\n"
    )
    (root / "al/verification-obligations.json").write_text(json.dumps(declarations(root)))
    return root


def amendment_fixture(tmp_path):
    raw = AMENDMENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == V.AMENDMENT_SHA256
    path = tmp_path / "timing-acceptance.json"
    path.write_bytes(raw)
    return path


def invoke(tmp_path, body=None, *, timeout=60):
    root = workspace(tmp_path, body)
    output = tmp_path / "proof"
    result = V.run(root, output, amendment_fixture(tmp_path), paths=PATHS, timeout=timeout)
    request = json.loads((output / "invocation.json").read_text())
    return root, output, request, result


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return invoke(tmp_path_factory.mktemp("functional-bundle"))


def validate_bundle(bundle):
    root, output, request, result = bundle
    return V.validate(
        root,
        output,
        request["source_binding"],
        sorted(PATHS),
        None,
        request["selection"],
        result["raw_runner_exit"],
    )


def test_real_success_preserves_exact_selection_and_unknown_retained_custody(bundle):
    _, output, request, result = bundle
    assert result["functional_assertions"] == "PASS" and result["raw_runner_status"] == "PASS"
    assert result["executed_passed"] == 3 and result["over_budget"] == []
    assert (
        result["descendant_settlement"] == "unknown" and result["producer_closure_claim"] == "none"
    )
    assert result["storage"] == "retained; descendant settlement unproven"
    assert all(
        p["leader_reaped"] and p["owned_group_settled"] and p["descendants_settled"] is None
        for p in result["producer_receipts"]
    )
    argv = request["argv"]
    assert argv[2:4] == ["--tier", "all"]
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--path"] == PATHS
    assert not {"--base", "--all", "--workers", "--split", "--host"} & set(argv)
    assert (output / "functional.json").is_file()
    assert validate_bundle(bundle)["raw_report"] == result["raw_report"]


def test_real_timing_only_failure_has_separate_checkpoint_disposition(tmp_path):
    _, output, _, result = invoke(tmp_path, "import time\ndef test_a():\n    time.sleep(5.1)\n")
    raw = json.loads((output / "raw.json").read_text())
    assert result["functional_assertions"] == "PASS" and result["executed_passed"] == 3
    assert result["raw_runner_status"] == raw["status"] == "FAIL" and result["raw_runner_exit"] == 1
    assert result["timing_disposition"] == "accepted checkpoint limitation"
    assert result["over_budget"] == raw["over_budget"] and result["over_budget"]
    assert result["amendment"]["sha256"] == V.AMENDMENT_SHA256
    assert (
        result["raw_report"]["sha256"]
        == hashlib.sha256((output / "raw.json").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "body",
    [
        "def test_a():\n    assert False\n",
        'import pytest\ndef test_a():\n    pytest.skip("unavailable")\n',
        "import os\ndef test_a():\n    os._exit(2)\n",
    ],
)
def test_real_failure_skip_and_crash_never_become_functional_pass(tmp_path, body):
    with pytest.raises((ValueError, KeyError)):
        invoke(tmp_path, body)
    assert not (tmp_path / "proof/functional.json").exists()


def test_real_timeout_is_not_timing_acceptance(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        invoke(tmp_path, "import time\ndef test_a():\n    time.sleep(30)\n", timeout=0.2)
    journal = json.loads((tmp_path / "proof/process/process-journal.json").read_text())
    assert journal["cause"]["kind"] == "TimeoutExpired"
    assert journal["cleanup"]["leader_reaped"] and journal["cleanup"]["owned_group_settled"]
    assert not (tmp_path / "proof/functional.json").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "failed",
        "skip",
        "missing",
        "duplicate",
        "selection",
        "source",
        "cause",
        "cleanup",
        "unresolved",
        "budget",
        "plan_hash",
        "wrong_run",
        "stale_plan",
        "counts",
        "exit",
    ],
)
def test_inconsistent_current_report_refuses(bundle, fault):
    _, output, _, _ = bundle
    path = output / "raw.json"
    original = path.read_bytes()
    report = json.loads(original)
    r = report["verification"]
    if fault == "failed":
        r["reconciliation"]["executed_failed"] = 1
    elif fault == "skip":
        r["reconciliation"]["skipped"] = 1
    elif fault == "missing":
        r["shards"].pop()
    elif fault == "duplicate":
        r["shards"].append(copy.deepcopy(r["shards"][0]))
    elif fault == "selection":
        report["selection"]["installer"] = []
    elif fault == "source":
        report["source_stable"] = False
    elif fault == "cause":
        r["cause"] = {"kind": "KeyboardInterrupt"}
    elif fault == "cleanup":
        r["cleanup"]["complete"] = False
    elif fault == "unresolved":
        r["cleanup"]["unresolved"] = [{"pid": 1}]
    elif fault == "budget":
        report["over_budget"] = ["invented timing violation"]
    elif fault == "plan_hash":
        r["plan_sha256"] = "0" * 64
    elif fault == "wrong_run":
        r["run_id"] = "other-invocation"
    elif fault == "stale_plan":
        r["plan_path"] = str(output.parent / "other-run/plan.json")
    elif fault == "counts":
        r["reconciliation"]["executed_passed"] += 1
    else:
        report["status"] = "FAIL"
    try:
        path.write_text(json.dumps(report))
        with pytest.raises((ValueError, KeyError, OSError)):
            validate_bundle(bundle)
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize(
    "fault",
    [
        "publication_missing",
        "publication_corrupt",
        "ownership",
        "node",
        "child_hash",
        "log",
        "malformed",
        "duplicate_json",
        "nonfinite",
    ],
)
def test_retained_artifact_tamper_refuses(bundle, fault):
    _, output, _, _ = bundle
    report = json.loads((output / "raw.json").read_text())
    shard = report["verification"]["shards"][0]
    directory = Path(shard["report"]).parent
    target = {
        "publication_missing": directory / "storage-publication.json",
        "publication_corrupt": directory / "storage-publication.json",
        "ownership": Path(shard["process"]["ownership"]["receipt"]),
        "node": Path(shard["report"]),
        "child_hash": Path(shard["report"]),
        "log": Path(shard["log"]),
    }.get(fault, output / "raw.json")
    original = target.read_bytes()
    try:
        if fault == "publication_missing":
            target.unlink()
        elif fault == "duplicate_json":
            target.write_bytes(b'{"status":"PASS","status":"FAIL"}')
        elif fault == "nonfinite":
            target.write_bytes(b'{"value":NaN}')
        elif fault == "malformed":
            target.write_bytes(b"{")
        else:
            target.write_bytes(original + b" tampered")
        with pytest.raises((ValueError, KeyError, OSError)):
            validate_bundle(bundle)
    finally:
        target.write_bytes(original)


def test_source_changes_after_actual_run_refuse(bundle):
    root, _, _, _ = bundle
    path = root / PATHS[0]
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n# changed after run\n")
        with pytest.raises(ValueError, match="source drift"):
            validate_bundle(bundle)
    finally:
        path.write_bytes(original)


def test_preexisting_output_and_wrong_amendment_refuse_without_execution(bundle, tmp_path):
    root, output, _, _ = bundle
    before = (output / "raw.json").read_bytes()
    with pytest.raises(FileExistsError):
        V.run(root, output, amendment_fixture(tmp_path), paths=PATHS)
    wrong = tmp_path / "amendment.json"
    wrong.write_text("{}")
    with pytest.raises(ValueError, match="amendment"):
        V.run(root, tmp_path / "not-created", wrong, paths=PATHS)
    assert not (tmp_path / "not-created").exists()
    assert (output / "raw.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["fifo", "symlink", "oversize"])
@pytest.mark.parametrize("reader", ["read", "artifact"])
def test_nonregular_and_oversize_artifacts_refuse_without_blocking(tmp_path, kind, reader):
    path = tmp_path / "unsafe"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("{}")
        path.symlink_to(target)
    else:
        with path.open("wb") as stream:
            stream.truncate(V.LIMIT + 1)
    program = (
        "import sys\nfrom scripts import verify_al_functional as v\n"
        "try: getattr(v,sys.argv[1])(sys.argv[2])\n"
        "except (ValueError,OSError): raise SystemExit(0)\n"
        "raise SystemExit(3)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, reader, str(path)],
        cwd=Path(V.__file__).resolve().parents[1],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_public_adapter_uses_portable_amendment_without_workstation_file(tmp_path, monkeypatch):
    capture = V.capture
    unavailable = Path("/private/tmp/reliability-publication-timing-acceptance-20261125.json")

    def clean_checkout(path, **kwargs):
        if Path(path) == unavailable:
            raise FileNotFoundError("workstation amendment is unavailable")
        return capture(path, **kwargs)

    monkeypatch.setattr(V, "capture", clean_checkout)
    _, output, _, result = invoke(tmp_path)
    assert result["functional_assertions"] == "PASS" and result["executed_passed"] == 3
    assert Path(result["amendment"]["path"]).is_relative_to(tmp_path)
    assert result["amendment"]["sha256"] == V.AMENDMENT_SHA256
    assert (output / "functional.json").is_file()


def test_installer_over_eight_nodes_retains_whole_file_fixture_lifecycle(tmp_path):
    body = "import pytest\n@pytest.fixture(scope='module')\ndef seen():\n    return []\n"
    body += ''.join(
        f"def test_{i:02d}(seen):\n    assert seen == list(range({i}))\n    seen.append({i})\n"
        for i in range(10)
    )
    root, output, request, result = invoke(tmp_path, body)
    assert result['functional_assertions'] == 'PASS' and result['executed_passed'] == 12
    raw = json.loads((output / 'raw.json').read_bytes())
    plan = json.loads(Path(raw['verification']['plan_path']).read_bytes())
    rows = [a for a in plan['execution_assignments'] if a['suite'] == 'installer']
    assert [a['node']['nodeid'] for a in rows] == [
        f'al/tests/test_a.py::test_{i:02d}' for i in range(10)]
    assert {a['group'] for a in rows} == {'installer:al/tests/test_a.py'}
    shards = [s for s in raw['verification']['shards'] if s['assignment']['suite'] == 'installer']
    assert len(shards) == 1
    assert result['amendment']['sha256'] == V.AMENDMENT_SHA256
    assert V.validate(
        root, output, request['source_binding'], sorted(PATHS), None,
        request['selection'], result['raw_runner_exit'],
    )['raw_report'] == result['raw_report']
