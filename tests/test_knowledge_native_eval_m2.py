"""Public synthetic method qualification; never provider or acceptance evidence."""

import json
from pathlib import Path

import pytest

from harness.execution_mcp import MCPConnection
from scripts.knowledge_evaluation.live import connection_class
from tests.test_knowledge_native_eval import make_adapter
from tests.test_knowledge_native_eval import operations as initialized_operations


@pytest.fixture
def operations(controller, monkeypatch, launched, request):
    yield from initialized_operations.__wrapped__(controller, monkeypatch, launched, request)


def rpc(connection, name, arguments=None):
    return connection.request(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": {} if arguments is None else arguments},
        }
    )


def connection(adapter, principal):
    conn = connection_class(MCPConnection)(lambda _: principal, adapter.dispatch)
    conn.request({"jsonrpc": "2.0", "method": "initialize"})
    return conn


def test_public_v1_repro_predispatch_failures_are_not_counted(operations, tmp_path, monkeypatch):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    conn = connection(adapter, principal)
    assert rpc(conn, "unknown")["isError"]
    assert rpc(conn, "read_file", {"unexpected": True})["isError"]
    assert adapter.calls == 0 and adapter.delivered == 0


def test_public_v1_repro_data_can_consume_every_final_call(operations, tmp_path, monkeypatch):
    adapter, principal = make_adapter(operations, tmp_path, monkeypatch)
    conn = connection(adapter, principal)
    for _ in range(30):
        assert not rpc(conn, "await_assignment", {"timeout": 1})["isError"]
    assert rpc(conn, "await_assignment", {"timeout": 1})["isError"]
    assert adapter.calls == 30


def test_public_v1_repro_committed_output_can_lose_ack(operations, tmp_path, monkeypatch):
    from tests.test_execution_continuity import child

    adapter, root = make_adapter(operations, tmp_path, monkeypatch)
    adapter.dispatch(root, "await_assignment", {"timeout": 1})
    principal = child(adapter.operation, "al-architect")
    assignment = adapter.dispatch(principal, "await_assignment", {"timeout": 1})
    adapter.delivered = 98304
    with pytest.raises(ValueError):
        adapter.dispatch(
            principal,
            "submit_output",
            {
                "name": "answer.md",
                "content": "Public partial answer.",
                "expected_graph_revision": assignment["graph_revision"],
            },
        )
    assert len(adapter.answers) == 1
    assert adapter.operation.artifacts.outputs()[principal["thread_id"]]["answer.md"]
    assert json.dumps(adapter.receipt())


def m2_case(root, tmp_path):
    from scripts.knowledge_evaluation.method import contract
    from tests.test_knowledge_native_eval import case

    package = case(root, tmp_path)
    package.update(schema_version=2, method=contract())
    return package


def m2_adapter(operations, tmp_path, monkeypatch, arm="B", donor=False):
    from scripts.knowledge_evaluation.adapter import EvaluatorAdapter
    from scripts.knowledge_evaluation.method import contract

    old, root = make_adapter(operations, tmp_path, monkeypatch, arm)
    old.package.update(schema_version=2, method=contract())
    adapter = EvaluatorAdapter(old.operation, old.package, {"action": "donor"} if donor else None)
    return adapter, root


def authenticated_pair(adapter, root):
    from tests.test_execution_continuity import child

    root_conn = connection(adapter, root)
    assert not rpc(root_conn, "await_assignment", {"timeout": 1})["isError"]
    principal = child(adapter.operation, "al-architect")
    conn = connection(adapter, principal)
    assignment = rpc(conn, "await_assignment", {"timeout": 1})
    assert not assignment["isError"], assignment
    return conn, root_conn, principal


def submit_partial(conn):
    assignment = rpc(conn, "await_assignment", {"timeout": 1})
    assert not assignment["isError"], assignment
    revision = json.loads(assignment["content"][0]["text"])["graph_revision"]
    return rpc(
        conn,
        "submit_output",
        {
            "name": "answer.md",
            "content": "synthetic/source.py defines synthetic_value; other impacts unknown.",
            "expected_graph_revision": revision,
        },
    )


