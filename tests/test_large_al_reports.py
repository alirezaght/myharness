"""Synthetic guarded result producers exercise real dispatch, private capture and publication."""

import json
import sys
import time
from pathlib import Path

import pytest

from harness.execution_al_reports import REPORT_LIMIT, STDERR_LIMIT, GateReport, report_request
from harness.execution_artifacts import ArtifactStore
from harness.execution_contract import digest
from tests.test_execution_operations import operations  # noqa: F401


def request_input(operation, principal, plan=False, scope="apps/web"):
    with __import__("contextlib").closing(
        __import__("harness.db", fromlist=["connect"]).connect(operation.database)
    ) as conn:
        state = operation.state(
            __import__("harness.execution_store", fromlist=["get_execution"]).get_execution(
                conn, operation.execution_id
            )
        )
    content = json.dumps([scope])
    if plan:
        content = json.dumps(
            {
                "schema_version": 1,
                "base_oid": state["identity"]["merge_base"],
                "paths": ["apps/web"],
                "units": ["apps/web"],
                "rules": [],
                "gates": [{"applicability": "UNRESOLVED"}],
                "service_coverage": {
                    "assessment": {
                        "outcome": "incomplete",
                        "enumeration": {"status": "incomplete"},
                        "check_discovery": {"status": "incomplete"},
                        "gaps": [{"code": "missing-check"}],
                    }
                },
            }
        )
    artifact = operation.artifacts.write_artifact(
        principal,
        "paths.json",
        content,
        state["integration"]["graph_revision"],
        state["integration"]["graph_revision"],
    )
    ref = "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"]
    return [
        "gates",
        "plan" if plan else "discover",
        "--file" if plan else "--paths-file",
        ref,
    ], state


def producer(operation, *, large=False, failure=None):
    source = operation.initialization["source"]
    # This synthetic producer replaces only AL computation. Dispatch/authority, OS isolation,
    # capture, append binding, canonical publication and public polling are production code.
    return (
        """import sys,json,hashlib,os,time
from pathlib import Path
source=Path(SOURCE)
s=json.loads(source.read_bytes())
input_path=INPUT
if FAILURE in {'timeout','cancel'}: time.sleep(10)
n=len(s.get('gate_manifests',[]))+1
m={'schema_version':1,'id':'G'+str(n),'revision':n,'base_oid':s['identity']['merge_base'],'paths':['apps/web'],'units':['apps/web'],'rules':[],'gates':[{'applicability':'UNRESOLVED'}],'service_coverage':{'assessment':{'outcome':'incomplete','enumeration':{'status':'incomplete'},'check_discovery':{'status':'incomplete'},'gaps':[{'code':'missing-check'}]}}}
if LARGE: m.update(padding='x'*(17*1024*1024),large_array=[0]*500000)
raw=json.dumps(m,sort_keys=True).encode();sha=hashlib.sha256(raw).hexdigest()
target=source.parent/s['session_id']/'artifacts'/sha
target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
r={'id':sha,'kind':'gate-manifest','sha256':sha,'stored_path':str(target),'source':input_path,'captured_at':1.0}
if FAILURE=='kind':r['kind']='other'
if FAILURE=='hash':r['sha256']='f'*64;r['id']='f'*64
if FAILURE=='tamper':target.write_bytes(raw+b' ')
if FAILURE=='input':r['source']='foreign input'
if FAILURE=='foreign':r['stored_path']=str(source)
if FAILURE=='hardlink':os.link(target,target.parent/'alias')
if FAILURE=='symlink':
 target.rename(target.parent/'original')
 target.symlink_to(target.parent/'original')
if FAILURE=='base':m['base_oid']='wrong'
if FAILURE=='scope':m['paths']=['foreign']
if FAILURE=='pin':s['runtime_origin']['pin']={'pin_identity':'foreign'}
if FAILURE=='identity':s['run_id']='foreign'
s.setdefault('gate_manifests',[]).append(r)
if FAILURE=='interleaved':s['gate_manifests'].append(r)
temporary=source.parent/'.synthetic-state.tmp'
temporary.write_text(json.dumps(s));os.replace(temporary,source)
if FAILURE=='exit':raise SystemExit(2)
if FAILURE=='overflow':sys.stdout.write('x'*(32*1024*1024+1));raise SystemExit()
if FAILURE=='stderr-overflow':sys.stderr.write('x'*(1024*1024+1));raise SystemExit()
if FAILURE=='malformed':print('{broken');raise SystemExit()
print(json.dumps(m,indent=2))
if FAILURE=='trailing':print('unexpected tail')
""".replace("SOURCE", repr(source))
        .replace("INPUT", repr(str(operation.artifacts.resolve(*operation.report_input))))
        .replace("FAILURE", repr(failure))
        .replace("LARGE", repr(large))
    )


