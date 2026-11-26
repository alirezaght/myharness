"""Real AL artifact/assignment adapters keep specialized and legacy authority."""

import copy
import json

import pytest
import al_agent_contracts as C
import al_agent_exchange as X
import al_artifacts as A
from hook_regression_helpers import invoke
from test_al_usability import state
from test_writer_handoff import ready


@pytest.mark.parametrize('defect', ['domain', 'denied', 'legacy', 'malformed', 'harness-missing'])
def test_process_observation_never_uses_numeric_pid_alias_as_settlement(monkeypatch, defect):
    import os
    import al_process_observation as processes
    monkeypatch.delenv('HARNESS_CHECK_CONTEXT', raising=False)
    entry = {'run': 'run', 'id': 'E1', 'reservation': 'allocation', 'recipe': {'test': True},
             'owner_pid': os.getpid(), 'pid': None}
    entry['process_observation'] = processes.allocate(entry)
    assert processes.observe(entry) == {'owner': 'live', 'checks': 'settled'}
    if defect == 'domain':
        entry['process_observation']['domain'] = {'platform': 'foreign'}
    elif defect == 'denied':
        def denied(pid):
            raise PermissionError('test-owned identity denial')
        monkeypatch.setattr(processes, 'identity', denied)
    elif defect == 'legacy':
        del entry['process_observation']
    elif defect == 'malformed':
        entry['process_observation']['owner'] = {'pid': os.getpid()}
    else:
        monkeypatch.setenv('HARNESS_CHECK_CONTEXT', 'test-owned-missing-channel')
        monkeypatch.delenv('HARNESS_CHECK_FD', raising=False)
    observed = processes.observe(entry)
    assert observed['owner'] == 'unknown'
    assert 'live' not in observed.values()


def test_process_owner_reuse_and_exact_coordinator_relationship(monkeypatch):
    import os
    import al_process_observation as processes
    monkeypatch.delenv('HARNESS_CHECK_CONTEXT', raising=False)
    parent = {'run': 'run', 'id': 'E1', 'reservation': 'parent', 'recipe': {'test': True}}
    parent['process_observation'] = processes.allocate(parent)
    child = {'run': 'run', 'id': 'E2', 'reservation': 'child'}
    child['process_observation'] = processes.allocate(child)
    assert processes.same_owner(parent, child)
    assert processes.same_owner(parent)
    original = processes.identity(os.getpid())
    changed = list(original['start'])
    changed[0] += 1
    monkeypatch.setattr(processes, 'identity', lambda pid: dict(original, start=changed))
    assert processes.observe(parent)['owner'] == 'settled'
    assert not processes.same_owner(parent, child)


def test_host_process_reference_cannot_select_another_entries_allocation(monkeypatch):
    import al_harness_process
    import al_process_observation as processes
    monkeypatch.setenv('HARNESS_CHECK_CONTEXT', 'test-owned-channel')
    calls = []
    monkeypatch.setattr(al_harness_process, 'request', lambda value: calls.append(value))
    entry = {'run': 'run', 'id': 'E2', 'reservation': 'child', 'process_observation': {
        'schema_version': 1, 'mode': 'harness', 'host_instance': 'instance', 'owner_ref': 'owner',
        'run': 'run', 'id': 'E1', 'reservation': 'parent'}}
    assert processes.observe(entry) == {'owner': 'unknown', 'checks': 'unknown'}
    assert calls == []


def test_public_generated_assignment_declares_immutable_typed_outputs(cli, tmp_path, repo):
    body = ready(cli, tmp_path, repo)
    invoke(cli, "writer", "handoff", "--id", "worker", "--body-file", str(body))
    st = state(cli)
    receipt = st["writers"]["worker"]["handoffs"][-1]
    value = receipt["contract"]
    assert value["kind"] == "task-assignment"
    assert value["context"]["producer"] == "worker"
    assert value["context"]["execution"] is None
    assert value["context"]["applicability"]["execution"] == "standalone"
    assert value["expected_outputs"] == [
        {"name": "implementation-result", "kind": "implementation-result"}
    ]
    assert any(r["reference"] == C.ref(receipt["artifact"]) for r in value["required_inputs"])
    X.consume(
        st,
        "task-assignment",
        "worker",
        {k: v for k, v in receipt.items() if k != "contract"},
        producer="worker",
    )


