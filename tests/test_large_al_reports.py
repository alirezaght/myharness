"""Synthetic guarded result producers exercise real dispatch, private capture and publication."""

import json
import sys
import time
from pathlib import Path

import pytest

from harness.execution_al_reports import REPORT_LIMIT, STDERR_LIMIT, GateReport, report_request
from harness.execution_artifacts import ArtifactStore
from harness.execution_contract import digest
from harness.execution_logs import MAX_TEXT
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
if FAILURE=='identity':
 release=Path(IDENTITY_RELEASE)
 deadline=time.monotonic()+20
 while not release.exists():
  if time.monotonic()>=deadline:raise TimeoutError('owned handle barrier timed out')
  time.sleep(0.01)
s=json.loads(source.read_bytes())
input_path=INPUT
if FAILURE in {'timeout','cancel'}: time.sleep(10)
n=len(s.get('gate_manifests',[]))+1
m={'schema_version':1,'id':'G'+str(n),'revision':n,'base_oid':s['identity']['merge_base'],'paths':['apps/web'],'units':['apps/web'],'rules':[],'gates':[{'applicability':'UNRESOLVED'}],'service_coverage':{'assessment':{'outcome':'incomplete','enumeration':{'status':'incomplete'},'check_discovery':{'status':'incomplete'},'gaps':[{'code':'missing-check'}]}}}
if LARGE: m.update(padding='x'*(17*1024*1024),large_array=[0]*500000)
# Content failures change the manifest before it is hashed and stored, so the harness's
# own base and scope checks (not the stored/stdout comparison) must refuse them.
if FAILURE=='base':m['base_oid']='wrong'
if FAILURE=='scope':m['paths']=['foreign']
# A legitimately widened report (#117): discovery adds paths and units beyond the request.
if FAILURE=='widened':
 m['paths']=['apps/web','apps/admin']
 m['units']=['apps/web','services/billing']
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
        .replace("IDENTITY_RELEASE", repr(str(operation.report_identity_release)))
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
    operation.report_identity_release = operation.directory / "identity-release"
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
    operation.report_launch_response = result
    if not background:
        return operation, principal, result
    if failure == "identity":
        assert result.get("handle"), result
        operation.report_identity_release.write_text("owned handle obtained")
    if failure == "cancel":
        with pytest.raises(ValueError, match="already active"):
            operation.dispatch(principal, "al_command", {"arguments": args, "background": True})
        operation.dispatch(principal, "cancel_process", {"handle": result["handle"]})
    refused_poll = False
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            page = operation.dispatch(
                principal, "poll_process", {"handle": result["handle"], "limit": 65536}
            )
        except ValueError as error:
            if failure != "identity":
                raise
            assert "AL run is stale" in str(error)
            refused_poll = True
            # The public authority guard correctly refuses a foreign run. Inspect only this
            # fixture's owned process to verify that publication was independently refused.
            page = operation.processes.poll(principal["thread_id"], result["handle"])
        assert page["output"] == ""
        if page["status"] != "Running":
            if failure == "identity":
                assert refused_poll, "stale public polling was not refused"
                record = operation.processes.records[result["handle"]]
                assert record["process"].poll() is not None
                assert record["cleanup_status"] == "observed"
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
    resource = "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"]
    first = operation.dispatch(principal, "read_file", {"path": resource, "limit": 65536})
    second = operation.dispatch(
        principal, "read_file", {"path": resource, "offset": first["next_offset"], "limit": 65536}
    )
    tail = operation.dispatch(
        principal, "read_file", {"path": resource, "offset": artifact["size"] - 1024}
    )
    raw = path.read_bytes()
    for page in (first, second, tail):
        assert len(page["text"]) <= MAX_TEXT
        assert page["next_offset"] > page["offset"]
        assert page["text"] == raw[page["offset"] : page["next_offset"]].decode()
    assert first["truncated"] and second["truncated"] and not tail["truncated"]
    assert tail["next_offset"] == artifact["size"]
    assert result["coverage"]["incomplete"] and result["coverage"]["admission"] == "unverified"
    assert result["coverage"]["outcome"] == "incomplete"
    assert result["coverage"]["enumeration"] == "incomplete"
    source = (
        operation.report_launch_response["coverage"] if background else response["source_coverage"]
    )
    assert source == {
        "immutable_snapshot_validated": None,
        "mutable_source_not_checked": True,
        "coordinated_source_consumption": False,
        "legacy_uncoordinated": True,
    }
    if background:
        assert "coverage" not in response and "source_coverage" not in response
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
        assert report.paths["stdout"].is_file()
        code = (
            "from pathlib import Path\ntry:Path("
            + repr(str(report.paths["stdout"]))
            + ").read_bytes()\nexcept OSError as e:\n"
            " if e.errno not in (1,2,13):raise\n"
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
    operation, _, page = run_report(operations, monkeypatch, scope="apps/web/")
    assert page["status"] == "Exited" and page["result"]["artifact"]
    # The published, immutable report is the canonical stored manifest, and the trailing-slash
    # request matched its normalized paths. (The producer is synthetic and always reports
    # apps/web; per #117 discovery may widen, see the widened-report control below.)
    artifact = page["result"]["artifact"]
    stored = operation.artifacts.resolve(artifact["artifact_id"], artifact["sha256"])
    assert digest(stored.read_bytes()) == artifact["sha256"]
    manifest = json.loads(stored.read_bytes())
    assert manifest["paths"] == ["apps/web"] and manifest["units"] == ["apps/web"]
    with operation.artifacts.locked() as index:
        published = [
            r for r in index["records"].values() if r["source"] == "guarded-al-gate-manifest"
        ]
    assert [r["artifact_id"] for r in published] == [artifact["artifact_id"]]


def test_canonical_widened_discovery_report_is_published_as_stored(
    operations,  # noqa: F811
    monkeypatch,
):
    # #117: discovery may legitimately widen beyond the requested paths (task changes,
    # prior manifests, dependency consumers). The transport publishes the canonical stored
    # manifest as long as it covers the normalized request; relevance of the extra units is
    # AL's producer boundary (al/*/skills/al/tests/test_service_coverage.py), not a
    # transport guard.
    operation, _, page = run_report(operations, monkeypatch, scope="apps/web/", failure="widened")
    assert page["status"] == "Exited" and page["result"]["artifact"]
    artifact = page["result"]["artifact"]
    stored = operation.artifacts.resolve(artifact["artifact_id"], artifact["sha256"])
    assert digest(stored.read_bytes()) == artifact["sha256"]
    manifest = json.loads(stored.read_bytes())
    assert manifest["paths"] == ["apps/web", "apps/admin"]
    assert manifest["units"] == ["apps/web", "services/billing"]


def test_normalized_discovery_scope_still_refuses_an_unrelated_report(
    operations,  # noqa: F811
    monkeypatch,
):
    # The trailing-slash request is compared with the report's own paths, not waved through:
    # a stored report that does not cover the requested scope is not published.
    operation, _, page = run_report(operations, monkeypatch, scope="apps/web/", failure="scope")
    assert page["status"] == "Failed" and page["result"] is None
    with operation.artifacts.locked() as index:
        assert all(r["source"] != "guarded-al-gate-manifest" for r in index["records"].values())


def gate_provenance(raw):
    sha = digest(raw).split(":", 1)[1]
    return {
        "operation_id": "al-report-" + "a" * 32,
        "binding": {"execution": {}, "state": {}, "pin": "fixture-pin"},
        "arguments": ["gates", "discover", "--paths-file", "artifact:fixture:sha256:input"],
        "normalized_arguments": ["gates", "discover", "--paths-file", "/fixture/input.json"],
        "input": {"artifact_id": "artifact-" + "b" * 32, "sha256": "sha256:" + "c" * 64},
        "prior_manifests": [],
        "canonical_reference": {
            "id": sha,
            "kind": "gate-manifest",
            "sha256": sha,
            "stored_path": "/fixture/artifacts/" + sha,
            "source": "/fixture/input.json",
            "captured_at": 1.0,
        },
    }


@pytest.mark.parametrize(
    "damage", ["absent", "not-object", "reference", "kind", "hash", "id", "operation", "name"]
)
def test_gate_manifest_read_refuses_malformed_stored_provenance(tmp_path, damage):
    store = ArtifactStore(tmp_path / "artifacts")
    raw = b'{"manifest":"fixture"}'
    provenance = gate_provenance(raw)
    record = store.capture_gate_manifest(raw, provenance)
    with store.locked() as index:
        stored = index["records"][record["artifact_id"]]
        if damage == "absent":
            del stored["provenance"]
        elif damage == "not-object":
            stored["provenance"] = []
        elif damage == "reference":
            stored["provenance"]["canonical_reference"] = []
        elif damage == "operation":
            stored["provenance"]["operation_id"] = "ordinary-operation"
        elif damage == "name":
            stored["name"] = "ordinary.json"
        else:
            stored["provenance"]["canonical_reference"][
                {"kind": "kind", "hash": "sha256", "id": "id"}[damage]
            ] = "foreign"
    with pytest.raises(ValueError, match="gate manifest provenance"):
        store.read(record["artifact_id"], record["sha256"])


def test_large_ordinary_record_cannot_borrow_gate_manifest_allowance(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    raw = b"x" * (16 * 1024 * 1024 + 1)
    with store.locked() as index:
        record = store._publish(index, "gate-manifest.json", raw, "guarded-al-generated")
        record["provenance"] = gate_provenance(raw)
    with pytest.raises(ValueError, match="bounded ordinary execution artifact"):
        store.buffer(record["artifact_id"], record["sha256"])


def test_large_gate_page_redacts_without_changing_authenticated_buffer(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    raw = b'{"password":"SYNTHETIC_GATE_CANARY","padding":"' + b"x" * (17 * 1024 * 1024) + b'"}'
    record = store.capture_gate_manifest(raw, gate_provenance(raw))
    page = store.read(record["artifact_id"], record["sha256"], limit=1024)
    assert "SYNTHETIC_GATE_CANARY" not in page["text"] and "[redacted]" in page["text"]
    assert page["presentation_redacted"] and page["truncated"]
    assert 0 < page["next_offset"] <= 1024 and len(page["text"]) <= MAX_TEXT
    assert store.buffer(record["artifact_id"], record["sha256"]) == raw


@pytest.mark.parametrize("background", [False, True])
def test_ordinary_command_retains_top_level_source_coverage(
    operations,  # noqa: F811
    monkeypatch,
    background,
):
    operation, principal, _, _, _, _, _ = operations
    monkeypatch.setattr(
        "harness.execution_operations.al_argv",
        lambda *_a, **_k: [sys.executable, "-I", "-c", "print('ordinary command output')"],
    )
    response = operation.dispatch(
        principal, "al_command", {"arguments": ["status"], "background": background}
    )
    assert response["coverage"] == {
        "immutable_snapshot_validated": None,
        "mutable_source_not_checked": True,
        "coordinated_source_consumption": False,
        "legacy_uncoordinated": True,
    }
    assert "source_coverage" not in response
    if background:
        deadline, output = time.monotonic() + 30, ""
        while time.monotonic() < deadline:
            page = operation.dispatch(principal, "poll_process", {"handle": response["handle"]})
            output += page["output"]
            if page["status"] != "Running":
                break
            time.sleep(0.01)
        assert page["status"] == "Exited" and page["returncode"] == 0
        assert "result" not in page
    else:
        assert response["returncode"] == 0
        output = response["stdout"]
    assert "ordinary command output" in output
