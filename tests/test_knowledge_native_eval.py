"""Synthetic-only evaluation contracts; these establish no live/provider acceptance."""

import copy
import json
import os
from contextlib import closing
from pathlib import Path

import pytest

from harness.analyzer import analyze
from harness.db import connect
from harness.design_models import DesignSection
from harness.interview_catalogue import CATALOGUE
from scripts.knowledge_evaluation.admission import admit
from scripts.knowledge_evaluation.package import BUDGETS, PROVENANCE, corpus_receipt, validate
from scripts.knowledge_native_eval import execute
from tests.test_execution_operations import operations as initialized_operations


@pytest.fixture
def operations(controller, monkeypatch, launched, request):
    """Add one meaningful tracked source BEFORE ordinary fixture approval/initialization."""
    from tests.sample_repo import git_init

    root = launched[2]
    source = root / "synthetic/source.py"
    source.parent.mkdir()
    source.write_text("def synthetic_value():\n    return 7\n")
    git_init(root)
    yield from initialized_operations.__wrapped__(controller, monkeypatch, launched, request)


def case(root, tmp_path):
    answers = {
        q.text: "Read-only sourced explanation; preserve approvals and record unavailable cases."
        for q in CATALOGUE
    }
    answers.update(
        {
            q.text: "This evaluation inspects only this frozen synthetic repository. "
            "No product or dependency changes are authorized."
            for q in analyze(root, "Explain demo/export.py architecture").questions
        }
    )
    return {
        "schema_version": 1,
        "case_id": "synthetic-public-case",
        "arm": "B",
        "participant": "single",
        "prompt": "Explain demo/export.py architecture",
        "host": "codex",
        "role": "al-architect",
        "model": "gpt-6-astra",
        "effort": "medium",
        "corpus": {"root": str(root), **corpus_receipt(root)},
        "allowed_roots": [str(root)],
        "state_directory": str(tmp_path / "evaluation-state"),
        "output_directory": str(tmp_path / "evaluation-output"),
        "provenance": {
            "accepted_mission_sha256": "1" * 64,
            "evaluation_authorization_sha256": "2" * 64,
            "statement": PROVENANCE,
        },
        "budgets": dict(BUDGETS),
        "specification": {
            "request": "Explain demo/export.py architecture using read-only corpus evidence.",
            "answers": answers,
            "design": {
                str(s): "Evaluation uses a frozen corpus and private read-only adapter; "
                "return a sourced answer and preserve native refusals."
                for s in DesignSection
            },
            "scenarios": [
                {
                    "category": "e2e",
                    "text": "An actual specialized role reads frozen "
                    "sources and submits one sourced answer without repository mutation.",
                },
                {
                    "category": "failure",
                    "text": "Deny knowledge in B at listing and "
                    "dispatch; reject alternate resources, aliases and excess budgets.",
                },
            ],
        },
    }


def test_default_validation_is_read_only(sample_repo, tmp_path):
    package = case(sample_repo, tmp_path)
    assert execute(package, "validate")["provider_started"] is False
    assert not Path(package["state_directory"]).exists()
    assert not Path(package["output_directory"]).exists()


@pytest.mark.parametrize("change", ["extra", "budget", "overlap", "provenance", "other-root"])
def test_closed_package_refusals(sample_repo, tmp_path, change):
    package = case(sample_repo, tmp_path)
    if change == "extra":
        package["gold"] = "forbidden"
    elif change == "budget":
        package["budgets"]["tool_calls"] = 31
    elif change == "overlap":
        package["output_directory"] = str(sample_repo / "outputs")
    elif change == "provenance":
        package["provenance"]["statement"] = "user clicked approval"
    else:
        package["allowed_roots"].append(str(tmp_path))
    with pytest.raises(ValueError):
        validate(package)


@pytest.mark.parametrize("change", ["dirty", "untracked", "hardlink", "symlink", "head"])
def test_exact_corpus_refusals(sample_repo, tmp_path, change):
    package = case(sample_repo, tmp_path)
    path = sample_repo / "README.md"
    if change == "dirty":
        path.write_text("future candidate material")
    elif change == "untracked":
        (sample_repo / "future.py").write_text("x = 1")
    elif change == "hardlink":
        os.link(path, tmp_path / "alias")
    elif change == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "forbidden")
    else:
        package["corpus"]["head"] = "0" * 40
    with pytest.raises((ValueError, FileNotFoundError)):
        validate(package)