def test_public_typed_output_roundtrip_and_tamper_refusal(cli, tmp_path, repo):
    body = ready(cli, tmp_path, repo)
    invoke(cli, "writer", "handoff", "--id", "worker", "--body-file", str(body))
    st = state(cli)
    assignment = st["writers"]["worker"]["handoffs"][-1]["contract"]
    content = "Authored implementation. Evidence is separately governed."
    result = C.make(
        "implementation-result",
        X.context(st, "worker"),
        X.specification(st),
        "implementation-result",
        content,
        status="authored",
        inputs=assignment["required_inputs"],
        criteria=assignment["acceptance_criteria"],
        constraints=assignment["constraints"],
    )
    path = tmp_path / "result.md"
    path.write_text(content)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(result))
    invoke(
        cli,
        "artifact",
        "output",
        "--name",
        "result.md",
        "--file",
        str(path),
        "--contract-file",
        str(contract),
        "--writer",
        "worker",
    )
    _, out, _ = invoke(cli, "artifact", "show", "--name", "result.md")
    assert json.loads(out)["contract"] == result
    assert state(cli)["writers"]["worker"]["state"] == "ACTIVE"
    reference = st["writers"]["worker"]["handoffs"][-1]["artifact"]
    from pathlib import Path

    Path(reference["stored_path"]).write_text("corrupted prompt")
    code, _, err = invoke(cli, "artifact", "show", "--name", "result.md", ok=False)
    assert code != 0
    assert "corrupted prompt" not in err


@pytest.mark.parametrize("mutation", ["kind", "version", "key", "status", "context", "reference"])
def test_invalid_explicit_contract_does_not_publish_or_echo(cli, tmp_path, repo, mutation):
    body = ready(cli, tmp_path, repo)
    invoke(cli, "writer", "handoff", "--id", "worker", "--body-file", str(body))
    st = state(cli)
    content = "A supporting report without verified completion."
    value = C.make(
        "implementation-result",
        X.context(st, "worker"),
        X.specification(st),
        "implementation-result",
        content,
        status="authored",
    )
    secret = "PRIVATE_CONTRACT_SECRET_SENTINEL"
    if mutation == "kind":
        value["kind"] = secret
    if mutation == "version":
        value["schema_version"] = secret
    if mutation == "key":
        value[secret] = secret
    if mutation == "status":
        value["status"] = secret
    if mutation == "context":
        value["context"]["producer"] = secret
    if mutation == "reference":
        value["source_specification"]["reference"]["id"] = secret
    report = tmp_path / "report.md"
    report.write_text(content)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(value))
    before = state(cli)
    code, out, err = invoke(
        cli,
        "artifact",
        "output",
        "--name",
        "bad.md",
        "--file",
        str(report),
        "--contract-file",
        str(contract),
        "--writer",
        "worker",
        ok=False,
    )
    assert code != 0 and secret not in out + err
    assert state(cli) == before


def test_legacy_supporting_outputs_remain_free_form(cli, tmp_path):
    invoke(cli, "start", "--spec", "Preserve supporting artifacts and legacy output semantics.")
    path = tmp_path / "notes.md"
    path.write_text("Free-form internal reasoning is supported.")
    invoke(cli, "artifact", "output", "--name", "notes.md", "--file", str(path))
    _, out, _ = invoke(cli, "artifact", "show", "--name", "notes.md")
    assert "contract" not in json.loads(out)
    assert A.read(state(cli)["task_package"])


def test_new_requirement_analysis_consumed_from_actual_accepted_package(cli):
    invoke(cli, "start", "--spec", "Analyze and preserve this actual accepted specification.")
    st = state(cli)
    result = X.consume(
        st, "requirement-analysis", st["task_package"]["id"], A.read_json(st["task_package"])
    )
    assert result["kind"] == "requirement-analysis"
    assert result["acceptance_criteria"] == A.read_json(st["task_package"])["criteria"]
    changed = copy.deepcopy(st)
    changed["agent_contracts"]["requirement-analysis:" + st["task_package"]["id"]]["status"] = (
        "approved"
    )
    with pytest.raises(C.ContractError):
        X.consume(
            changed,
            "requirement-analysis",
            st["task_package"]["id"],
            A.read_json(st["task_package"]),
        )