@pytest.mark.parametrize("arm", ["B", "C"])
@pytest.mark.parametrize("limit", ["calls", "bytes"])
def test_m2_exhaustion_preserves_actual_partial_output(
    operations, tmp_path, monkeypatch, arm, limit
):
    adapter, root = m2_adapter(operations, tmp_path, monkeypatch, arm)
    conn, _, principal = authenticated_pair(adapter, root)
    if limit == "calls":
        for _ in range(25):
            assert not rpc(conn, "read_file", {"path": "synthetic/source.py"})["isError"]
        result = rpc(conn, "read_file", {"path": "synthetic/source.py"})
    else:
        monkeypatch.setattr(adapter, "read", lambda *a: {"text": "x" * 81921})
        result = rpc(conn, "read_file", {"path": "synthetic/source.py"})
    assert result["isError"] and "Stop retrieval" in result["content"][0]["text"]
    assert not submit_partial(conn)["isError"]
    receipt = adapter.receipt()
    assert receipt["output_lifecycle"]["output_committed"]
    assert receipt["output_lifecycle"]["acknowledgment_delivered"]
    assert adapter.operation.artifacts.outputs()[principal["thread_id"]]["answer.md"]
    assert adapter.budget.attempted <= 30 and adapter.delivered <= 98304
    assert adapter.budget.internal_operations == 2
    assert adapter.budget.admitted + adapter.budget.denied == adapter.budget.attempted


def test_m2_counts_authentication_validation_unknown_and_overlimit(
    operations, tmp_path, monkeypatch
):
    adapter, principal = m2_adapter(operations, tmp_path, monkeypatch)
    conn = connection(adapter, principal)
    conn.resolve_principal = lambda _: (_ for _ in ()).throw(ValueError("Public auth rejection"))
    assert rpc(conn, "await_assignment")["isError"]
    conn.resolve_principal = lambda _: principal
    assert rpc(conn, "read_file", {"unexpected": True})["isError"]
    for _ in range(29):
        assert rpc(conn, "unknown")["isError"]
    assert adapter.budget.attempted == 31 and adapter.budget.denied == 31
    assert adapter.budget.events[-1]["outcome"] == "over-limit-attempt"
    assert adapter.budget.admitted == 0
    assert adapter.budget.delivered <= 512
    assert "_meta" not in json.dumps(adapter.budget.receipt())


def test_m2_repeated_listing_and_malformed_envelopes(operations, tmp_path, monkeypatch):
    adapter, root = m2_adapter(operations, tmp_path, monkeypatch)
    conn = connection(adapter, root)
    listing = conn.request({"jsonrpc": "2.0", "method": "tools/list"})
    assert {t["name"] for t in listing["tools"]} == adapter.allowed
    size = len(json.dumps(listing).encode())
    assert adapter.delivered == size
    conn.request({"jsonrpc": "2.0", "method": "tools/list"})
    assert adapter.delivered == 2 * size and adapter.budget.listings == 2
    assert adapter.budget.attempted == 0
    with pytest.raises(ValueError):
        conn.request({"jsonrpc": "2.0", "method": "tools/call", "params": []})
    assert adapter.budget.attempted == 0
    assert adapter.budget.events[-1]["kind"] == "transport-diagnostic"


@pytest.mark.parametrize("failure", ["after-store", "ack-bytes", "stale"])
def test_m2_reconciles_own_output_without_resubmission(operations, tmp_path, monkeypatch, failure):
    adapter, root = m2_adapter(operations, tmp_path, monkeypatch)
    conn, _, principal = authenticated_pair(adapter, root)
    assignment = rpc(conn, "await_assignment", {"timeout": 1})
    revision = json.loads(assignment["content"][0]["text"])["graph_revision"]
    original = adapter.operation.dispatch
    submissions = []

    def dispatch(who, name, args):
        if name == "submit_output":
            submissions.append(name)
            result = original(who, name, args)
            if failure == "after-store":
                raise ValueError("Synthetic ambiguity after publication")
            return result
        return original(who, name, args)

    monkeypatch.setattr(adapter.operation, "dispatch", dispatch)
    if failure == "ack-bytes":
        adapter.budget.delivered = 98304 - 512
    result = rpc(
        conn,
        "submit_output",
        {
            "name": "answer.md",
            "content": "Public immutable answer.",
            "expected_graph_revision": revision + (1 if failure == "stale" else 0),
        },
    )
    assert result["isError"]
    assert adapter.output_state["output_committed"] is (failure != "stale")
    assert not adapter.output_state["acknowledgment_delivered"]
    assert rpc(
        conn,
        "submit_output",
        {
            "name": "answer.md",
            "content": "Public immutable answer.",
            "expected_graph_revision": revision,
        },
    )["isError"]
    assert submissions == ["submit_output"]
    if failure != "stale":
        assert adapter.answers[0]["principal"] == principal["thread_id"]
        assert "text" not in adapter.answers[0]


@pytest.mark.parametrize("fault", ["version", "reserve", "capability", "help", "total", "profile"])
def test_m2_closed_contract_refuses_before_admission(sample_repo, tmp_path, monkeypatch, fault):
    from scripts.knowledge_native_eval import execute

    package = m2_case(sample_repo, tmp_path)
    if fault == "version":
        package["schema_version"] = 3
    elif fault == "total":
        package["budgets"]["tool_calls"] = 31
    elif fault == "profile":
        package["role"] = "al-scout"
    else:
        field = {
            "reserve": "reserve",
            "capability": "capability_profile",
            "help": "tool_help_sha256",
        }[fault]
        package["method"][field] = "unsupported"
    monkeypatch.setattr(
        "scripts.knowledge_native_eval.admit", lambda *a, **kw: pytest.fail("admitted")
    )
    with pytest.raises(ValueError):
        execute(package, "prepare")