def test_genuine_preparation_and_immutable_output(sample_repo, tmp_path):
    package = case(sample_repo, tmp_path)
    result = execute(package, "prepare")
    assert result["status"] == "prepared" and result["provider_started"] is False
    admission = json.loads((Path(package["output_directory"]) / "admission.json").read_bytes())
    assert admission["new_user_approval_event"] is False
    assert admission["approval"]["content_hash"]
    assert PROVENANCE in Path(admission["approval"]["path"]).read_text()
    assert corpus_receipt(sample_repo)["head"] == package["corpus"]["head"]
    with pytest.raises(ValueError, match="existing state/output"):
        execute(package, "prepare")


def test_incomplete_concrete_interview_refuses(sample_repo, tmp_path):
    package = case(sample_repo, tmp_path)
    package["specification"]["answers"] = {"nonmatching question": "concrete answer"}
    Path(package["state_directory"]).mkdir()
    with pytest.raises(ValueError, match="no answer for"):
        admit(package)


def make_adapter(operations, tmp_path, monkeypatch, arm="B"):
    from scripts.knowledge_evaluation.adapter import EvaluatorAdapter

    operation, principal, *_ = operations
    root = Path(operation.envelope["repository"])
    # This existing initialized fixture includes untracked installation/spec files.
    # Mock ONLY its frozen observation for adapter unit contracts; clean corpus tests above
    # and the complete synthetic worker integration below retain real validation.
    snapshot = {
        "head": "synthetic",
        "tree": "synthetic",
        "files_sha256": "synthetic",
        "file_count": 0,
    }
    monkeypatch.setattr("scripts.knowledge_evaluation.adapter.corpus_receipt", lambda _: snapshot)
    monkeypatch.setattr(__name__ + ".corpus_receipt", lambda _: snapshot)
    package = case(root, tmp_path)
    package["arm"] = arm
    return EvaluatorAdapter(operation, package), principal


def test_actual_initialized_synthetic_authority_and_baseline(operations, tmp_path, monkeypatch):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    from tests.test_execution_continuity import child

    supervisor = principal
    with pytest.raises(ValueError, match="source prefetch"):
        adapter.dispatch(supervisor, "read_file", {"path": "evaluation:analyze"})
    adapter.dispatch(supervisor, "await_assignment", {"timeout": 1})
    principal = child(adapter.operation, "al-architect")
    delivered_before = adapter.delivered
    result = adapter.dispatch(principal, "read_file", {"path": "evaluation:analyze"})
    assert result["repo_root"]
    assert adapter.delivered - delivered_before == len(json.dumps(result).encode())
    adapter.dispatch(principal, "read_file", {"path": "evaluation:analyze"})
    assert adapter.delivered - delivered_before == 2 * len(json.dumps(result).encode())
    forged = copy.deepcopy(principal)
    forged["policy"] = "forged"
    with pytest.raises(ValueError):
        adapter.dispatch(forged, "read_file", {"path": "evaluation:analyze"})
    assert adapter.receipt()["actual_native_proof"] is False

    from harness.execution_mcp import MCPConnection
    from scripts.knowledge_evaluation.live import connection_class

    connection = connection_class(MCPConnection)(lambda _: principal, adapter.dispatch)
    connection.request({"jsonrpc": "2.0", "method": "initialize"})
    listing = connection.request({"jsonrpc": "2.0", "method": "tools/list"})
    assert {t["name"] for t in listing["tools"]} == adapter.allowed
    response = connection.request(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "knowledge_query",
                "arguments": {"query": "forbidden"},
            },
        }
    )
    assert response["isError"] is True


