"""Public admission and explicit continuation boundaries for recovery."""

import json
from pathlib import Path

from test_rework_recovery import decision, exhaust, load, started
from test_rework import fast


def user_request(cli, state_file, tmp_path, request_id, *, binding=None):
    import al_rework_recovery_contract as Contract

    st = load(state_file)
    binding = binding or Contract.binding(st)
    authorization = {
        "schema_version": 1,
        "kind": "explicit-user-continuation-v1",
        "request_id": request_id,
        "binding": binding,
        "message": "Continue with one bounded two-return batch.",
        "provenance": "Explicit user message in the current conversation.",
    }
    source = tmp_path / f"{request_id}-authorization.json"
    source.write_text(json.dumps(authorization))
    code, out, err = cli("artifact", "record", "--file", str(source), "--kind", "user-authorization")
    assert code == 0, (out, err)
    reference = json.loads(out)
    if "artifact" in reference:
        reference = reference["artifact"]
    request = {
        "schema_version": 1,
        "kind": "rework-recovery-continuation-v1",
        "request_id": request_id,
        "checkpoint_id": st["stopping_checkpoint"],
        "binding": binding,
        "user_authorization": reference,
        "next_action": "Repair the remaining in-scope defect, then run its planned check.",
        "grant_size": 2,
    }
    path = tmp_path / f"{request_id}-continuation.json"
    path.write_text(json.dumps(request))
    return path


def test_pending_reassessment_blocks_all_public_writer_and_task_admission(cli, state_file, tmp_path):
    started(cli, tmp_path)
    exhaust(cli)
    before = state_file().read_bytes()

    attempts = (
        cli(
            "writer",
            "start",
            "--id",
            "degraded-bypass",
            "--role",
            "al-engineer",
            "--files",
            "svc/app.py",
            "--degraded",
            "--reason",
            "continue despite pending recovery",
        ),
        cli("integration", "task-admit", "--task", "recovery-bypass"),
        cli("phase", "FINALIZATION"),
    )
    assert all(result[0] != 0 for result in attempts)
    assert state_file().read_bytes() == before


def test_distinct_user_continuations_grant_two_each_and_replay_once(cli, state_file, tmp_path):
    started(cli, tmp_path)
    exhaust(cli)
    for number in (1, 2):
        if number == 1:
            path, _ = decision(
                cli,
                tmp_path,
                request_id="auto-1",
                decision="allow",
                basis="revised-diagnosis",
            )
        else:
            path, _ = decision(
                cli,
                tmp_path,
                request_id="auto-2",
                decision="allow",
                basis="new-independent-defect",
            )
        assert cli("rework-recovery", "decide", "--file", str(path))[0] == 0
        assert cli("phase", "FINALIZATION")[0] == 0
        assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
        assert cli("phase", "FINALIZATION")[0] == 0
        assert cli("rework", "--source", "finalization", *fast(cli))[0] == 10

    stopped = load(state_file)
    assert stopped["rework"]["total"] == 6
    assert stopped["rework_recovery"]["automatic_authorizations"] == 2
    for number in (1, 2):
        request = user_request(cli, state_file, tmp_path, f"user-{number}")
        code, receipt, err = cli("rework-recovery", "continue", "--file", str(request))
        assert code == 0, (receipt, err)
        granted = load(state_file)
        assert granted["rework"]["total"] == 6 + 2 * (number - 1)
        assert granted["rework"]["used"] == 0
        assert granted["rework_recovery"]["automatic_authorizations"] == 2
        before = state_file().read_bytes()
        assert cli("rework-recovery", "continue", "--file", str(request))[:2] == (0, receipt)
        assert state_file().read_bytes() == before
        for _ in range(2):
            assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
            assert cli("phase", "FINALIZATION")[0] == 0
        assert cli("rework", "--source", "finalization", *fast(cli))[0] == 10
    final = load(state_file)
    assert final["rework"]["total"] == 10
    assert final["rework_recovery"]["automatic_authorizations"] == 2
    assert [grant["kind"] for grant in final["rework_recovery"]["grants"]] == [
        "initial",
        "automatic",
        "automatic",
        "user",
        "user",
    ]


def test_changed_source_rejects_stale_continuation_then_current_binding_refreshes(cli, state_file, tmp_path, repo):
    started(cli, tmp_path)
    exhaust(cli)
    path, _ = decision(cli, tmp_path, decision="decline", basis="no-useful-next-step")
    assert cli("rework-recovery", "decide", "--file", str(path))[0] == 0
    stale = user_request(cli, state_file, tmp_path, "stale-source")
    (repo / "svc/app.py").write_text("changed while recovery was stopped\n")
    before = state_file().read_bytes()
    refused = cli("rework-recovery", "continue", "--file", str(stale))
    assert refused[0] != 0
    assert state_file().read_bytes() == before

    # A distinct explicit request binds the now-current candidate; checkpoint
    # restoration retains the unfinished finalization occurrence and invalidates
    # evidence captured against the earlier candidate.
    current = user_request(cli, state_file, tmp_path, "current-source")
    assert cli("rework-recovery", "continue", "--file", str(current))[0] == 0
    st = load(state_file)
    assert st["rework_recovery"]["automatic_authorizations"] == 0
    assert st["rework"]["total"] == 2 and st["rework"]["used"] == 0
    assert st["phase"] == "FINALIZATION"
    assert st["evidence"].get("invalidated_at") is not None