def test_m2_validation_and_generic_instruction_parity(sample_repo, tmp_path):
    from scripts.knowledge_evaluation.live import prompt
    from scripts.knowledge_evaluation.method import INSTRUCTIONS, contract
    from scripts.knowledge_native_eval import execute

    package = m2_case(sample_repo, tmp_path)
    assert execute(package, "validate")["status"] == "validated"
    baseline = prompt(package)
    package["arm"] = "C"
    assert prompt(package) == baseline and INSTRUCTIONS in baseline
    assert package["method"] == contract()


def test_cross_method_donor_refused_before_store_access(sample_repo, tmp_path):
    from scripts.knowledge_evaluation.continuity import donor_capability
    from scripts.knowledge_evaluation.package import sha

    package = m2_case(sample_repo, tmp_path)
    refs = {}
    for name in ("admission", "completion"):
        path = tmp_path / (name + ".json")
        path.write_text("{}")
        refs[name] = {"path": str(path), "sha256": sha(path.read_bytes())}
    with pytest.raises(ValueError, match="cross-method"):
        donor_capability(package, refs)


@pytest.mark.parametrize("arm", ["B", "C"])
def test_m2_synthetic_donor_exhaustion_keeps_observation_unavailable(
    sample_repo, tmp_path, monkeypatch, arm
):
    """Real admission/bridge/lease/NativeRuntime/authority; only host executable is synthetic."""
    from scripts.knowledge_evaluation.method import contract
    from tests import test_knowledge_native_eval as legacy

    original_case = legacy.case

    def case(root, directory):
        package = original_case(root, directory)
        package.update(schema_version=2, method=contract())
        return package

    exhaust = """
while True:
    result=request('tools/call',{'name':'read_file','arguments':{'path':'demo/export.py'},
        '_meta':{'threadId':child,'sessionId':child}})
    if result.get('isError'):
        assert 'Stop retrieval' in result['content'][0]['text'],result
        break
assignment=call(child,'await_assignment',{'timeout':1})
"""
    usage = """
counts={'input_tokens':100,'cached_input_tokens':20,'output_tokens':30,
        'reasoning_output_tokens':5,'total_tokens':130}
emit(child,'event_msg',{'type':'token_count','info':{
    'total_token_usage':counts,'last_token_usage':counts}})
"""
    script = (
        legacy.SYNTHETIC_CONTINUITY_CLI.replace(
            "'model':'gpt-6-astra'",
            "'model':('gpt-6.1-sol' if role=='al-engineer' else 'gpt-6-astra')",
        )
        .replace("content='PUBLIC_SYNTHETIC_DONOR", exhaust + "\ncontent='PUBLIC_SYNTHETIC_DONOR")
        .replace(
            "emit(child,'event_msg',{'type':'task_complete'",
            usage + "\nemit(child,'event_msg',{'type':'task_complete'",
        )
    )
    monkeypatch.setattr(legacy, "case", case)
    monkeypatch.setattr(legacy, "SYNTHETIC_CONTINUITY_CLI", script)
    legacy.test_synthetic_donor_refuses_unqualified_completion(
        sample_repo, tmp_path, monkeypatch, arm
    )
    result = json.loads((tmp_path / "evaluation-output" / "native-result.json").read_bytes())
    ledger = result["adapter"]["accounting"]
    assert ledger["attempted_tools_call"] == 30
    assert ledger["delivered_bytes"] <= 98304
    assert result["adapter"]["output_lifecycle"]["acknowledgment_delivered"]
    assert len(result["settlement"]["attempts"]) == 1
    assert result["settlement"]["attempts"][0]["status"] == "Exited"
    assert result["settlement"]["attempts"][0]["closed_context"]
    assert result["evaluator_hashes"] and result["prompt_sha256"]
    assert result["usage"]["children"][0]["status"] == "unavailable"
    assert result["usage"]["children"][0]["turn_generation_delta"] is None
    assert result["usage"]["answer_token_compliance"] == "UNKNOWN"
    assert result["child_returns"][0]["status"] == "unavailable"
    assert not (tmp_path / "recipient-output").exists()