def run_report(
    fixture,
    monkeypatch,
    *,
    background=True,
    large=False,
    failure=None,
    plan=False,
    scope="apps/web",
):
    operation, principal, _, _, _, _, _ = fixture
    args, state = request_input(operation, principal, plan=plan, scope=scope)
    _, identifier, sha = args[3].split(":", 2)
    operation.report_input = (identifier, sha)
    script = producer(operation, large=large, failure=failure)
    monkeypatch.setattr(
        "harness.execution_operations.al_argv",
        lambda *args, **kwargs: [sys.executable, "-I", "-c", script],
    )
    result = operation.dispatch(
        principal,
        "al_command",
        {"arguments": args, "background": background, "timeout": 1 if failure == "timeout" else 30},
    )
    if not background:
        return operation, principal, result
    if failure == "cancel":
        with pytest.raises(ValueError, match="already active"):
            operation.dispatch(principal, "al_command", {"arguments": args, "background": True})
        operation.dispatch(principal, "cancel_process", {"handle": result["handle"]})
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            page = operation.dispatch(
                principal, "poll_process", {"handle": result["handle"], "limit": 65536}
            )
        except ValueError:
            if failure != "identity":
                raise
            # The public authority guard correctly refuses a foreign run. Inspect only this
            # fixture's owned process to verify that publication was independently refused.
            page = operation.processes.poll(principal["thread_id"], result["handle"])
        assert page["output"] == ""
        if page["status"] != "Running":
            return operation, principal, page
        time.sleep(0.01)
    raise AssertionError("report did not settle")


@pytest.mark.parametrize("background", [False, True])
def test_large_manifest_uses_canonical_bytes_and_small_public_result(
    operations,  # noqa: F811
    monkeypatch,
    background,
):
    operation, principal, response = run_report(
        operations, monkeypatch, background=background, large=True
    )
    result = response["result"] if background else response
    artifact = result["artifact"]
    path = operation.artifacts.resolve(artifact["artifact_id"], artifact["sha256"])
    assert artifact["size"] > 16 * 1024 * 1024 and path.stat().st_size == artifact["size"]
    assert len(json.dumps(json.loads(path.read_bytes()), indent=2).encode()) > 20 * 1024 * 1024
    assert digest(path.read_bytes()) == artifact["sha256"]
    assert len(json.dumps(response)) < 4096
    assert result["coverage"]["incomplete"] and result["coverage"]["admission"] == "unverified"
    assert result["manifest"]["units"] == 1 and result["manifest"]["gates"] == 1
    with operation.artifacts.locked() as index:
        record = index["records"][artifact["artifact_id"]]
        assert (
            record["provenance"]["canonical_reference"]["sha256"]
            == artifact["sha256"].split(":")[1]
        )
    if background:
        again = operation.dispatch(principal, "poll_process", {"handle": response["handle"]})
        assert again["result"]["artifact"] == artifact
    assert list((operation.directory / "private-report-spools").iterdir()) == []


@pytest.mark.parametrize(
    "failure",
    [
        "exit",
        "kind",
        "hash",
        "foreign",
        "hardlink",
        "symlink",
        "base",
        "scope",
        "pin",
        "identity",
        "interleaved",
        "malformed",
        "trailing",
        "timeout",
        "cancel",
        "overflow",
        "stderr-overflow",
        "tamper",
        "input",
    ],
)
def test_invalid_or_failed_report_never_publishes(operations, monkeypatch, failure):  # noqa: F811
    operation, _, result = run_report(operations, monkeypatch, failure=failure)
    assert result["status"] == "Failed" and result["result"] is None
    with operation.artifacts.locked() as index:
        assert all(r["source"] != "guarded-al-gate-manifest" for r in index["records"].values())
    assert list((operation.directory / "private-report-spools").iterdir()) == []


def test_only_known_commands_get_dedicated_capability():
    assert report_request(["status"]) is False
    for args in [
        ["gates", "discover", "--paths-file", "/host/file"],
        ["gates", "plan", "--file", "artifact:x:y", "--limit", "huge"],
    ]:
        with pytest.raises(ValueError):
            report_request(args)