def test_planning_adapter_does_not_bypass_specialized_report_validation(cli, repo, tmp_path, monkeypatch):
    from test_planning_authoring import pending, retain, ok
    import al_store
    sid = pending(cli, repo, tmp_path, monkeypatch)
    packet = json.loads(ok(cli, 'planning', 'packet')[1])
    report, path, saved = retain(cli, tmp_path, packet)
    st = al_store.load(sid)
    key = 'implementation-plan:' + saved['report']['id']
    assert st['agent_contracts'][key]['kind'] == 'implementation-plan'
    ok(cli, 'planning', 'admit')
    # A typed envelope does not substitute for the protected planning payload.
    forged = copy.deepcopy(report);forged['packet_sha256'] = 'a' * 64
    path.write_text(json.dumps(forged))
    before = al_store.state_path(sid).read_bytes()
    assert cli('planning', 'report', '--file', str(path))[0] != 0
    assert al_store.state_path(sid).read_bytes() == before


def test_real_evidence_result_is_consumed_by_ledger_and_delivery_is_advisory(cli, tmp_path):
    import sys
    import al_ledger
    from evidence_helpers import start_implementation
    start_implementation(cli, tmp_path)
    invoke(cli, 'evidence', 'plan', '--area', 'contract-check', '--scope', 'svc', '--kind', 'check',
           '--', sys.executable, '-c', 'print("checked real fixture")')
    invoke(cli, 'evidence', 'run', '--area', 'contract-check')
    st = state(cli)
    entry = st['evidence']['entries'][-1]
    assert st['agent_contracts']['verification-result:' + entry['id']]['kind'] == 'verification-result'
    assert al_ledger.entry_verdict(st, entry)[0] == 'VALID'
    _, out, _ = invoke(cli, 'publication', 'status')
    delivery = json.loads(out)
    assert delivery['contract']['kind'] == 'final-delivery'
    assert delivery['contract']['status'] == 'observed'
    assert delivery['ready_to_publish'] is False
    assert state(cli)['status'] == 'RUNNING'


def test_failure_and_escalation_real_consumers_preserve_operational_vs_business(cli, tmp_path, repo, monkeypatch):
    from execution_helpers import report
    from test_implementation_continuity import setup, ok
    import al_store
    controller, _, sid = setup(cli, tmp_path, repo, monkeypatch)
    try:
        diagnostic = tmp_path / 'diagnostic.md';diagnostic.write_text('Fixture provider unavailable.')
        reference = json.loads(ok(cli, 'artifact', 'record', '--file', str(diagnostic), '--kind', 'operational-diagnostic')[1])
        issue = {'id':'provider', 'revision':1, 'cause':'provider_unavailable', 'task_ids':['A'],
                 'paths':['svc'], 'capability_or_check_ref':'provider-health', 'diagnostic_ref':reference,
                 'state':'Repairing', 'next_action':'Retry configured health check',
                 'observed_binding':al_store.load(sid)['integration']['binding']}
        assert report(cli, tmp_path, 'issue-update', issue)[0] == 0
        st = al_store.load(sid)
        assert st['agent_contracts']['failure-report:provider']['kind'] == 'failure-report'
        assert not st['integration']['blockers']
        assert report(cli, tmp_path, 'issue-update', issue)[0] == 0
        blocker = {'id':'choice', 'kind':'developer_engineering_decision', 'tasks':['A'],
                   'question':'Choose a storage architecture', 'context':'Two supported alternatives',
                   'alternatives':['one','two'], 'recommendation':'one'}
        assert report(cli, tmp_path, 'blocker', blocker)[0] == 0
        assert al_store.load(sid)['agent_contracts']['escalation-request:choice']['kind'] == 'escalation-request'
        assert report(cli, tmp_path, 'blocker', blocker)[0] == 0
        before = al_store.state_path(sid).read_bytes()
        assert cli('integration', 'task-admit', '--task', 'A')[0] != 0
        assert al_store.state_path(sid).read_bytes() == before
    finally:
        controller.conn.close()