def test_public_exact_pem_canary_reader_boundaries(sample_repo, tmp_path):
    """Exact benign placement only; filename visibility and subtree ACLs are not promised."""
    from contextlib import closing

    from harness.analyzer import analyze
    from harness.db import connect
    from harness.decision_suggest import suggest
    from harness.knowledge.models import ContextRequest, RefreshRequest
    from harness.knowledge.policy import current_operator_scope
    from harness.knowledge.query import explain, query
    from harness.knowledge.refresh import refresh
    from harness.knowledge.sources import eligible, read_source
    from harness.registry import DecisionRegistry
    from harness.repository_audit import audit
    from tests.sample_repo import git_init

    marker = "PUBLIC_BENIGN_PEM_CANARY_8273"
    private = sample_repo / "private"
    private.mkdir()
    (private / "canary.pem").write_text(marker)
    # A supported extension in the same subtree remains eligible: no invented private/** ACL.
    (private / "notes.md").write_text("Public ordinary text.")
    git_init(sample_repo)
    assert not eligible("private/canary.pem") and eligible("private/notes.md")
    with pytest.raises(ValueError, match="excluded"):
        read_source(sample_repo, "private/canary.pem", 1024)
    assert b"Public ordinary" in read_source(sample_repo, "private/notes.md", 1024)
    baseline = [
        analyze(sample_repo, "Explain demo/export.py").to_dict(),
        audit(sample_repo, "Explain demo/export.py"),
        str(suggest("Explain demo/export.py", [], [sample_repo])),
    ]
    registry = DecisionRegistry(sample_repo / "docs/decisions")
    baseline.append(str([(r, registry.history(r.id)) for r in registry.records()]))
    assert marker not in json.dumps(baseline)
    with closing(connect(tmp_path / "canary.db")) as conn:
        for commit in (None, "HEAD"):
            scope = current_operator_scope(conn, sample_repo)
            refresh(conn, scope, RefreshRequest(commit=commit))
            scope = current_operator_scope(conn, sample_repo)
            bundle = query(conn, scope, ContextRequest(marker)).to_dict()
            assert marker not in json.dumps(bundle.get("assertions", []))
            for assertion in bundle.get("assertions", []):
                assert marker not in json.dumps(
                    explain(conn, scope, bundle["bundle_hash"], assertion["assertion_id"])
                )


def test_m2_assignment_cannot_borrow_final_bytes(operations, tmp_path, monkeypatch):
    adapter, root = m2_adapter(operations, tmp_path, monkeypatch)
    conn = connection(adapter, root)
    original = adapter.operation.dispatch
    monkeypatch.setattr(
        adapter.operation,
        "dispatch",
        lambda *a, **kw: {"status": "ReadOnly", "padding": "x" * 81921},
    )
    result = rpc(conn, "await_assignment", {"timeout": 1})
    assert result["isError"] and adapter.budget.phase == "completion"
    assert adapter.budget.delivered < 512
    monkeypatch.setattr(adapter.operation, "dispatch", original)
    assert not rpc(conn, "await_assignment", {"timeout": 1})["isError"]


def test_m2_unknown_reconciliation_is_not_output_absence(operations, tmp_path, monkeypatch):
    adapter, root = m2_adapter(operations, tmp_path, monkeypatch)
    conn, _, _ = authenticated_pair(adapter, root)
    monkeypatch.setattr(
        "scripts.knowledge_evaluation.lifecycle.reconcile_output",
        lambda *a: (_ for _ in ()).throw(ValueError("Synthetic index unavailable")),
    )
    assert submit_partial(conn)["isError"]
    assert adapter.output_state["output_committed"] is None
    assert adapter.output_attempted


def test_m2_contract_rejects_float_reserve(sample_repo, tmp_path):
    from scripts.knowledge_evaluation.package import validate

    package = m2_case(sample_repo, tmp_path)
    package["method"]["reserve"]["final_calls"] = 3.0
    with pytest.raises(ValueError, match="exact supported"):
        validate(package)


def test_m2_control_overflow_and_wall_expiration_preserve_failure(
    operations, tmp_path, monkeypatch
):
    adapter, root = m2_adapter(operations, tmp_path, monkeypatch)
    conn = connection(adapter, root)
    adapter.budget.phase = "completion"
    monkeypatch.setattr(
        adapter.operation,
        "dispatch",
        lambda *a, **kw: {"status": "ReadOnly", "padding": "x" * 98304},
    )
    assert rpc(conn, "await_assignment")["isError"]
    assert adapter.budget.phase == "terminal"
    assert adapter.budget.events[-1]["outcome"] == "control-budget-exhausted"
    adapter.started -= 240
    assert rpc(conn, "await_assignment")["isError"]
    assert adapter.budget.attempted == 2


