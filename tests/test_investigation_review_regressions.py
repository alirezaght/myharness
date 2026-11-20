"""Independent review counterexamples through real source, lineage and worker seams."""

import copy
import time
from dataclasses import asdict

import pytest

from tests.investigation_fixture import native_fixture as native_fixture
from tests.planning_session_fixture import attached_payload, ordinary_owner, request
from tests.planning_session_fixture import session as session


def source_publication(
    bridge, receipt, request_id, *, premise="Original CSV premise", predecessor=None
):
    from harness.interview_coverage import coverage_entries
    from harness.investigation_repository import source_record
    from tests.investigation_fixture import synthesis

    if not hasattr(bridge, "_test_source_template"):
        bridge._test_source_template = attached_payload(bridge, receipt, request_id, decisions=True)
    payload = copy.deepcopy(bridge._test_source_template)
    payload.update(request(bridge, receipt, request_id))
    payload["content"]["source_bundle"]["attached_envelope"]["captured_base"] = bridge.base()
    envelope = payload["content"]["source_bundle"]["attached_envelope"]
    envelope["sources"][0] = source_record(
        "ticket", "https://one.atlassian.net/browse/AR-2906", premise, {}
    )
    output = synthesis(
        {
            "base": {"task": bridge.task_id},
            "investigation": {
                "sources": envelope["sources"],
                "catalogue": [
                    asdict(e) for e in coverage_entries(bridge.controller, bridge.task_id)
                ],
            },
        },
        decisions=True,
    )
    output["investigation"]["decisions"][0]["supersedes"] = predecessor
    envelope["evidence"] = output["investigation"]
    envelope["candidate"] = {"body": output["body"], "graph": output["graph"]["concrete"]}
    return bridge.publish(payload, bridge._test_caller, bridge._test_cap)


def test_sir001_search_observations_validate_without_mutating_originals(tmp_path):
    from harness.authoring_native import validate_output
    from harness.interview_coverage import validate_sources
    from harness.investigation_repository import Reader
    from tests.investigation_fixture import synthesis

    (tmp_path / "src").mkdir()
    (tmp_path / "src/example.py").write_text("needle = True\n")
    reader = Reader(tmp_path)
    original = reader.observe("needle")
    saved = copy.deepcopy(original)
    searched = reader.additional([{"kind": "search", "path": "src", "text": "needle"}])
    validate_sources(searched["sources"])
    assert original == saved
    inputs = {
        "base": {"task": "fixture"},
        "investigation": {"sources": searched["sources"], "catalogue": []},
        "valid_refs": ["investigation:fixture:" + s["id"] for s in searched["sources"]],
    }
    validate_output(synthesis(inputs), inputs)


@pytest.mark.parametrize("corrected", [False, True])
def test_sir002_source_retirement_preserves_physical_history_and_effective_current(
    session, corrected
):
    from harness.authoring import AuthoringService
    from harness.interview import InterviewEngine
    from harness.investigation_store import InvestigationStore
    from tests.test_authoring import rework1b_apply

    bridge, receipt, caller, cap = session
    bridge._test_caller, bridge._test_cap = caller, cap
    source_publication(bridge, receipt, "first-source-decision")
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    q1 = InvestigationStore(bridge.controller, bridge.project, bridge.task_id).decisions()[0][
        "question_id"
    ]
    service.answer(
        expected=service.store.base(),
        request_id="answer-original-source-decision",
        question=q1,
        mode="answer",
        answer="Keep the original CSV behavior",
    )
    predecessor = q1
    if corrected:
        rework1b_apply(
            service,
            f"question-response:{q1}",
            {"mode": "unknown", "answer": "", "source": None, "ref": None},
            suffix="source-corrected",
        )
        predecessor = (
            InterviewEngine(bridge.controller, bridge.task_id).effective().current("question", q1)
        )
        assert predecessor != q1
    source_publication(
        bridge,
        receipt,
        "changed-source-decision",
        premise="Changed CSV source premise",
        predecessor=predecessor,
    )
    snapshot = service.snapshot()
    current = InvestigationStore(bridge.controller, bridge.project, bridge.task_id).decisions()[0][
        "question_id"
    ]
    assert {q["id"] for q in snapshot["questions"]} == {current}
    history = {q["id"]: q for q in snapshot["historical"]["questions"]}
    assert q1 in history and history[q1]["answer"] == "Keep the original CSV behavior"
    if corrected:
        assert predecessor in history and history[predecessor]["answer"] is None
    assert {
        q.id for q in InterviewEngine(bridge.controller, bridge.task_id).effective_questions()
    } == {current}