@pytest.mark.parametrize(
    "name,args",
    [
        ("knowledge_query", {"query": "hidden"}),
        ("knowledge_explain", {"bundle_hash": "x", "assertion_id": "x"}),
        ("read_file", {"path": "harness:assignment"}),
        ("read_file", {"path": "harness:knowledge"}),
        ("read_file", {"path": "evaluation:refresh"}),
        ("read_file", {"path": ".env"}),
        ("read_file", {"path": "../other-arm/answer.md"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("read_file", {"path": ".git/config"}),
        ("al_command", {"arguments": ["show"]}),
        ("diagnostic", {"request": {"kind": "source", "path": ".env"}}),
        ("environment_operation", {"operation": "prepare"}),
    ],
)
def test_actual_initialized_synthetic_bypass_refusal(operations, tmp_path, monkeypatch, name, args):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        adapter.dispatch(principal, name, args)
    assert "knowledge_query" not in adapter.allowed
    assert "knowledge_explain" not in adapter.allowed


def test_visible_budgets_and_null_accounting(operations, tmp_path, monkeypatch):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    adapter.delivered = BUDGETS["delivered_bytes"]
    with pytest.raises(ValueError):
        adapter.dispatch(principal, "read_file", {"path": "README.md"})
    assert adapter.delivered == BUDGETS["delivered_bytes"]
    adapter.calls = BUDGETS["tool_calls"]
    with pytest.raises(ValueError):
        adapter.dispatch(principal, "read_file", {"path": "README.md"})
    adapter.started -= BUDGETS["wall_seconds"]
    with pytest.raises(ValueError, match="wall budget"):
        adapter.check_time()
    assert adapter.receipt()["service_read_bytes"] is None
    assert adapter.receipt()["output_tokens"] is None


@pytest.mark.parametrize("arm", ["B", "C"])
def test_complete_clean_synthetic_worker_uses_selected_bridge(
    sample_repo, tmp_path, monkeypatch, arm
):
    """Actual admission/lease/initialization/runtime/cleanup; synthetic CLI, zero provider use."""
    from tests.execution_helpers import isolated_launch_environment

    isolated_launch_environment(monkeypatch, tmp_path / "isolated-tooling")
    package = case(sample_repo, tmp_path)
    package.update(host="claude", model="fable", arm=arm)
    before = corpus_receipt(sample_repo)
    result = execute(package, "live")
    assert result["status"] == "unavailable", result
    output = Path(package["output_directory"])
    assert (output / "worker-reservation.json").exists(), result
    receipt = json.loads((output / "native-result.json").read_bytes())
    assert receipt["native_id"]
    assert receipt["execution_status"] == "Interrupted"
    assert receipt["mission_complete"] is False
    assert receipt["modules"]["harness.execution_worker"]["path"].startswith(
        receipt["selected_bridge"] + "/harness/"
    )
    admission = json.loads((output / "admission.json").read_bytes())
    with connect(admission["database"]) as conn:
        attempts = conn.execute(
            "SELECT status,worker_pid,child_pid FROM execution_attempts"
        ).fetchall()
    assert len(attempts) == 1
    assert attempts[0]["status"] == "Exited"
    assert attempts[0]["worker_pid"] and attempts[0]["child_pid"]
    assert corpus_receipt(sample_repo) == before


def test_import_origin_guard_refuses_checkout_modules(tmp_path):
    from scripts.knowledge_evaluation.live import module_receipt

    with pytest.raises(ValueError, match="conflicting Harness import"):
        module_receipt(tmp_path)


def test_c_real_controller_query_with_synthetic_authenticated_child(
    operations, tmp_path, monkeypatch
):
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.refresh import refresh
    from tests.test_execution_continuity import child

    adapter, supervisor = make_adapter(operations, tmp_path, monkeypatch, arm="C")
    operation = adapter.operation
    adapter.dispatch(supervisor, "await_assignment", {"timeout": 1})
    principal = child(operation, "al-architect")
    with closing(connect(operation.database)) as conn:
        refresh(conn, current_operator_scope(conn, adapter.root))
    result = adapter.dispatch(principal, "knowledge_query", {"query": "architecture"})
    assert isinstance(result, dict)
    assert principal["thread_id"] in adapter.registered
    with closing(connect(operation.database)) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM operation_audit WHERE request LIKE ?", ("%knowledge_query%",)
            ).fetchone()[0]
            >= 1
        )