def test_m2_listing_exhaustion_preserves_protocol_and_counts(operations, tmp_path, monkeypatch):
    adapter, root = m2_adapter(operations, tmp_path, monkeypatch)
    conn = connection(adapter, root)
    adapter.budget.delivered = 81920
    first = conn.request({"jsonrpc": "2.0", "method": "tools/list"})
    assert first["tools"] == [] and "Stop retrieval" in first["_meta"]["evaluation_error"]
    assert adapter.budget.delivered == 81920 + len(json.dumps(first).encode())
    for _ in range(5):
        response = conn.request({"jsonrpc": "2.0", "method": "tools/list"})
        assert response["tools"] == []
    assert adapter.budget.listings == 6 and adapter.budget.attempted == 0
    assert adapter.budget.errors_delivered <= 512
    assert adapter.delivered <= 98304


@pytest.mark.parametrize("stage", [None, {"action": "donor"}, {"action": "recipient"}])
@pytest.mark.parametrize("arm", ["B", "C"])
def test_review_repro_actual_child_packet_contains_m2_policy(stage, arm):
    from scripts.knowledge_evaluation.live import prompt
    from scripts.knowledge_evaluation.method import INSTRUCTIONS

    package = {
        "schema_version": 2,
        "role": "al-architect",
        "arm": arm,
        "prompt": "Public synthetic architecture question.",
    }
    from scripts.knowledge_evaluation.package import sha

    rendered = prompt(package, stage)
    child_packet = rendered.partition("Child instructions: ")[2]
    assert rendered == prompt(dict(package, arm="C" if arm == "B" else "B"), stage)
    v1_hashes = (
        "5a28141a5b76446880b1b2552e576cddd5dcaf3443d57180866a814986f30de8",
        "2f759edc9b6b58ea996ff1828d3ee51ba9692327c220461c36d4c9cc0dbcc9a5",
        "b65fffa48863dab516e7311394ffda5734977f65e91b9990451302a400bf3b9e",
    )
    index = {None: 0, "donor": 1, "recipient": 2}[stage["action"] if stage else None]
    assert sha(prompt(dict(package, schema_version=1), stage).encode()) == v1_hashes[index]
    assert ("evaluation:donor-report" in child_packet) == bool(
        stage and stage["action"] == "recipient"
    )
    assert INSTRUCTIONS in child_packet
    assert "Public synthetic architecture question." in child_packet
    assert "After the real child returns" not in child_packet


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("regular_package", [False, True])
def test_selected_worker_rejects_ambient_editable_source(
    tmp_path, monkeypatch, schema, regular_package
):
    import shutil
    import subprocess
    import venv
    from pathlib import Path

    from scripts import knowledge_native_eval as runner
    from scripts.knowledge_evaluation.worker import hashes

    engine, foreign, bridge = (tmp_path / n for n in ("engine", "foreign", "bridge"))
    selected = engine / "scripts/knowledge_evaluation"
    stale = foreign / "scripts/knowledge_evaluation"
    selected.mkdir(parents=True)
    stale.mkdir(parents=True)
    bridge.mkdir()
    if regular_package:
        (foreign / "scripts/__init__.py").write_text("")
    (stale / "__init__.py").write_text("")
    (stale / "live.py").write_text("raise RuntimeError('foreign evaluator selected')")
    (selected / "__init__.py").write_text("")
    (engine / "scripts/knowledge_native_eval.py").write_text("# public fixture entrypoint")
    shutil.copyfile(
        Path(runner.__file__).parent / "knowledge_evaluation/worker.py", selected / "worker.py"
    )
    (selected / "live.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "def run(package, admission, bridge, *, evaluator_source):\n"
        " from .worker import verify_source\n"
        " modules=verify_source(*evaluator_source)\n"
        " print(json.dumps({'schema':json.loads(Path(package).read_text())['schema_version'],"
        "'live':__file__,'first':sys.path[0],'modules':modules}))\n"
    )
    venv_root = tmp_path / "environment"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(venv_root)
    interpreter = venv_root / "bin/python"
    site = next((venv_root / "lib").glob("python*/site-packages"))
    (site / "public-foreign.pth").write_text(str(foreign) + "\n")
    (tmp_path / "package.json").write_text(json.dumps({"schema_version": schema}))
    (tmp_path / "isolation.json").write_text(json.dumps({"adapter_hashes": hashes(engine)}))
    monkeypatch.setattr(runner, "_engine", engine)
    monkeypatch.setattr(runner.sys, "executable", str(interpreter))
    result = subprocess.run(
        runner.worker_command(bridge, tmp_path), capture_output=True, check=True
    )
    receipt = json.loads(result.stdout)
    assert receipt["schema"] == schema
    assert Path(receipt["live"]).resolve() == selected / "live.py"
    assert receipt["first"] == str(bridge)
    assert "scripts.knowledge_evaluation.live" in receipt["modules"]