@pytest.mark.parametrize("repository_only", [False, True])
def test_sir003_real_start_queue_claim_worker_continues_saved_sources(
    controller, tmp_path, db_path, session, monkeypatch, repository_only
):
    from harness.authoring import AuthoringService
    from harness.authoring_worker import AuthoringWorker
    from tests.investigation_fixture import synthesis

    bridge, receipt, caller, cap = (
        ordinary_owner(controller, tmp_path) if repository_only else session
    )
    publication = bridge.publish(
        attached_payload(
            bridge,
            receipt,
            "seed-source-for-normal-start",
            decisions=False,
            repository_only=repository_only,
        ),
        caller,
        cap,
    )
    bridge.detach(
        request(bridge, receipt, "explicit-standalone-for-start"),
        caller,
        cap,
        destination="standalone",
    )
    service = AuthoringService(controller, bridge.root, bridge.task_id)
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"host": host, "available": True}
    )
    old_sources = service.snapshot()["investigation"]["sources"]
    seen = []

    class Provider:
        def __init__(self, host):
            pass

        def run(self, inputs, **kwargs):
            seen.append(inputs)
            # A valid response is available; the production context/validator must agree.
            candidate = dict(
                inputs,
                investigation=dict(
                    inputs["investigation"],
                    sources=inputs["investigation"].get("sources", old_sources),
                ),
            )
            return synthesis(candidate)

    def no_acquisition(*args):
        raise AssertionError("normal continuation must not acquire Jira")

    started = service.start(
        expected=service.store.base(), request_id="normal-source-aware-start", host="codex"
    )
    base = service.store.base()
    resumed = service.start(expected=base, request_id="source-start-noop-resume", host="codex")
    assert resumed["job"] == started["job"] and service.store.base() == base
    worker = AuthoringWorker(
        db_path, bridge.root, provider_factory=Provider, acquisition_factory=no_acquisition
    )
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = controller.conn.execute(
                "SELECT * FROM authoring_jobs WHERE id=?", (started["job"],)
            ).fetchone()
            if row["state"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert seen and seen[0]["investigation"].get("sources"), (
            "captured standalone source context is incomplete"
        )
        assert row["state"] == "succeeded", dict(row)
        assert service.snapshot()["investigation"]["bundle"] != publication["result"]["bundle"]
        assert service.snapshot()["investigation"]["coverage"]["accepted"] is False
    finally:
        worker.close()
        assert worker.thread is None or not worker.thread.is_alive()


def test_sir001_repeated_no_match_and_tampered_search(tmp_path):
    from harness.interview_coverage import validate_sources
    from harness.investigation_repository import Reader

    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("known = True\n")
    reader = Reader(tmp_path)
    before = reader.observe("known")
    after = reader.additional([{"kind": "search", "path": "src", "text": "absent-literal"}])
    assert after["sources"] == before["sources"]
    repeated = reader.additional([{"kind": "search", "path": "src", "text": "known"}])
    assert repeated["sources"] == before["sources"]
    validate_sources(repeated["sources"])
    tampered = copy.deepcopy(repeated["sources"])
    tampered[0]["metadata"]["literal_match"] = True
    with pytest.raises(ValueError, match="identity"):
        validate_sources(tampered)


def test_sir002_retired_targets_consumers_and_failed_publication_are_atomic(session):
    from harness.authoring import AuthoringService
    from harness.interview import InterviewEngine
    from harness.investigation_store import InvestigationStore
    from harness.review_package import investigation_package

    bridge, receipt, caller, cap = session
    bridge._test_caller, bridge._test_cap = caller, cap
    source_publication(bridge, receipt, "source-lineage-first")
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    store = InvestigationStore(bridge.controller, bridge.project, bridge.task_id)
    old = store.decisions()[0]["question_id"]
    service.answer(
        expected=service.store.base(),
        request_id="old-source-answer",
        question=old,
        mode="answer",
        answer="Old source requirement is retained as history",
    )
    before = service.snapshot()
    with pytest.raises(ValueError):
        source_publication(
            bridge, receipt, "invalid-source-predecessor", premise="New premise", predecessor=99999
        )
    assert service.snapshot() == before
    source_publication(
        bridge, receipt, "valid-source-successor", premise="New premise", predecessor=old
    )
    current = store.decisions()[0]["question_id"]
    before = service.snapshot()
    for mode in ("answer", "unknown"):
        with pytest.raises(ValueError, match="source premise"):
            service.answer(
                expected=service.store.base(),
                request_id="refused-retired-" + mode,
                question=old,
                mode=mode,
                answer="Should not change retired history",
            )
    with pytest.raises(ValueError, match="Retired"):
        service.revise_record(
            expected=service.store.base(),
            request_id="retired-edit",
            target=f"question-response:{old}",
            target_revision=next(
                t["revision"]
                for t in before["revision_targets"]
                if t["target"] == f"question-response:{old}"
            ),
            value={"mode": "unknown", "answer": "", "source": None, "ref": None},
        )
    assert service.snapshot() == before
    assert "Old source requirement" not in service.manager().structured_body()
    engine = InterviewEngine(bridge.controller, bridge.task_id)
    assert old not in {row.question_id for row in engine.checklist().items}
    package = investigation_package(bridge.controller, bridge.task_id)["content"]
    assert old not in {q["id"] for q in package["questions"]}
    assert old in {q["id"] for q in package["historical_questions"]}
    assert package["source_lineage"]["replacements"][str(old)] == current


def seeded_continuation(session, monkeypatch):
    from harness.authoring import AuthoringService
    from harness.authoring_jobs import claim_next

    bridge, receipt, caller, cap = session
    publication = bridge.publish(
        attached_payload(bridge, receipt, "guard-source-seed", decisions=False), caller, cap
    )
    bridge.detach(request(bridge, receipt, "guard-detach"), caller, cap, destination="standalone")
    monkeypatch.setattr(
        "harness.authoring_policy.availability", lambda host: {"host": host, "available": True}
    )
    service = AuthoringService(bridge.controller, bridge.root, bridge.task_id)
    result = service.request_regeneration(
        expected=service.store.base(), request_id="guard-regenerate", host="codex"
    )
    claim = claim_next(bridge.controller.conn, "fixture-worker", service.project)
    assert result["job"] == claim["id"] and claim["kind"] == "generate"
    return service, claim, publication["result"]["bundle"]


def run_claim(service, claim, provider, stop=None):
    import threading
    from types import SimpleNamespace

    from harness.investigation_native import HANDOFFS
    from harness.investigation_sources import run

    def no_acquisition(*args):
        raise AssertionError("saved-source continuation must not acquire")

    run(
        service,
        claim,
        SimpleNamespace(
            stop=stop or threading.Event(),
            provider_factory=lambda host: provider,
            acquisition_factory=no_acquisition,
            handoffs=HANDOFFS,
        ),
    )


@pytest.mark.parametrize(
    "change", ["file", "validation", "parent", "material", "mode", "epoch", "cancel", "phase"]
)
@pytest.mark.parametrize("timing", ["before", "during"])
def test_sir003_continuation_refuses_stale_authority_and_inputs(
    session, monkeypatch, change, timing
):
    from harness.db import transaction
    from tests.investigation_fixture import synthesis

    service, claim, old_bundle = seeded_continuation(session, monkeypatch)
    manual = service.store.binding()["working_id"]
    calls = []

    def mutate():
        if change == "file":
            (service.root / "export.py").write_text("changed_after_capture=True\n")
        elif change == "material":
            service.followup(
                expected=service.store.base(),
                request_id="new-material-input",
                body="Also preserve archived account permissions",
            )
        elif change == "cancel":
            service.cancel_job(
                expected=service.store.base(), request_id="cancel-continuation", job=claim["id"]
            )
        else:
            # Fault injection: public owner transitions refuse active work.
            with transaction(service.conn):
                if change == "validation":
                    service.conn.execute(
                        "UPDATE investigation_bindings SET "
                        "validation_generation=validation_generation+1 WHERE task_id=?",
                        (service.task_id,),
                    )
                elif change == "parent":
                    service.conn.execute(
                        "UPDATE investigation_bindings SET active_bundle=NULL WHERE task_id=?",
                        (service.task_id,),
                    )
                elif change == "phase":
                    service.conn.execute(
                        "UPDATE tasks SET phase='approval' WHERE id=?", (service.task_id,)
                    )
                elif change == "mode":
                    service.conn.execute(
                        "UPDATE planning_session_owners SET mode='manual' WHERE task_id=?",
                        (service.task_id,),
                    )
                else:
                    epoch = dict(
                        service.conn.execute(
                            "SELECT * FROM planning_session_epochs WHERE task_id=? "
                            "ORDER BY epoch DESC LIMIT 1",
                            (service.task_id,),
                        ).fetchone()
                    )
                    from harness.planning_session_events import native_binding

                    epoch["epoch"] = (
                        max(epoch["epoch"], native_binding(service.conn, service.task_id)["epoch"])
                        + 1
                    )
                    epoch["attachment_id"] = "fixture-replacement-attachment"
                    service.conn.execute(
                        "INSERT INTO planning_session_epochs VALUES("
                        + ",".join("?" for _ in epoch)
                        + ")",
                        tuple(epoch.values()),
                    )
                    changed = service.conn.execute(
                        "UPDATE planning_session_owners SET epoch=?, "
                        "attachment_id=? WHERE task_id=?",
                        (epoch["epoch"], epoch["attachment_id"], service.task_id),
                    )
                    assert changed.rowcount == 1
                    import json

                    from harness.planning_session_events import native_binding

                    assert (
                        native_binding(service.conn, service.task_id)
                        != json.loads(claim["input_json"])["planning_owner"]
                    )

    class Provider:
        def run(self, inputs, **kwargs):
            calls.append(inputs)
            if timing == "during":
                mutate()
            return synthesis(inputs)

    if timing == "before":
        mutate()
    run_claim(service, claim, Provider())
    assert (
        service.conn.execute(
            "SELECT count(*) FROM investigation_bundles WHERE task_id=?", (service.task_id,)
        ).fetchone()[0]
        == 1
    )
    assert service.store.binding()["working_id"] == manual
    row = service.conn.execute("SELECT * FROM authoring_jobs WHERE id=?", (claim["id"],)).fetchone()
    assert row["applied_id"] is None and row["state"] in {"failed", "stale", "cancelled"}
    assert row["settlement"] == "settled"
    if timing == "before":
        assert not calls


def test_sir003_regenerate_additional_search_and_retry_real_publication(session, monkeypatch):
    from harness.authoring_jobs import claim_next
    from tests.investigation_fixture import synthesis

    service, claim, parent = seeded_continuation(session, monkeypatch)
    calls = []

    class Failure:
        def run(self, inputs, **kwargs):
            raise ValueError("fixture provider rejected")

    run_claim(service, claim, Failure())
    result = service.retry_job(
        expected=service.store.base(),
        request_id="retry-source-continuation",
        job=claim["id"],
        acknowledge_unknown=False,
    )
    assert result["attempt"] == 2
    claim = claim_next(service.conn, "fixture-worker-retry", service.project)

    class Provider:
        def run(self, inputs, **kwargs):
            calls.append(inputs)
            output = synthesis(inputs)
            if len(calls) == 1:
                output["investigation"]["reads"] = [
                    {"kind": "search", "path": "export.py", "text": "export_csv"}
                ]
            return output

    run_claim(service, claim, Provider())
    row = service.conn.execute("SELECT * FROM authoring_jobs WHERE id=?", (claim["id"],)).fetchone()
    assert row["state"] == "succeeded", dict(row)
    assert (
        len(calls) == 2
        and calls[0]["investigation"]["round"] == 0
        and calls[1]["investigation"]["round"] == 1
    )
    current = service.snapshot()["investigation"]["bundle"]
    from harness.investigation_store import InvestigationStore

    store = InvestigationStore(service.controller, service.project, service.task_id)
    content = store.read_active()["content"]
    assert current != parent and content["acquisition"]["bundle"] == parent
    assert content["acquisition"]["fresh_remote_verification"] is False
    assert store.acceptance() is None
    assert (
        service.store.revision(service.store.binding()["working_id"])["attribution"]
        != "model_proposal"
    )


def test_sir002_invalid_lineage_is_refused_without_fabricating_application(session):
    from dataclasses import replace

    from harness.authoring import AuthoringService
    from harness.effective_records import effective_records
    from harness.investigation_lineage import source_lineage

    bridge, receipt, caller, cap = session
    bridge._test_caller, bridge._test_cap = caller, cap
    source_publication(bridge, receipt, "lineage-negative-seed")
    ws = bridge.controller.workspace(bridge.task_id)
    rows = [
        dict(r)
        for r in bridge.controller.conn.execute(
            "SELECT * FROM investigation_decisions WHERE task_id=?", (bridge.task_id,)
        )
    ]
    effective = effective_records(ws)
    qid = rows[0]["question_id"]
    for changes in (
        {"task_id": "foreign-task"},
        {"question_id": 999999},
        {"revision": 2},
        {"supersedes": qid},
    ):
        with pytest.raises(ValueError):
            source_lineage(bridge.controller, bridge.task_id, effective, rows=[rows[0] | changes])
    with pytest.raises(ValueError, match="Cyclic"):
        source_lineage(
            bridge.controller,
            bridge.task_id,
            replace(effective, question_successors={qid: qid}),
            rows=rows,
        )
    with pytest.raises(ValueError, match="foreign"):
        source_lineage(
            bridge.controller,
            bridge.task_id,
            replace(effective, question_successors={qid: 999999}),
            rows=rows,
        )
    before = AuthoringService(bridge.controller, bridge.root, bridge.task_id).snapshot()
    with pytest.raises(ValueError, match="unchanged premise"):
        source_publication(bridge, receipt, "unchanged-cannot-reopen", predecessor=qid)
    assert AuthoringService(bridge.controller, bridge.root, bridge.task_id).snapshot() == before
    assert not [e for e in ws.events() if e.kind == "authoring.record_applied"]


@pytest.mark.parametrize("delay", ["acquisition", "synthesis"])
def test_sir004_upstream_wait_does_not_consume_repository_work_budget(
    controller, tmp_path, native_fixture, monkeypatch, delay
):
    import threading
    from types import SimpleNamespace

    from harness.authoring import AuthoringService
    from harness.authoring_jobs import claim_next
    from harness.investigation_native import HANDOFFS
    from harness.investigation_repository import source_record
    from harness.investigation_sources import run
    from harness.investigation_store import InvestigationStore
    from tests.investigation_fixture import synthesis

    repo = tmp_path / "timing-repo"
    repo.mkdir()
    (repo / "export.py").write_text("export_csv = True\n")
    started = AuthoringService(controller, repo).start(
        request="AR-2906", request_id="timing-start-" + delay, host="codex"
    )
    service = AuthoringService(controller, repo, started["task"])
    claim = claim_next(controller.conn, "timing-fixture-worker", service.project)
    clock = [0.0]
    monkeypatch.setattr(
        "harness.investigation_repository.time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    calls = []

    class Adapter:
        settlement = "settled"

        def acquire(self, request, **kwargs):
            if delay == "acquisition":
                clock[0] += 11
            return {
                "settlement": "settled",
                "issue": request["issue"],
                "site": request["site"],
                "sources": [source_record("ticket", "fixture-ticket", "Export CSV accounts")],
            }

    class Provider:
        def run(self, inputs, **kwargs):
            calls.append(inputs)
            output = synthesis(inputs)
            if delay == "synthesis" and len(calls) == 1:
                clock[0] += 11
                output["investigation"]["reads"] = [
                    {"kind": "search", "path": "export.py", "text": "export_csv"}
                ]
            return output

    run(
        service,
        claim,
        SimpleNamespace(
            stop=threading.Event(),
            provider_factory=lambda host: Provider(),
            acquisition_factory=lambda qualified: Adapter(),
            handoffs=HANDOFFS,
        ),
    )
    row = service.conn.execute("SELECT * FROM authoring_jobs WHERE id=?", (claim["id"],)).fetchone()
    bundle = InvestigationStore(controller, service.project, service.task_id).read_active()
    assert row["state"] == "succeeded", dict(row)
    assert bundle and bundle["content"]["complete"], bundle
    assert any(s["locator"] == "export.py" for s in bundle["content"]["sources"])
    assert len(calls) == (2 if delay == "synthesis" else 1)


def timed_reader(tmp_path, monkeypatch, costs):
    from types import SimpleNamespace

    from harness import investigation_repository as module

    (tmp_path / "a.py").write_text("bounded_source = True\n")
    clock = [0.0]
    original = module.read_file

    def read(*args, **kwargs):
        clock[0] += costs.pop(0)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module, "read_file", read)
    return module.Reader(tmp_path), clock


def test_sir004_repository_work_is_cumulative_across_excluded_waits(tmp_path, monkeypatch):
    from harness.investigation_policy import AcquisitionError

    reader, clock = timed_reader(tmp_path, monkeypatch, [4, 3, 3])
    assert reader.observe("bounded")["complete"]
    assert reader.elapsed == 4
    clock[0] += 100
    read = [{"kind": "read", "path": "a.py", "offset": 0, "limit": 100}]
    assert reader.additional(read)["complete"]
    assert reader.elapsed == 7
    clock[0] += 100
    with pytest.raises(AcquisitionError) as failure:
        reader.additional(read)
    assert failure.value.reason == "timeout" and reader.elapsed == 10
    assert reader.used["rounds"] == 2
    with pytest.raises(AcquisitionError) as exhausted:
        reader.read("a.py")
    assert exhausted.value.reason == "timeout" and reader.elapsed == 10


def test_sir004_nested_operations_charge_once_and_direct_work_is_bounded(tmp_path, monkeypatch):
    from harness import investigation_repository as module

    reader, clock = timed_reader(tmp_path, monkeypatch, [1, 1, 1])
    original = module.os.scandir

    def scan(*args, **kwargs):
        clock[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module.os, "scandir", scan)
    assert reader.observe("bounded")["complete"]
    assert reader.elapsed == clock[0]  # Nested walk/read intervals count once.
    previous, start = reader.elapsed, clock[0]
    reader.additional([{"kind": "read", "path": "a.py", "offset": 0, "limit": 100}])
    assert reader.elapsed == previous + clock[0] - start
    previous = reader.elapsed
    clock[0] += 100
    start = clock[0]
    reader.read("a.py")
    assert reader.elapsed == previous + clock[0] - start
    direct = module.Reader(tmp_path)
    start = clock[0]
    direct.walk()
    assert direct.elapsed == clock[0] - start and direct.elapsed > 0
    previous = direct.elapsed
    clock[0] += 100
    start = clock[0]
    direct.walk()
    assert direct.elapsed == previous + clock[0] - start


@pytest.mark.parametrize("operation", ["observe", "read", "walk"])
def test_sir004_slow_last_filesystem_operation_cannot_return_complete(
    tmp_path, monkeypatch, operation
):
    from harness import investigation_repository as module
    from harness.investigation_policy import AcquisitionError

    reader, clock = timed_reader(tmp_path, monkeypatch, [11])
    if operation == "walk":
        original = module.os.scandir

        def scan(*args, **kwargs):
            clock[0] += 11
            return original(*args, **kwargs)

        monkeypatch.setattr(module.os, "scandir", scan)
        # An empty directory has no later entry check to discover the elapsed work.
        (tmp_path / "a.py").unlink()
    if operation == "observe":
        result = reader.observe("bounded")
        assert not result["complete"] and "timeout" in result["limitations"]
    else:
        with pytest.raises(AcquisitionError) as failure:
            reader.walk() if operation == "walk" else reader.read("a.py")
        assert failure.value.reason == "timeout"
    assert reader.elapsed == 11


@pytest.mark.parametrize("elapsed", [4, 11])
def test_sir004_cancellation_is_first_and_charges_failed_work(tmp_path, monkeypatch, elapsed):
    from harness import investigation_repository as module
    from harness.investigation_policy import AcquisitionError

    reader, clock = timed_reader(tmp_path, monkeypatch, [])
    cancelled = [False]
    reader.cancelled = lambda: cancelled[0]
    calls = []

    def read(*args, **kwargs):
        calls.append(True)
        clock[0] += elapsed
        cancelled[0] = True
        # Use the actual confined reader, bypassing only the timing test wrapper.
        from harness.execution_files import read_file

        return read_file(*args, **kwargs)

    monkeypatch.setattr(module, "read_file", read)
    with pytest.raises(AcquisitionError) as failure:
        reader.read("a.py")
    assert failure.value.reason == "cancelled" and reader.elapsed == elapsed
    with pytest.raises(AcquisitionError) as cancelled_again:
        reader.read("a.py")
    assert cancelled_again.value.reason == "cancelled" and len(calls) == 1
    cancelled[0] = False
    if elapsed >= 10:
        with pytest.raises(AcquisitionError) as exhausted:
            reader.read("a.py")
        assert exhausted.value.reason == "timeout" and len(calls) == 1


def test_sir004_exception_charges_elapsed_and_does_not_renew_budget(tmp_path, monkeypatch):
    from harness import investigation_repository as module
    from harness.investigation_policy import AcquisitionError

    reader, clock = timed_reader(tmp_path, monkeypatch, [])

    def failed(*args, **kwargs):
        clock[0] += 4
        raise OSError("synthetic filesystem failure")

    monkeypatch.setattr(module, "read_file", failed)
    with pytest.raises(OSError):
        reader.read("a.py")
    assert reader.elapsed == 4

    def slow(*args, **kwargs):
        from harness.execution_files import read_file

        clock[0] += 7
        return read_file(*args, **kwargs)

    clock[0] += 100
    monkeypatch.setattr(module, "read_file", slow)
    with pytest.raises(AcquisitionError) as failure:
        reader.read("a.py")
    assert failure.value.reason == "timeout" and reader.elapsed == 11


@pytest.mark.parametrize("cap", ["files", "results", "hash_bytes", "rounds", "bytes"])
def test_sir004_other_repository_caps_remain_independent(tmp_path, cap):
    from harness.investigation_policy import AcquisitionError
    from harness.investigation_repository import Reader

    (tmp_path / "a.py").write_text("bounded_source = True\n")
    reader = Reader(tmp_path, {cap: 1})
    observation = reader.observe("bounded")
    if cap == "hash_bytes":
        assert not observation["complete"] and "output_limit" in observation["limitations"]
        assert observation["sources"] == []
        return
    read = [{"kind": "read", "path": "a.py", "offset": 0, "limit": 100}]
    if cap == "rounds":
        reader.additional(read)
    with pytest.raises(AcquisitionError) as failure:
        reader.additional(read)
    assert failure.value.reason == "output_limit"


def test_sir004_worker_retains_synthesis_round_limit(session, monkeypatch):
    from tests.investigation_fixture import synthesis

    service, claim, parent = seeded_continuation(session, monkeypatch)
    calls = []

    class Provider:
        def run(self, inputs, **kwargs):
            calls.append(inputs)
            output = synthesis(inputs)
            output["investigation"]["reads"] = [
                {"kind": "read", "path": "export.py", "offset": 0, "limit": 100}
            ]
            return output

    run_claim(service, claim, Provider())
    row = service.conn.execute("SELECT * FROM authoring_jobs WHERE id=?", (claim["id"],)).fetchone()
    assert row["state"] == "failed" and row["settlement"] == "settled"
    assert len(calls) == 3 and row["applied_id"] is None
    assert service.snapshot()["investigation"]["bundle"] == parent