def test_capture_bounds_and_generic_artifact_limit(operations, tmp_path):  # noqa: F811
    operation, principal, _, _, _, _, _ = operations
    args, state = request_input(operation, principal)
    identifier, sha = (
        operation.report_input if hasattr(operation, "report_input") else args[3].split(":", 2)[1:]
    )
    report = GateReport(
        operation,
        principal,
        args,
        ["gates", "discover", "--paths-file", str(operation.artifacts.resolve(identifier, sha))],
        state,
    )
    try:
        report.consume("stdout", b" " * REPORT_LIMIT)
        with pytest.raises(ValueError, match="stdout limit"):
            report.consume("stdout", b"x")
        report.consume("stderr", b" " * STDERR_LIMIT)
        with pytest.raises(ValueError, match="stderr limit"):
            report.consume("stderr", b"x")
    finally:
        report.close()
    with pytest.raises(ValueError, match="capture bound"):
        ArtifactStore(tmp_path / "ordinary").capture_generated(
            "ordinary.json", b"x" * (16 * 1024 * 1024 + 1)
        )


def test_private_spools_are_hidden_from_operation_and_check_children(operations):  # noqa: F811
    from harness.execution_check_process import check_profile
    from harness.execution_isolation import operation_popen

    operation, principal, _, initial, _, execution, envelope = operations
    args, state = request_input(operation, principal)
    identifier, sha = args[3].split(":", 2)[1:]
    report = GateReport(
        operation,
        principal,
        args,
        ["gates", "discover", "--paths-file", str(operation.artifacts.resolve(identifier, sha))],
        state,
    )
    try:
        report.consume("stdout", b"private synthetic report")
        parent = operation.profile(al=True)
        context = Path(parent.policy).parent / "context.json"
        context.write_text(
            json.dumps(
                {
                    "handoff": execution["handoff_path"],
                    "repository": envelope["repository"],
                    "temporary": parent.temporary,
                    "al_session": str(Path(initial["source"]).parent),
                    "protected": list(parent.protected),
                    "hidden": list(parent.hidden),
                }
            )
        )
        checked, _ = check_profile(context, envelope["repository"])
        code = (
            "from pathlib import Path\ntry:Path("
            + repr(str(report.paths["stdout"]))
            + ").read_bytes()\nexcept PermissionError:pass\n"
            "else:raise SystemExit('private spool readable')"
        )
        for profile in [operation.profile(), checked]:
            proc = operation_popen(
                profile,
                [sys.executable, "-I", "-c", code],
                cwd=envelope["repository"],
                stdout=__import__("subprocess").PIPE,
                stderr=__import__("subprocess").PIPE,
                start_new_session=True,
            )
            out, err = proc.communicate(timeout=10)
            assert proc.returncode == 0, (out, err)
    finally:
        report.close()


def test_gate_plan_uses_same_bounded_result_contract(operations, monkeypatch):  # noqa: F811
    _, _, page = run_report(operations, monkeypatch, plan=True)
    assert page["status"] == "Exited" and page["result"]["manifest"]["revision"] == 1
    assert page["result"]["coverage"]["incomplete"]


def test_synchronous_failure_keeps_actual_exit_without_publication(operations, monkeypatch):  # noqa: F811
    operation, _, response = run_report(operations, monkeypatch, background=False, failure="exit")
    assert response["status"] == "Failed" and response["returncode"] == 2
    assert response["artifact"] is None and response["stdout"] == ""
    assert list((operation.directory / "private-report-spools").iterdir()) == []


def test_partial_spool_creation_failure_cleans_owned_files(operations, monkeypatch):  # noqa: F811
    import harness.execution_al_reports as reports

    operation, principal, _, _, _, _, _ = operations
    args, state = request_input(operation, principal)
    identifier, sha = args[3].split(":", 2)[1:]
    original = reports.tempfile.mkstemp
    calls = []

    def fail_second(*args, **kwargs):
        if Path(kwargs.get("dir", ".")) == operation.directory / "private-report-spools":
            calls.append(True)
            if len(calls) == 2:
                raise OSError("synthetic spool creation failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(reports.tempfile, "mkstemp", fail_second)
    with pytest.raises(OSError, match="synthetic spool creation"):
        GateReport(
            operation,
            principal,
            args,
            [
                "gates",
                "discover",
                "--paths-file",
                str(operation.artifacts.resolve(identifier, sha)),
            ],
            state,
        )
    assert list((operation.directory / "private-report-spools").iterdir()) == []


def test_discovery_scope_matches_pinned_path_normalization(operations, monkeypatch):  # noqa: F811
    _, _, page = run_report(operations, monkeypatch, scope="apps/web/")
    assert page["status"] == "Exited" and page["result"]["artifact"]