@pytest.mark.parametrize("field", ["arm", "host", "role", "participant"])
def test_malformed_identity_fields_refuse_cleanly(sample_repo, tmp_path, field):
    package = case(sample_repo, tmp_path)
    package[field] = []
    with pytest.raises(ValueError, match="bounded text"):
        validate(package)


def test_package_special_file_refused_without_reading(tmp_path):
    from scripts.knowledge_evaluation.package import load

    fifo = tmp_path / "package-pipe"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular case package"):
        load(fifo)


@pytest.mark.parametrize("arm", ["B", "C"])
def test_engineer_actual_synthetic_task_source_query_and_output(
    operations, tmp_path, monkeypatch, arm
):
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.refresh import refresh
    from tests.test_execution_continuity import child

    adapter, supervisor = make_adapter(operations, tmp_path, monkeypatch, arm=arm)
    adapter.package["role"] = "al-engineer"
    adapter.package["specification"]["task_files"] = ["synthetic/source.py"]
    operation = adapter.operation
    adapter.dispatch(supervisor, "await_assignment", {"timeout": 1})
    principal = child(operation, "al-engineer")
    assert principal["writable"] is True
    monkeypatch.setattr(
        "harness.task_environment.prepare",
        lambda *a, **kw: pytest.fail("evaluation registration must not launch automatic setup"),
    )
    monkeypatch.setattr(
        "harness.knowledge.integration.assignment_context",
        lambda *a: pytest.fail("evaluation must not retrieve automatic assignment knowledge"),
    )
    assignment = adapter.dispatch(principal, "await_assignment", {"timeout": 1})
    assert assignment["status"] == "Assigned"
    assert assignment["handoff"]["status"] == "unavailable"
    assert assignment["knowledge_context"]["status"] == "unavailable"
    source = adapter.dispatch(principal, "read_file", {"path": "synthetic/source.py"})
    assert "synthetic_value" in source["text"]
    with closing(connect(operation.database)) as conn:
        if arm == "C":
            refresh(conn, current_operator_scope(conn, adapter.root))
        else:
            assert conn.execute("SELECT count(*) FROM knowledge_snapshots").fetchone()[0] == 0
    if arm == "C":
        assert isinstance(
            adapter.dispatch(principal, "knowledge_query", {"query": "synthetic"}), dict
        )
    else:
        with pytest.raises(ValueError):
            adapter.dispatch(principal, "knowledge_query", {"query": "synthetic"})
    result = adapter.dispatch(
        principal,
        "submit_output",
        {
            "name": "answer.md",
            "content": "synthetic/source.py defines synthetic_value.",
            "expected_graph_revision": assignment["graph_revision"],
        },
    )
    assert result["owner"] == principal["thread_id"] and result["role"] == "al-engineer"
    state = operation.state(operations[5])
    assert state["writers"] == {}
    assert state["integration"]["tasks"]["evaluation"]["status"] == "Idle"
    assert "admission" not in state["integration"]["tasks"]["evaluation"]
    assert state["integration"]["agents"][principal["thread_id"]]["task"] == "evaluation"
    assert operation.processes.records == {}


@pytest.mark.parametrize("scope", [[], [".git"], [".env"], ["missing.py"], ["../escape"]])
def test_engineer_task_scope_refusals(sample_repo, tmp_path, scope):
    package = case(sample_repo, tmp_path)
    package["role"] = "al-engineer"
    package["specification"]["task_files"] = scope
    with pytest.raises(ValueError):
        validate(package)