@pytest.mark.parametrize("preload", ["namespace", "module", "changed-hash"])
def test_selected_worker_refuses_foreign_preload(tmp_path, preload):
    import subprocess
    import sys
    from pathlib import Path

    from scripts.knowledge_evaluation import worker

    engine = Path(worker.__file__).resolve().parents[2]
    program = (
        "import importlib.util,sys,types;from pathlib import Path;"
        "spec=importlib.util.spec_from_file_location('selector',sys.argv[1]);"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "engine=Path(sys.argv[2]);expected=m.hashes(engine);"
    )
    if preload == "namespace":
        program += (
            "n=types.ModuleType('scripts');n.__path__=[sys.argv[3]];sys.modules['scripts']=n;"
        )
    elif preload == "module":
        program += (
            "n=types.ModuleType('scripts.knowledge_evaluation.live');"
            "n.__file__=sys.argv[3]+'/live.py';sys.modules[n.__name__]=n;"
        )
    else:
        program += "expected['knowledge_evaluation/live.py']='0'*64;"
    program += "m.select_source(engine,expected)"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, worker.__file__, str(engine), str(tmp_path)],
        capture_output=True,
    )
    assert result.returncode != 0
    assert b"ValueError" in result.stderr
    assert (
        b"foreign preimported evaluator" in result.stderr
        or b"hash binding changed" in result.stderr
    )


@pytest.mark.parametrize("arm", ["B", "C"])
def test_r2_ordinary_read_paging_and_accounting(operations, tmp_path, monkeypatch, arm):
    from scripts.knowledge_evaluation.package import sha

    adapter, root = m2_adapter(operations, tmp_path, monkeypatch, arm)
    conn, _, _ = authenticated_pair(adapter, root)
    path = adapter.root / "synthetic/Makefile"
    raw = b"all: public-build\n"
    path.write_bytes(raw)
    # Unit adapter fixture freezes membership separately; real corpus negatives remain below.
    monkeypatch.setattr(
        "scripts.knowledge_evaluation.adapter.git", lambda *a: b"synthetic/Makefile\0"
    )
    first = rpc(conn, "read_file", {"path": "synthetic/Makefile", "offset": 5, "limit": 3})
    assert not first["isError"], first
    page = json.loads(first["content"][0]["text"])
    assert page == {
        "text": raw[5:8].decode(),
        "offset": 5,
        "next_offset": 8,
        "truncated": True,
        "source_sha256": sha(raw),
    }
    for name in ("synthetic/missing", "synthetic//Makefile", ".npmrc"):
        assert rpc(conn, "read_file", {"path": name})["isError"]
    path.write_bytes(b"public\n" + b"x" * 200 + b'api_key="publiccanary123"')
    assert rpc(conn, "read_file", {"path": "synthetic/Makefile", "limit": 3})["isError"]
    assert adapter.budget.admitted + adapter.budget.denied == adapter.budget.attempted == 7
    assert adapter.budget.attempted <= 30 and adapter.delivered <= 98304


def test_r2_scope_formats_legacy_refusal_and_setup(sample_repo, tmp_path):
    from scripts.knowledge_evaluation.method import contract, validate_method
    from scripts.knowledge_evaluation.package import corpus_receipt, task_files
    from tests.sample_repo import git_init

    unit = sample_repo / "public-unit"
    unit.mkdir()
    (unit / "Makefile").write_text("all: build\n")
    git_init(sample_repo)
    package = m2_case(sample_repo, tmp_path)
    package["specification"]["task_files"] = ["public-unit/Makefile"]
    assert task_files(package) == ["public-unit/Makefile"]
    package["schema_version"] = 1
    with pytest.raises(ValueError, match="protected/secret"):
        task_files(package)
    package.update(schema_version=2, method=dict(contract(), id="knowledge-native-m2-r1"))
    with pytest.raises(ValueError, match="exact supported"):
        validate_method(package)
    with pytest.raises(ValueError, match="exact supported"):
        task_files(package)
    package["method"] = contract()
    for name in ("missing", "../public-unit", "public-unit//Makefile"):
        package["specification"]["task_files"] = [name]
        with pytest.raises(ValueError):
            task_files(package)
    package["specification"]["task_files"] = ["public-unit/Makefile"]
    (unit / "package.json").write_text("{}")
    git_init(sample_repo)
    package["corpus"] = {"root": str(sample_repo), **corpus_receipt(sample_repo)}
    with pytest.raises(ValueError, match="automatic dependency setup"):
        task_files(package)