def test_final_review_inspects_recovery_reports_and_rejects_tampering(cli, state_file, tmp_path):
    import al_proof_json as Proof
    import al_rework_recovery_contract as Contract
    from evidence_helpers import complete_review, plan_and_run_broad
    from test_rework import REPRO

    started(cli, tmp_path)
    exhaust(cli)
    report, _ = decision(cli, tmp_path, request_id="reviewed-recovery")
    assert cli("rework-recovery", "decide", "--file", str(report))[0] == 0
    assert cli("phase", "FINALIZATION")[0] == 0
    Path("svc/fixed").write_text("fixed for current-candidate review\n")
    assert cli("evidence", "run", "--area", "repro", "--tier", "focused", "--scope", "svc", "--", *REPRO)[0] == 0
    assert cli("phase", "VERIFICATION")[0] == 0
    assert plan_and_run_broad(cli) == 0

    def report_inspection_guard(*arguments):
        if arguments and arguments[0] in {"review-fresh", "review-result"}:
            path = Path(arguments[arguments.index("--file") + 1])
            data = json.loads(path.read_bytes())
            required = Contract.required_hashes(load(state_file))
            data["inspected_artifacts"] = sorted(set(data["inspected_artifacts"]) | required)
            missing = next(iter(required))
            data["inspected_artifacts"] = [value for value in data["inspected_artifacts"] if value != missing]
            path.write_text(json.dumps(data))
            before = state_file().read_bytes()
            refused = cli(*arguments)
            assert refused[0] != 0
            assert state_file().read_bytes() == before
            data["inspected_artifacts"] = sorted(set(data["inspected_artifacts"]) | required)
            path.write_text(json.dumps(data))
        return cli(*arguments)

    report_inspection_guard.state = cli.state
    complete_review(report_inspection_guard, tmp_path, blocking=False)
    state = load(state_file)
    assert state["review_status"] == "APPROVED"
    required = Contract.required_hashes(state)
    for stage in ("fresh", "comparison"):
        saved_report = Proof.read(state["review"][stage])
        assert required <= set(saved_report["inspected_artifacts"])

    saved = state_file().read_bytes()
    retained = state["rework_recovery"]["decisions"][0]["report"]
    artifact = Path(retained["stored_path"])
    original = artifact.read_bytes()
    artifact.write_bytes(original + b"\n")
    assert cli("show")[0] != 0
    assert state_file().read_bytes() == saved


def test_published_schemas_validate_live_decision_continuation_and_receipts(
    cli, state_file, tmp_path
):
    from jsonschema import Draft202012Validator

    schemas = Path(__file__).resolve().parents[1] / "reference/schemas"
    decision_schema = json.loads((schemas / "rework-recovery-decision.json").read_bytes())
    continuation_schema = json.loads((schemas / "rework-recovery-continuation.json").read_bytes())
    receipt_schema = json.loads((schemas / "rework-recovery-receipt.json").read_bytes())
    for schema in (decision_schema, continuation_schema, receipt_schema):
        Draft202012Validator.check_schema(schema)

    started(cli, tmp_path)
    exhaust(cli)
    decision_path, decision_report = decision(cli, tmp_path, request_id="schema-allow")
    Draft202012Validator(decision_schema).validate(decision_report)
    code, receipt_text, err = cli("rework-recovery", "decide", "--file", str(decision_path))
    assert code == 0, (receipt_text, err)
    Draft202012Validator(receipt_schema).validate(json.loads(receipt_text))

    assert cli("phase", "FINALIZATION")[0] == 0
    assert cli("rework", "--source", "finalization", *fast(cli))[0] == 0
    assert cli("phase", "FINALIZATION")[0] == 0
    assert cli("rework", "--source", "finalization", *fast(cli))[0] == 10
    declined_path, _ = decision(
        cli,
        tmp_path,
        request_id="schema-decline",
        decision="decline",
        basis="no-useful-next-step",
    )
    assert cli("rework-recovery", "decide", "--file", str(declined_path))[0] == 0
    continuation_path = user_request(cli, state_file, tmp_path, "schema-user")
    continuation = json.loads(continuation_path.read_bytes())
    Draft202012Validator(continuation_schema).validate(continuation)
    code, receipt_text, err = cli("rework-recovery", "continue", "--file", str(continuation_path))
    assert code == 0, (receipt_text, err)
    Draft202012Validator(receipt_schema).validate(json.loads(receipt_text))
