"""Server-owned acquisition orchestration over the existing authoring job scheduler."""

import json

from harness.authoring_contract import AuthoringConflict, canonical, validate_context
from harness.db import transaction
from harness.investigation_policy import (
    AcquisitionError,
    authorize_request,
    parse_issue,
)
from harness.investigation_store import InvestigationStore


def start(service, host, site=None, *, reconnect=False, next_generation=True):
    service.store.writing()
    if host not in {"codex", "claude"} or (
        site is not None and (not isinstance(site, str) or len(site) > 200)
    ):
        raise ValueError("invalid investigation host or site selection")
    from harness.authoring_jobs import queue_job
    from harness.investigation_eligibility import require_eligible

    require_eligible(service)
    from harness.planning_session_events import require_standalone

    require_standalone(service.conn, service.task_id)
    from harness.investigation_artifacts import bind

    bind(service)
    store = InvestigationStore(service.controller, service.project, service.task_id)
    parsed = parse_issue(service.controller.get_task(service.task_id).request)
    if not parsed:
        raise AcquisitionError("scope_denied")
    selected, error = None, None
    from harness.investigation_policy import capability

    qualified = capability()
    try:
        selected = authorize_request(
            service, site, qualified, purpose="reconnect" if reconnect else "read"
        )
        if reconnect and not qualified.reconnect_qualified:
            raise AcquisitionError("reconnect_unavailable")
    except AcquisitionError as failure:
        error = failure.public()
        if failure.reason == "site_selection_required" and qualified:
            error["site_choices"] = [
                {"id": s["id"], "url": s["url"]} for s in qualified.sites if s.get("verified")
            ]
    old = store.binding()
    pending = service.conn.execute(
        (
            "SELECT * FROM authoring_jobs WHERE task_id=? AND project=? "
            "AND kind IN ('source_investigation','jira_reconnect') AND "
            "state IN ('queued','running') ORDER BY rowid DESC LIMIT 1"
        ),
        (service.task_id, service.project),
    ).fetchone()
    if pending:
        raise AuthoringConflict(
            "An investigation is already active; cancel and settle it before refreshing"
        )
    selection = selected or {
        "schema_version": 1,
        "task": service.task_id,
        "project": service.project,
        "repository": str(service.root),
        "issue": parsed["issue"],
        "site": None,
        "policy": None,
        "fields": [],
        "reconnect_available": bool(qualified and qualified.reconnect_qualified),
    }
    service.conn.execute(
        (
            "INSERT INTO "
            "investigation_bindings(task_id,project,selection_json,recovery_json)"
            " VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            + (
                "selection_json=excluded.selection_json,recovery_json=excluded.rec"
                "overy_json,validation_generation=investigation_bindings.validatio"
                "n_generation+1"
            )
        ),
        (
            service.task_id,
            service.project,
            canonical(selection),
            canonical(error) if error else None,
        ),
    )
    if error:
        return {"job": None, "state": "unavailable", "acquisition_recovery": error}
    ident = queue_job(
        service,
        "jira_reconnect" if reconnect else "source_investigation",
        host,
        next_generation=next_generation,
    )
    return {"job": ident, "state": "queued", "parent_bundle": old["active_bundle"] if old else None}


def snapshot(service):
    store = InvestigationStore(service.controller, service.project, service.task_id)
    binding = store.binding()
    if not binding:
        return {"enabled": False}
    from harness.interview_coverage import current_coverage
    from harness.investigation_artifacts import derive

    bundle = store.read_active()
    row = service.conn.execute(
        (
            "SELECT * FROM authoring_jobs WHERE task_id=? AND project=? "
            "AND kind IN ('source_investigation','jira_reconnect') ORDER "
            "BY rowid DESC LIMIT 1"
        ),
        (service.task_id, service.project),
    ).fetchone()
    progress = store.read_attempt(row["id"], row["attempt"]) if row else None
    content = bundle["content"] if bundle else {}
    recovery = binding["recovery"] or (progress["diagnostic"] if progress else None)
    from harness.planning_session_store import PlanningSessionStore

    owner = PlanningSessionStore(service.conn, service.task_id).owner()
    attached = bool(owner and owner["mode"] == "attached")
    attached_bundle = content.get("origin") == "attached"
    standalone_recovery = recovery
    standalone_job = row["id"] if row else None
    if attached_bundle:
        row = service.conn.execute(
            "SELECT * FROM authoring_jobs WHERE id=? AND task_id=? AND kind='attached_publication'",
            (bundle["job_id"], service.task_id),
        ).fetchone()
        progress = None
    if attached:
        recovery = None
    synthesis_recovery = (
        dict(recovery, host=row["host"], state="failed")
        if recovery and recovery.get("reason") == "model_unavailable" and row
        else None
    )
    return {
        "enabled": True,
        "selection": binding["selection"],
        "bundle": bundle["id"] if bundle else None,
        "job": row["id"] if row else None,
        "attempt": row["attempt"] if row else None,
        "state": "succeeded"
        if attached_bundle
        else "attached"
        if attached
        else row["state"]
        if row
        else "unavailable",
        "stage": "ready"
        if attached_bundle
        else "waiting_for_attached_agent"
        if attached
        else progress["stage"]
        if progress
        else "unavailable",
        "origin": "attached" if attached_bundle else "standalone",
        "standalone_recovery": standalone_recovery,
        "standalone_job": standalone_job,
        "provenance": content.get("provenance"),
        "observations": content.get("observations", []),
        "completed_stages": progress["completed"] if progress else [],
        "recovery": None if synthesis_recovery else recovery,
        "synthesis_recovery": synthesis_recovery,
        "attempt_observations": progress["observations"] if progress else None,
        "decisions": store.decisions(),
        "sources": content.get("sources", []),
        "findings": content.get("evidence", {}).get("coverage", []),
        "limitations": content.get("limitations", []),
        "coverage": current_coverage(
            service.controller, service.task_id, managed_policy=derive(service)
        ).to_dict(),
        "review_available": bundle is not None,
        "reconnect": {
            "available": bool(binding["selection"].get("reconnect_available")) and not attached,
            "reason": None
            if binding["selection"].get("reconnect_available")
            else "bootstrap_and_oauth_qualification_required",
            "scope": ["read:jira:agent-interface"],
            "disclosure": (
                "Reconnect uses native consent and may update this "
                "connection's authentication outside this task."
            ),
        },
    }