def test_legacy_donor_observation_cannot_admit_recipient(tmp_path, monkeypatch):
    from scripts.knowledge_evaluation.continuity import donor_capability
    from scripts.knowledge_evaluation.package import sha

    package = {"schema_version": 1, "corpus": {"root": "/synthetic/project"}, "provenance": {}}
    admission = {"task_id": "task", "execution_id": "execution", "provenance": {}}
    completion = {
        "kind": "evaluation-producer-completion-v1",
        "task_id": "task",
        "execution_id": "execution",
        "repository": "/synthetic/project",
        "native_return": {"wait": {"record_sha256": "a" * 64}},
    }
    refs = {}
    for name, value in (("admission", admission), ("completion", completion)):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        refs[name] = {"path": str(path), "sha256": sha(path.read_bytes())}
    before = {name: Path(ref["path"]).read_bytes() for name, ref in refs.items()}

    def forbidden(*args):
        pytest.fail("unqualified donor must not reach its database")

    monkeypatch.setattr("scripts.knowledge_evaluation.continuity._readonly", forbidden)
    with pytest.raises(ValueError, match="^qualified native observer unavailable$"):
        donor_capability(package, refs)
    assert before == {name: Path(ref["path"]).read_bytes() for name, ref in refs.items()}


_CLEANUP_WORKER_SHIM = """import os,runpy,signal,subprocess,sys
mode=sys.argv.pop(1)
worker=sys.argv.pop(1)
assert mode in {'disposed','deny-final','deny-term'}
popen,killpg=subprocess.Popen,os.killpg
native={}
def capture(argv,*args,**kwargs):
    process=popen(argv,*args,**kwargs)
    if isinstance(argv,(list,tuple)) and '--session-id' in argv:
        native[process.pid]=process
    return process
def dispose(pid,sig):
    if pid not in native:
        return killpg(pid,sig)
    if (mode=='deny-final' and sig==signal.SIGKILL) or (mode=='deny-term' and sig==signal.SIGTERM):
        raise PermissionError('FIXTURE_SIGNAL_CANARY')
    if sig==signal.SIGKILL:
        assert native[pid].wait(timeout=5)==0
        raise ProcessLookupError
    return None
subprocess.Popen=capture
os.killpg=dispose
runpy.run_path(worker,run_name='__main__')
"""


def _controlled_cleanup_worker(monkeypatch, tmp_path, mode):
    from scripts import knowledge_native_eval

    original = knowledge_native_eval.worker_command
    shim = tmp_path / "controlled-cleanup-worker.py"
    shim.write_text(_CLEANUP_WORKER_SHIM)

    def command(bridge, output):
        argv = original(bridge, output)
        return [*argv[:3], str(shim), mode, *argv[3:]]

    monkeypatch.setattr(knowledge_native_eval, "worker_command", command)


@pytest.mark.parametrize("malformed", [False, True])
def test_persisted_telemetry_boundary_uses_real_worker_drain(
    sample_repo, tmp_path, monkeypatch, malformed
):
    _persisted_telemetry_case(sample_repo, tmp_path, monkeypatch, malformed, "disposed")


@pytest.mark.parametrize("mode", ["deny-final", "deny-term"])
def test_persisted_telemetry_boundary_injected_cleanup_failure(
    sample_repo, tmp_path, monkeypatch, mode
):
    _persisted_telemetry_case(sample_repo, tmp_path, monkeypatch, True, mode)