def metadata_fixture(directory, *, parent=None, role="al-architect", terminal=True, wait=True):
    import uuid
    from datetime import UTC, datetime

    native = directory / "native"
    files = native / "sessions/2026/11/17"
    files.mkdir(parents=True)
    root, child, turn = (str(uuid.uuid4()) for _ in range(3))
    stamp = datetime.now(UTC).isoformat()

    def event(kind, payload):
        return {"timestamp": stamp, "type": kind, "payload": payload}

    def session(tid, source):
        return event(
            "session_meta",
            {
                "id": tid,
                "session_id": tid,
                "cli_version": "0.165.0",
                "source": source,
                "timestamp": stamp,
            },
        )

    root_rows = [
        session(root, "exec"),
        event(
            "response_item",
            {
                "type": "function_call",
                "name": "wait_agent",
                "call_id": "wait-own",
                "arguments": "{}",
            },
        ),
    ]
    if wait:
        root_rows.append(
            event(
                "response_item",
                {
                    "type": "function_call_output",
                    "call_id": "wait-own",
                    "output": json.dumps({"message": "Agent completed", "timed_out": False}),
                },
            )
        )
    child_rows = [
        session(
            child,
            {
                "subagent": {
                    "thread_spawn": {"parent_thread_id": parent or root, "agent_role": role}
                }
            },
        ),
        event("event_msg", {"type": "task_started", "turn_id": turn}),
        event("turn_context", {"turn_id": turn, "model": "gpt-6-astra", "effort": "medium"}),
        event("response_item", {"type": "reasoning", "text": "MUST_NOT_PERSIST_REASONING"}),
    ]
    if terminal:
        child_rows.append(
            event(
                "event_msg",
                {
                    "type": "task_complete",
                    "turn_id": turn,
                    "completed_at": stamp,
                    "last_agent_message": "MUST_NOT_COPY_PROSE",
                },
            )
        )
    for tid, rows in ((root, root_rows), (child, child_rows)):
        (files / ("rollout-" + tid + ".jsonl")).write_text(
            "".join(json.dumps(r) + "\n" for r in rows)
        )
    return native, root, child, files


def test_observer_retains_only_bound_terminal_metadata(tmp_path):
    from scripts.knowledge_evaluation.observer import observe_return

    native, root, child, _ = metadata_fixture(tmp_path)
    receipt = observe_return(native, root, child, "al-architect")
    assert receipt["child_id"] == child
    assert "MUST_NOT" not in json.dumps(receipt)


@pytest.mark.parametrize(
    "fault", ["parent", "role", "terminal", "wait", "turn", "partial", "alias", "oversized"]
)
def test_observer_refuses_unproven_return(tmp_path, fault):
    import uuid

    from scripts.knowledge_evaluation.observer import MAX_RECORD, observe_return

    native, root, child, files = metadata_fixture(
        tmp_path,
        parent=str(uuid.uuid4()) if fault == "parent" else None,
        terminal=fault != "terminal",
        wait=fault != "wait",
    )
    child_file = files / ("rollout-" + child + ".jsonl")
    if fault == "turn":
        rows = [json.loads(line) for line in child_file.read_text().splitlines()]
        rows[-1]["payload"]["turn_id"] = "foreign-turn"
        child_file.write_text("".join(json.dumps(r) + "\n" for r in rows))
    elif fault == "partial":
        child_file.write_text(child_file.read_text().rstrip("\n"))
    elif fault == "alias":
        original = tmp_path / "original-metadata"
        child_file.rename(original)
        child_file.symlink_to(original)
    elif fault == "oversized":
        with child_file.open("a") as stream:
            stream.write(" " * (MAX_RECORD + 1) + "\n")
    with pytest.raises(ValueError):
        observe_return(native, root, child, "al-designer" if fault == "role" else "al-architect")