def test_writer_release_does_not_require_a_typed_result(cli, tmp_path, repo):
    body = ready(cli, tmp_path, repo)
    invoke(cli, 'writer', 'handoff', '--id', 'worker', '--body-file', str(body))
    invoke(cli, 'writer', 'finish', '--id', 'worker', '--outcome', 'success')
    assert state(cli)['writers']['worker']['state'] == 'FINISHED'
    assert not state(cli).get('agent_outputs')


def test_requirement_analysis_tracks_real_amendment_lineage_and_captured_producer(cli, tmp_path):
    invoke(cli, 'start', '--spec', 'Preserve actual accepted requirement analysis across explicit amendments.')
    initial = state(cli)
    original = copy.deepcopy(initial['agent_contracts']['requirement-analysis:' + initial['task_package']['id']])
    amendment = {'id':'scope-v2', 'text':'Also retain immutable typed evidence across a restarted consumer.',
                 'source':'explicit developer fixture amendment', 'affected_criteria':['AC-01']}
    path = tmp_path / 'amendment.json';path.write_text(json.dumps(amendment))
    invoke(cli, 'task', 'amend', '--file', str(path))
    current = state(cli)
    assert X.require_analysis(current)['source_specification']['amendments']
    changed = copy.deepcopy(current)
    changed['agent_contracts']['requirement-analysis:' + current['task_package']['id']] = original
    with pytest.raises(C.ContractError):
        X.require_analysis(changed)
    changed = copy.deepcopy(current)
    changed['agent_contracts']['requirement-analysis:' + current['task_package']['id']]['context']['producer'] = 'invented-native-producer'
    with pytest.raises(C.ContractError):
        X.require_analysis(changed)


def test_common_verification_envelope_cannot_replace_actual_independent_review(cli, tmp_path, state_file):
    from evidence_helpers import start_implementation, plan_and_run_broad, complete_review
    start_implementation(cli, tmp_path)
    assert plan_and_run_broad(cli) == 0
    invoke(cli, 'phase', 'VERIFICATION')
    def inspect(report):
        st = state(cli)
        envelope = C.make('verification-result', X.context(st), X.specification(st),
                          'review-observation', report, status='observed')
        path = tmp_path / 'not-a-review.json';path.write_text(json.dumps(envelope))
        before = state_file().read_bytes()
        result = getattr(cli, 'raw', cli)('review-fresh' if report['stage'] == 'fresh' else 'review-result', '--file', str(path))
        assert result[0] != 0
        assert state_file().read_bytes() == before
    complete_review(cli, tmp_path, blocking=False, inspect_report=inspect)


def _r4_interrupted_check(cli, cli_process, state_file):
    import signal
    import sys
    from evidence_helpers import wait_running
    invoke(cli, 'start', '--spec', 'Retain interrupted evidence and authenticate explicit reconciliation.')
    invoke(cli, 'evidence', 'plan', '--area', 'interrupt-observation', '--kind', 'check', '--scope', 'svc', '--',
           sys.executable, '-c', 'import time;time.sleep(60)')
    proc = cli_process('evidence', 'run', '--area', 'interrupt-observation')
    try:
        wait_running(state_file, 'interrupt-observation', process=proc)
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=15)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill();proc.communicate(timeout=5)
    original = copy.deepcopy(state(cli)['evidence']['entries'][-1])
    assert original['status'] == 'INFRA' and original['interrupted'] and original['ended_at']
    companion = copy.deepcopy(state(cli)['agent_contracts']['verification-result:' + original['id']])
    return original, companion, proc.returncode