def _persisted_telemetry_case(sample_repo, tmp_path, monkeypatch, malformed, mode):
    """Synthetic stdout crosses live.run's drain hook into its actual persisted receipt."""
    import os

    from scripts.knowledge_evaluation.live import TELEMETRY_UNAVAILABLE
    from scripts.knowledge_evaluation.package import corpus_receipt, sha
    from scripts.knowledge_native_eval import execute
    from tests.execution_helpers import isolated_launch_environment
    from tests.native_provider_fixture import _CLAUDE
    from tests.test_knowledge_native_eval import case

    isolated_launch_environment(monkeypatch, tmp_path / "telemetry-tooling")
    _controlled_cleanup_worker(monkeypatch, tmp_path, mode)
    events = [
        {
            "type": "assistant",
            "parent_tool_use_id": "toolu_Telemetry123",
            "message": {
                "model": "gpt-6-astra",
                "effort": "medium",
                "usage": {"input_tokens": 12, "output_tokens": 3},
                "content": [
                    {"type": "text", "text": "BOUNDARY_PROSE_CANARY"},
                    {"type": "thinking", "thinking": "BOUNDARY_REASONING_CANARY"},
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "usage": {"input_tokens": 1, "auth": {"token": "BOUNDARY_USAGE_AUTH_CANARY"}},
            },
        },
        {
            "type": "result",
            "modelUsage": {
                "gpt-6-astra": {"inputTokens": 1, "auth": {"token": "BOUNDARY_MODEL_AUTH_CANARY"}},
            },
        },
        {"type": "turn.completed", "usage": {"input_tokens": 21, "output_tokens": 5}},
    ]
    lines = [json.dumps(event) for event in events]
    if malformed:
        lines.append('{"BOUNDARY_MALFORMED_CANARY":')
    binary = tmp_path / "telemetry-bin"
    binary.mkdir()
    executable = binary / "claude"
    # Reuse the fixture's real SessionStart handshake, then emit synthetic public stream events.
    executable.write_text(
        _CLAUDE + "\nfor line in " + repr(lines) + ":\n    print(line, flush=True)\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    package = case(sample_repo, tmp_path)
    package.update(host="claude", model="fable", arm="B")
    before = corpus_receipt(sample_repo)
    result = execute(package, "live")
    output = Path(package["output_directory"])
    from contextlib import closing

    from harness.db import connect
    from harness.execution_settlement import closed_context
    from harness.execution_store import get_execution
    from harness.native_diagnostics import message

    admission = json.loads((output / "admission.json").read_bytes())
    with closing(connect(admission["database"])) as conn:
        execution = get_execution(conn, admission["execution_id"])
        attempt = conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=?", (execution["id"],)
        ).fetchone()
        assert execution["result"] is None and attempt["status"] == "Exited"
        if mode == "disposed":
            assert execution["status"] == "Interrupted"
            assert closed_context(execution, attempt)
            if malformed:
                assert execution["error"] == message("NATIVE_STREAM_INVALID")
            else:
                assert (
                    execution["error"]
                    == "native host exited 0; no guarded AL implementation result"
                )
        else:
            assert execution["status"] == "RecoveryRequired"
            assert execution["error"] == message("NATIVE_STREAM_INVALID")
            expected_closure = mode == "deny-term"
            assert closed_context(execution, attempt) is expected_closure
            stage = json.loads((output / "native-failure-stage.json").read_bytes())
            failures = [
                {
                    "stage": "failure-signal" if mode == "deny-term" else "final-signal",
                    "category": "os-error",
                    "diagnostic": "NATIVE_SETTLEMENT_UNAVAILABLE",
                }
            ]
            if mode == "deny-final":
                failures.append(
                    {
                        "stage": "closure-record",
                        "category": "closure-refused",
                        "diagnostic": "NATIVE_SETTLEMENT_UNAVAILABLE",
                    }
                )
            assert stage == {
                "kind": "native-failure-stage-v1",
                "execution_id": execution["id"],
                "attempt_id": attempt["id"],
                "failures": failures,
                "returncode_known": True,
                "closure_validated": expected_closure,
                "status_publication_reached": True,
            }
    if mode != "disposed":
        assert result["status"] == "unavailable"
        assert not (output / "native-result.json").exists()
        receipt = json.loads((output / "operational-failure.json").read_bytes())
        assert receipt == {
            "kind": "evaluation-worker-failure-v2",
            "status": "unavailable",
            "worker_returncode": 1,
            "execution_id": result["execution_id"],
            "diagnostic": "isolated-worker-failed",
            "recovery": "inspect actual execution diagnostics and reservation; no automatic retry",
            "settlement": "unknown; inspect authoritative attempt/closure receipts",
        }
        persisted = json.dumps([receipt, stage, execution["error"]])
        assert "BOUNDARY_" not in persisted
        for line in lines:
            assert sha(line.encode()) not in persisted
            assert sha((line + "\n").encode()) not in persisted
        assert corpus_receipt(sample_repo) == before
        return
    assert not (output / "native-failure-stage.json").exists()
    assert not (output / "operational-failure.json").exists()
    receipt = json.loads((output / "native-result.json").read_bytes())
    assert receipt == result and receipt["status"] == "unavailable"
    assert receipt["execution_status"] == "Interrupted"
    assert (output / "worker-reservation.json").exists()
    assert receipt["modules"]["harness.execution_worker"]["path"].startswith(
        receipt["selected_bridge"] + "/harness/"
    )
    telemetry = receipt["provider_telemetry"]
    assert [row for row in telemetry if row.get("type") == "assistant"] == [
        {
            "kind": "public-provider-telemetry-v2",
            "status": "reported",
            "type": "assistant",
            "parent_tool_use_id": "toolu_Telemetry123",
            "model": "gpt-6-astra",
            "effort": "medium",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
    ]
    assert [row for row in telemetry if row.get("type") == "turn.completed"] == [
        {
            "kind": "public-provider-telemetry-v2",
            "status": "reported",
            "type": "turn.completed",
            "usage": {"input_tokens": 21, "output_tokens": 5},
        }
    ]
    assert sum(row == TELEMETRY_UNAVAILABLE for row in telemetry) == (3 if malformed else 2)
    persisted = json.dumps(receipt)
    for canary in (
        "BOUNDARY_PROSE_CANARY",
        "BOUNDARY_REASONING_CANARY",
        "BOUNDARY_USAGE_AUTH_CANARY",
        "BOUNDARY_MODEL_AUTH_CANARY",
        "BOUNDARY_MALFORMED_CANARY",
    ):
        assert canary not in persisted
    for line in lines:
        assert sha(line.encode()) not in persisted
        assert sha((line + "\n").encode()) not in persisted
    assert corpus_receipt(sample_repo) == before