SYNTHETIC_CONTINUITY_CLI = r"""#!/usr/bin/env python3
import json,sys,os,uuid,time,tomllib
from pathlib import Path
from datetime import datetime,UTC
from urllib.request import Request,urlopen
if '--version' in sys.argv:
    print('codex-cli 0.165.0');raise SystemExit(0)
config={}
for i,v in enumerate(sys.argv):
    if v=='-c':config.update(tomllib.loads(sys.argv[i+1]))
transport=config['mcp_servers']['harness']
headers={'Authorization':transport['http_headers']['Authorization'],'Content-Type':'application/json'}
root,child,turn=[str(uuid.uuid4()) for _ in range(3)]
role=os.environ['EVAL_SYNTHETIC_ROLE'];stage=os.environ['EVAL_SYNTHETIC_STAGE']
folder=Path(os.environ['CODEX_HOME'])/'sessions/2026/11/17';folder.mkdir(parents=True,exist_ok=True)
def emit(tid,kind,payload):
    record={'timestamp':datetime.now(UTC).isoformat(),'type':kind,'payload':payload}
    with (folder/('rollout-'+tid+'.jsonl')).open('a') as stream:
        stream.write(json.dumps(record)+chr(10))
def session(tid,source):
    emit(tid,'session_meta',{'id':tid,'session_id':tid,'cli_version':'0.165.0','source':source,'timestamp':datetime.now(UTC).isoformat()})
session(root,'exec')
print(json.dumps({'type':'thread.started','thread_id':root}),flush=True)
sys.stdin.read();time.sleep(.25)
def request(method,params):
    raw=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
    with urlopen(Request(transport['url'],raw,headers),timeout=30) as response:
        headers['Mcp-Session-Id']=response.headers['Mcp-Session-Id']
        return json.load(response)['result']
request('initialize',{})
def call(tid,name,args):
    value=request('tools/call',{'name':name,'arguments':args,'_meta':{'threadId':tid,'sessionId':tid}})
    assert not value.get('isError'),value
    return json.loads(value['content'][0]['text'])
call(root,'await_assignment',{'timeout':1})
session(child,{'subagent':{'thread_spawn':{'parent_thread_id':root,'agent_role':role}}})
emit(child,'event_msg',{'type':'task_started','turn_id':turn})
emit(child,'turn_context',{'turn_id':turn,'model':'gpt-6-astra','effort':'medium'})
assignment=call(child,'await_assignment',{'timeout':1})
if stage=='recipient':
    report=call(child,'read_file',{'path':'evaluation:donor-report'})
    assert 'PUBLIC_SYNTHETIC_DONOR' in report['text']
    if os.environ['EVAL_SYNTHETIC_ARM']=='C':
        result=call(child,'knowledge_query',{'query':'PUBLIC_SYNTHETIC_DONOR'})
        assert any('PUBLIC_SYNTHETIC_DONOR' in a['text'] for a in result['assertions']),result
else:
    call(child,'read_file',{'path':'demo/export.py'})
content='PUBLIC_SYNTHETIC_DONOR demo/export.py defines export_rows.'
if stage!='donor':content='Recipient explicitly used the exact public donor report.'
call(child,'submit_output',{'name':'answer.md','content':content,
    'expected_graph_revision':assignment['graph_revision']})
emit(child,'event_msg',{'type':'task_complete','turn_id':turn,'completed_at':datetime.now(UTC).isoformat(),'last_agent_message':'IGNORED_FINAL_PROSE'})
if stage=='donor':
    emit(root,'response_item',{'type':'function_call','name':'wait_agent','call_id':'wait-synthetic','arguments':'{}'})
    waited=json.dumps({'message':'Agent completed','timed_out':False})
    emit(root,'response_item',{'type':'function_call_output',
        'call_id':'wait-synthetic','output':waited})
    completion=call(root,'read_file',{'path':'evaluation:complete-donor'})
    assert completion['agent_status']=='Completed'
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':0,'output_tokens':0}}),flush=True)
"""


def existing_binding(package, admission):
    from harness.controller import HarnessController
    from scripts.knowledge_evaluation.package import canonical, sha

    with closing(connect(admission["database"])) as conn:
        controller = HarnessController(conn)
        investigation = controller.workspace(admission["task_id"]).latest_checkpoint(
            "investigation"
        )
        return {
            "database": admission["database"],
            "specs": admission["specs"],
            "task_id": admission["task_id"],
            "task_request_sha256": sha(controller.get_task(admission["task_id"]).request.encode()),
            "investigation_sha256": sha(canonical(investigation.payload).encode()),
            "approval_id": admission["approval"]["id"],
            "approval_content_hash": admission["approval"]["content_hash"],
            "knowledge_binding": None,
        }


def stage_wrapper(package, action, *, existing=None, donor=None):
    return {
        "schema_version": 1,
        "action": action,
        "package": package,
        "al_task_id": "evaluation-" + action,
        "task_files": ["demo"],
        "existing": existing,
        "donor": donor,
    }