def test_r4_repair_interrupted_public_check_reconciles_and_retains_observation(cli, cli_process, tmp_path, state_file):
    original, companion, runner_code = _r4_interrupted_check(cli, cli_process, state_file)
    proof = tmp_path / 'reconciliation.json'
    proof.write_text(json.dumps({'runner_returncode': runner_code, 'cleanup': original.get('cleanup'),
                                'observation_id': original['id']}))
    invoke(cli, 'check-reconcile', '--id', original['id'], '--reason', 'Observed own runner termination and inspected cleanup.', '--artifact', str(proof))
    invoke(cli, 'evidence', 'status')
    current = state(cli)['evidence']['entries'][-1]
    assert current['status'] == 'INFRA'
    retained = A.read_json(current['reconciliation']['observation'])
    assert retained == {'schema_version': 1, 'entry': original, 'contract': companion}
    assert current['reconciliation']['schema_version'] == 1
    before = state_file().read_bytes()
    assert cli('check-reconcile', '--id', original['id'], '--reason', 'duplicate', '--artifact', str(proof))[0] != 0
    assert state_file().read_bytes() == before
    from pathlib import Path
    Path(current['reconciliation']['artifact']['stored_path']).write_text('altered retained proof')
    assert cli('evidence', 'status')[0] != 0
    assert state_file().read_bytes() == before


def test_r4_repair_issue_updates_across_unrelated_graph_growth(cli, tmp_path, repo, monkeypatch):
    import sys
    import al_store as S
    from execution_helpers import register_task, report
    from test_implementation_continuity import setup, ok
    controller, _, sid = setup(cli, tmp_path, repo, monkeypatch)
    try:
        diagnostic = tmp_path / 'graph-diagnostic.md';diagnostic.write_text('Observed repairable fixture tool failure.')
        ref = json.loads(ok(cli, 'artifact', 'record', '--file', str(diagnostic), '--kind', 'operational-diagnostic')[1])
        issue = {'id': 'graph-tool', 'revision': 1, 'cause': 'tool_unavailable', 'task_ids': ['A'], 'paths': ['svc'],
                 'capability_or_check_ref': 'graph-health', 'diagnostic_ref': ref, 'state': 'Repairing',
                 'next_action': 'Repair fixture runtime', 'observed_binding': S.load(sid)['integration']['binding']}
        assert report(cli, tmp_path, 'issue-update', issue)[0] == 0
        historical = copy.deepcopy(S.load(sid)['agent_contracts']['failure-report:graph-tool'])
        assert register_task(cli, tmp_path, 'Independent', ['other'])[0] == 0
        assert report(cli, tmp_path, 'issue-update', issue)[0] == 0
        for invalid in (
            issue | {'revision': 1, 'next_action': 'stale revision mutation'},
            issue | {'revision': 2, 'paths': ['web']},
            issue | {'revision': 2, 'observed_binding': issue['observed_binding'] | {'al_run_id': 'foreign'}},
            issue | {'revision': 2, 'state': 'Resolved', 'resolution_ref': {'evidence_id': 'E999', 'diagnostic_sha256': ref['sha256']}},
        ):
            before = S.state_path(sid).read_bytes()
            assert report(cli, tmp_path, 'issue-update', invalid)[0] != 0
            assert S.state_path(sid).read_bytes() == before
        update = issue | {'revision': 2, 'next_action': 'Verify repaired fixture runtime'}
        result = report(cli, tmp_path, 'issue-update', update);assert result[0] == 0, result
        assert S.load(sid)['agent_contracts']['failure-report:graph-tool']['context']['graph_revision'] > historical['context']['graph_revision']
        ok(cli, 'evidence', 'plan', '--area', 'graph-health', '--scope', 'svc', '--', sys.executable, '-c', 'print("1 passed")')
        ok(cli, 'evidence', 'run', '--area', 'graph-health')
        entry = S.load(sid)['evidence']['entries'][-1]
        diagnostic.write_text('Fresh health observation after the actual successful check.')
        fresh = json.loads(ok(cli, 'artifact', 'record', '--file', str(diagnostic), '--kind', 'operational-diagnostic')[1])
        resolved = update | {'revision': 3, 'state': 'Resolved', 'diagnostic_ref': fresh,
                             'resolution_ref': {'evidence_id': entry['id'], 'diagnostic_sha256': fresh['sha256']}}
        result = report(cli, tmp_path, 'issue-update', resolved);assert result[0] == 0, result
        assert S.load(sid)['operational_issues']['graph-tool'] == resolved
    finally:
        controller.conn.close()