def context(service):
    store = InvestigationStore(service.controller, service.project, service.task_id)
    binding = store.binding()
    if not binding:
        return None
    from dataclasses import asdict

    from harness.effective_records import effective_records
    from harness.interview_coverage import coverage_entries

    bundle = store.read_active()
    captured = {}
    if bundle:
        from harness.interview_coverage import validate_sources

        validate_sources(bundle["content"]["sources"])
        captured = {
            "sources": bundle["content"]["sources"],
            "repository": bundle["content"]["repository"],
            "parent_hash": bundle["content_hash"],
            "acquisition": {
                "kind": "saved_bundle",
                "bundle": bundle["id"],
                "content_hash": bundle["content_hash"],
                "origin": bundle["content"].get("origin", "standalone"),
                "fresh_remote_verification": False,
            },
        }
    return {
        **captured,
        "schema_version": 2,
        "request": binding["selection"],
        "parent": binding["active_bundle"],
        "validation_generation": binding["validation_generation"],
        "catalogue": [asdict(e) for e in coverage_entries(service.controller, service.task_id)],
        "decisions": store.decisions(
            effective_records(service.controller.workspace(service.task_id))
        ),
    }


def run(service, claim, worker):
    from harness.authoring_jobs import job_row, matches, require_available, settle
    from harness.authoring_native import validate_output
    from harness.authoring_policy import availability
    from harness.investigation_repository import Reader, recheck_observation
    from harness.logical_diagram import source_refs

    inputs = json.loads(claim["input_json"])
    request = inputs["investigation"]["request"]
    store = InvestigationStore(service.controller, service.project, service.task_id)

    def cancelled():
        return worker.stop.is_set() or not matches(job_row(service, claim["id"]), claim)

    def progress(stage, observations=None):
        with transaction(service.conn):
            store.progress(claim, stage, observations=observations)

    acquired, adapter = None, None
    try:
        if service.store.base() != inputs["base"]:
            raise AcquisitionError("source_changed")
        from harness.investigation_artifacts import require_policy
        from harness.investigation_eligibility import require_eligible
        from harness.planning_session_events import require_standalone

        require_eligible(service)
        require_standalone(
            service.conn, service.task_id, inputs.get("planning_owner"), check_captured=True
        )
        managed_policy = require_policy(service, inputs["managed_artifacts"])
        reader = Reader(service.root, cancelled=cancelled, managed_policy=managed_policy)
        continuation = claim["kind"] == "generate"
        if continuation:
            captured = inputs["investigation"]
            binding, parent = store.binding(), store.read_active()
            if (
                not binding
                or not parent
                or binding["selection"] != request
                or parent["id"] != captured["parent"]
                or parent["content_hash"] != captured["parent_hash"]
                or binding["validation_generation"] != captured["validation_generation"]
                or parent["content"]["sources"] != captured["sources"]
                or parent["content"]["repository"] != captured["repository"]
            ):
                raise AcquisitionError("source_changed")
            from harness.interview_coverage import validate_sources

            validate_sources(captured["sources"])
            repository = captured["repository"]
            if repository.get("managed_policy") != managed_policy:
                raise AcquisitionError("source_changed")
            recheck_observation(service.root, repository)
            reader.walk()
            reader.sources = list(repository["sources"])
            reader.limitations = list(repository["limitations"])
            # Continuing a bundle retains the original bounded observations and budgets.
            retained = repository.get(
                "counts",
                {
                    "files": len(reader.sources),
                    "bytes": sum(s["metadata"]["page_bytes"] for s in reader.sources),
                    "hash_bytes": sum(s["metadata"]["hash_bytes"] for s in reader.sources),
                    "results": len(reader.sources),
                },
            )
            for key, amount in retained.items():
                if key != "entries":
                    reader.charge(key, amount)
            acquired = {
                "sources": [s for s in captured["sources"] if s["kind"] != "repository"],
                "provenance": captured["acquisition"],
            }
            sources = list(captured["sources"])
        else:
            from harness.investigation_policy import capability

            qualified = capability()
            actual = authorize_request(
                service,
                request["site"]["id"] if request["site"] else None,
                qualified,
                purpose="reconnect" if claim["kind"] == "jira_reconnect" else "read",
            )
            if actual != request:
                raise AcquisitionError("scope_denied")
            adapter = worker.acquisition_factory(qualified)
            progress("acquiring_ticket")
            if claim["kind"] == "jira_reconnect":
                acquired = adapter.reconnect(
                    request,
                    cancelled=cancelled,
                    publish_handoff=lambda value: worker.handoffs.publish(claim, value),
                )
            else:
                acquired = adapter.acquire(request, cancelled=cancelled, progress=progress)
            if acquired.get("reconnected"):
                progress(
                    "authentication_completed",
                    {
                        "reconnected": True,
                        "tool_schema": acquired["tool_schema"],
                        "read_qualified": False,
                    },
                )
                raise AcquisitionError("unqualified_transport")
            if (
                acquired["settlement"] != "settled"
                or acquired["issue"] != request["issue"]
                or acquired["site"] != request["site"]
            ):
                raise AcquisitionError("scope_denied")
            progress("inspecting_repository", acquired)
            repository = reader.observe(canonical(acquired["sources"]))
            sources = acquired["sources"] + repository["sources"]
        progress("synthesizing", {"ticket": acquired, "repository": repository})
        require_available(claim["host"], availability(claim["host"]))
        provider = worker.provider_factory(claim["host"])
        for turn in range(3):
            if cancelled():
                raise AcquisitionError("cancelled")
            synthesis = dict(inputs)
            synthesis["investigation"] = dict(
                inputs["investigation"],
                sources=sources,
                round=turn,
                observation={k: v for k, v in repository.items() if k != "sources"},
            )
            synthesis["valid_refs"] = sorted(
                set(source_refs(service.controller, service.task_id))
                | {f"investigation:{service.task_id}:{s['id']}" for s in sources}
            )
            validate_context(synthesis)
            output = validate_output(provider.run(synthesis, cancelled=cancelled), synthesis)
            evidence = output["investigation"]
            if not evidence["reads"]:
                break
            if turn == 2:
                raise AcquisitionError("output_limit")
            repository = reader.additional(evidence["reads"])
            sources = acquired["sources"] + repository["sources"]
        if cancelled():
            raise AcquisitionError("cancelled")
        recheck_observation(service.root, repository)
        prepared = {
            "request": request,
            **({"acquisition": inputs["investigation"]["acquisition"]} if continuation else {}),
            "handling": output.get("handling", []),
            "parent": inputs["investigation"]["parent"],
            "sources": sources,
            "evidence": evidence,
            "repository": repository,
            "complete": repository["complete"],
            "limitations": repository["limitations"],
            "settlement": "settled",
            "body": output["body"],
            "graph": output["graph"]["concrete"],
            "assistant": output["assistant"],
            "valid_refs": synthesis["valid_refs"],
        }
        with transaction(service.conn):
            store.publish(service, claim, prepared, inputs["base"])
    except (OSError, ValueError, LookupError) as failure:
        reason = (
            failure
            if isinstance(failure, AcquisitionError)
            else AcquisitionError(
                "model_unavailable"
                if acquired and "unavailable" in str(failure).lower()
                else "protocol_error"
            )
        )
        settlement = (
            getattr(adapter, "settlement", reason.settlement) if adapter else reason.settlement
        )
        with transaction(service.conn):
            row = job_row(service, claim["id"])
            if matches(row, claim):
                current = service.store.base() == inputs["base"]
                diagnostic = dict(reason.public(), settlement=settlement)
                store.progress(claim, "failed", diagnostic=diagnostic)
                if current:
                    service.conn.execute(
                        "UPDATE investigation_bindings SET recovery_json=? WHERE task_id=?",
                        (canonical(diagnostic), service.task_id),
                    )
                settle(
                    service,
                    claim,
                    "failed" if current else "stale",
                    str(reason),
                    settlement=settlement,
                )
            elif row["state"] == "cancelled" and row["attempt"] == claim["attempt"]:
                settle(service, claim, "cancelled", settlement=settlement)

    finally:
        worker.handoffs.discard(claim)
        if adapter:
            with transaction(service.conn):
                service.conn.execute(
                    (
                        "UPDATE authoring_attempts SET settlement=? WHERE job_id=? "
                        "AND attempt=? AND claim=? AND owner=?"
                    ),
                    (
                        adapter.settlement,
                        claim["id"],
                        claim["attempt"],
                        claim["claim"],
                        claim["owner"],
                    ),
                )