@pytest.mark.parametrize("arm", ["B", "C"])
def test_full_synthetic_native_continuity_and_real_reapproval(
    sample_repo, tmp_path, monkeypatch, arm
):
    from scripts.knowledge_evaluation.continuity import knowledge_empty
    from scripts.knowledge_evaluation.package import sha
    from scripts.knowledge_evaluation.stages import execute_stage
    from tests.execution_helpers import isolated_launch_environment

    isolated_launch_environment(monkeypatch, tmp_path / "continuity-tooling")
    binary = tmp_path / "continuity-bin"
    binary.mkdir()
    executable = binary / "codex"
    executable.write_text(SYNTHETIC_CONTINUITY_CLI)
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("EVAL_SYNTHETIC_ROLE", "al-architect")
    monkeypatch.setenv("EVAL_SYNTHETIC_STAGE", "donor")
    monkeypatch.setenv("EVAL_SYNTHETIC_ARM", arm)
    package = case(sample_repo, tmp_path)
    package.update(arm=arm, participant="donor")
    before = corpus_receipt(sample_repo)
    donor_result = execute_stage(stage_wrapper(package, "donor"), "live")
    assert donor_result["status"] == "observed", donor_result
    output = Path(package["output_directory"])
    donor_admission = json.loads((output / "admission.json").read_bytes())
    completion = json.loads((output / "producer-completion.json").read_bytes())
    assert completion["agent_status"] == "Completed"
    if arm == "B":
        assert completion["B_ingestion_refusals"]
    else:
        assert completion["knowledge_contributions"][0]["status"] == "published"
    references = {
        name: {"path": str(output / filename), "sha256": sha((output / filename).read_bytes())}
        for name, filename in (
            ("admission", "admission.json"),
            ("completion", "producer-completion.json"),
        )
    }
    recipient = copy.deepcopy(package)
    recipient.update(
        participant="recipient",
        output_directory=str(tmp_path / "recipient-output"),
        role="al-engineer",
        model="gpt-6.1-sol",
    )
    recipient["specification"]["task_files"] = ["demo"]
    recipient["specification"]["request"] = (
        "Use the authorized earlier report in a later read-only demo/export.py task."
    )
    # Keep concrete supplied answers for newly observed investigation questions.
    recipient["specification"]["answers"].update(
        {
            q.text: "Read-only later task in this exact frozen project."
            for q in analyze(sample_repo, recipient["specification"]["request"]).questions
        }
    )
    binding = existing_binding(package, donor_admission)
    monkeypatch.setenv("EVAL_SYNTHETIC_ROLE", "al-engineer")
    monkeypatch.setenv("EVAL_SYNTHETIC_STAGE", "recipient")
    result = execute_stage(
        stage_wrapper(recipient, "recipient", existing=binding, donor=references), "live"
    )
    assert result["status"] == "observed", result
    admission = json.loads((Path(recipient["output_directory"]) / "admission.json").read_bytes())
    assert admission["task_id"] == donor_admission["task_id"]
    assert admission["approval"]["id"] != donor_admission["approval"]["id"]
    assert admission["approval"]["spec_version"] > donor_admission["approval"]["spec_version"]
    assert result["execution_id"] != donor_result["execution_id"]
    assert result["native_id"] != donor_result["native_id"]
    assert result["adapter"]["answers"][0]["role"] == "al-engineer"
    with closing(connect(admission["database"])) as conn:
        attempts = conn.execute("SELECT status FROM execution_attempts").fetchall()
        assert len(attempts) == 2 and all(row[0] == "Exited" for row in attempts)
        if arm == "B":
            knowledge_empty(conn)
        else:
            row = conn.execute("SELECT task_id FROM knowledge_contributions").fetchone()
            assert row is not None and row[0] == "evaluation-donor"
    assert corpus_receipt(sample_repo) == before
    with pytest.raises(ValueError):
        execute(recipient, "validate")  # Original single-attempt route remains strict.