@pytest.mark.parametrize('criteria', [['invented criterion'], []])
@pytest.mark.parametrize('existing', [False, True])
def test_r4_repair_actual_criteria_reject_before_publication(cli, tmp_path, repo, criteria, existing):
    body = ready(cli, tmp_path, repo)
    invoke(cli, 'writer', 'handoff', '--id', 'worker', '--body-file', str(body))
    st = state(cli);declared = st['writers']['worker']['handoffs'][-1]['contract']
    content = 'Actual authored content remains subject to the declared criteria.'
    value = C.make('implementation-result', X.context(st, 'worker'), X.specification(st),
                   'implementation-result', content, status='authored', inputs=declared['required_inputs'],
                   criteria=criteria, constraints=declared['constraints'])
    path = tmp_path / 'criteria-result.md';path.write_text(content)
    contract = tmp_path / 'criteria-contract.json';contract.write_text(json.dumps(value))
    if existing:
        valid = copy.deepcopy(value)
        valid['acceptance_criteria'] = declared['acceptance_criteria']
        valid['field_applicability'].pop('acceptance_criteria', None)
        contract.write_text(json.dumps(valid))
        invoke(cli, 'artifact', 'output', '--name', 'criteria.md', '--file', str(path),
               '--contract-file', str(contract), '--writer', 'worker')
        assert json.loads(invoke(cli, 'artifact', 'show', '--name', 'criteria.md')[1])['contract'] == valid
        contract.write_text(json.dumps(value))
    before = state(cli)
    code, out, err = cli('artifact', 'output', '--name', 'criteria.md', '--file', str(path),
                         '--contract-file', str(contract), '--writer', 'worker')
    assert code != 0, 'invented/empty criteria were accepted'
    assert '/acceptance_criteria' in str(code) + out + err
    assert state(cli) == before


@pytest.mark.parametrize('tamper', ['observation', 'log', 'artifact'])
def test_r4_repair_reconciliation_refuses_changed_provenance(cli, cli_process, tmp_path, state_file, tamper):
    from pathlib import Path
    original, _, _ = _r4_interrupted_check(cli, cli_process, state_file)
    proof = tmp_path / 'proof.md';proof.write_text('Observed owned runner termination and actual cleanup.')
    if tamper == 'observation':
        data = json.loads(state_file().read_bytes())
        data['evidence']['entries'][-1]['note'] = 'altered observation'
        state_file().write_text(json.dumps(data))
    elif tamper == 'log':
        Path(original['log']).write_text('altered original log')
    else:
        target = tmp_path / 'proof-target.md';target.write_text('fixture')
        proof.unlink();proof.symlink_to(target)
    before = state_file().read_bytes()
    assert cli('check-reconcile', '--id', original['id'], '--reason', 'Inspect actual proof', '--artifact', str(proof))[0] != 0
    assert state_file().read_bytes() == before


def test_r4_repair_living_check_cannot_be_reconciled(cli, cli_process, state_file, tmp_path):
    import signal
    import sys
    from evidence_helpers import wait_running
    invoke(cli, 'start', '--spec', 'Do not reconcile a living owned check.')
    invoke(cli, 'evidence', 'plan', '--area', 'living', '--kind', 'check', '--scope', 'svc', '--', sys.executable, '-c', 'import time;time.sleep(60)')
    proc = cli_process('evidence', 'run', '--area', 'living')
    try:
        wait_running(state_file, 'living', process=proc)
        current = state(cli)['evidence']['entries'][-1]
        proof = tmp_path / 'living-proof.md';proof.write_text('A living check cannot have a valid reconciliation.')
        before = state_file().read_bytes()
        assert cli('check-reconcile', '--id', current['id'], '--reason', 'living', '--artifact', str(proof))[0] != 0
        assert state_file().read_bytes() == before
    finally:
        proc.send_signal(signal.SIGINT);proc.communicate(timeout=15)