def test_existing_unapproved_task_ordinary_adoption(sample_repo, tmp_path):
    from harness.controller import HarnessController
    from scripts.knowledge_evaluation.package import canonical, sha
    from scripts.knowledge_evaluation.stages import execute_stage

    package = case(sample_repo, tmp_path)
    state = Path(package["state_directory"])
    state.mkdir()
    database = state / "harness.db"
    with closing(connect(database)) as conn:
        controller = HarnessController(conn)
        task = controller.create_task("Public read-only operator-prepared demonstration project")
        investigation = controller.record_investigation(
            task.id, analyze(sample_repo, package["specification"]["request"])
        )
        binding = {
            "database": str(database),
            "specs": str(state / "specs"),
            "task_id": task.id,
            "task_request_sha256": sha(task.request.encode()),
            "investigation_sha256": sha(canonical(investigation.payload).encode()),
            "approval_id": None,
            "approval_content_hash": None,
            "knowledge_binding": None,
        }
    wrapper = stage_wrapper(package, "adopt", existing=binding)
    forged = copy.deepcopy(wrapper)
    forged["existing"]["task_request_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="request binding"):
        execute_stage(forged, "validate")
    assert execute_stage(wrapper, "prepare")["status"] == "prepared"
    admission = json.loads((Path(package["output_directory"]) / "admission.json").read_bytes())
    assert admission["task_id"] == task.id and admission["approval"]["id"]
    assert admission["new_user_approval_event"] is False
    with pytest.raises(ValueError):
        execute(package, "validate")


def test_b_ingestion_restriction_is_before_original_entry(monkeypatch):
    from unittest.mock import Mock

    from harness.knowledge import contributions
    from scripts.knowledge_evaluation.lifecycle import ingestion_boundary

    original = Mock(side_effect=AssertionError("original ingestion body must not run"))
    monkeypatch.setattr(contributions, "ingest", original)
    refusals = []
    with ingestion_boundary("B", refusals):
        with pytest.raises(ValueError, match="denied at entry"):
            contributions.ingest(None, None, None, None, None)
    assert original.call_count == 0 and len(refusals) == 1
    with ingestion_boundary("C", []):
        assert contributions.ingest is original


def test_finalization_refuses_task_hash_yield_and_stale_graph(operations, tmp_path, monkeypatch):
    """Synthetic predicate units over real initialized operation/outbox; no live status claimed."""
    from scripts.knowledge_evaluation.lifecycle import complete
    from tests.test_execution_continuity import child

    adapter, root = make_adapter(operations, tmp_path, monkeypatch)
    adapter.stage = {
        "action": "donor",
        "al_task_id": "donor-unit",
        "task_files": ["synthetic/source.py"],
    }
    operation = adapter.operation
    adapter.dispatch(root, "await_assignment", {"timeout": 1})
    principal = child(operation, "al-architect")
    assignment = adapter.dispatch(principal, "await_assignment", {"timeout": 1})
    adapter.dispatch(
        principal,
        "submit_output",
        {
            "name": "answer.md",
            "content": "Synthetic source-bound report.",
            "expected_graph_revision": assignment["graph_revision"],
        },
    )
    monkeypatch.setattr("scripts.knowledge_evaluation.lifecycle.observe_return", lambda *a: {})
    original_authority = operation.authority
    yield_pending = False

    def authority(*args, **kwargs):
        execution, state = original_authority(*args, **kwargs)
        state = copy.deepcopy(state)
        if yield_pending:
            state["integration"]["agents"][principal["thread_id"]]["yield_required"] = True
        return dict(execution, status="Running"), state

    monkeypatch.setattr(operation, "authority", authority)
    adapter.stage["al_task_id"] = "foreign-task"
    with pytest.raises(ValueError, match="lifecycle binding"):
        complete(adapter, root)
    adapter.stage["al_task_id"] = "donor-unit"
    original_hash = adapter.answers[0]["artifact"]["sha256"]
    adapter.answers[0]["artifact"]["sha256"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="report provenance"):
        complete(adapter, root)
    adapter.answers[0]["artifact"]["sha256"] = original_hash
    yield_pending = True
    with pytest.raises(ValueError, match="lifecycle binding"):
        complete(adapter, root)
    yield_pending = False
    _, state = operation.authority(operations[2], root)
    adapter.report_registration(
        "task-register",
        "additional-task.json",
        {
            "id": "additional",
            "title": "A distinct synthetic read-only scope",
            "files": ["docs"],
            "criteria": operation.envelope["task_package"]["criteria"],
        },
        state,
    )
    with pytest.raises(ValueError, match="graph revision stale"):
        complete(adapter, root)
    current = operation.state(operations[5])
    assert current["integration"]["agents"][principal["thread_id"]]["status"] == "Idle"
    assert current["writers"] == {}
