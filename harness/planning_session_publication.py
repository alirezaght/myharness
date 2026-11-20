"""Closed attached-agent proposals and truthful synchronous source-publication receipts."""

import json
from urllib.parse import urlsplit

from harness.authoring_contract import MAX_BODY, canonical, digest, exact, text
from harness.authoring_store import identity
from harness.db import utcnow
from harness.interview_coverage import validate_citations, validate_evidence, validate_sources
from harness.investigation_policy import parse_issue
from harness.investigation_repository import recheck_observation
from harness.investigation_store import InvestigationStore
from harness.logical_diagram import source_refs, validate_graph
from harness.planning_session_contract import SessionError


def prepare(bridge, request, kind, content):
    if kind != "source_bundle":
        return None
    envelope = content["attached_envelope"]
    exact(
        envelope,
        {
            "schema_version",
            "origin",
            "task",
            "project",
            "attachment_id",
            "epoch",
            "selection",
            "sources",
            "evidence",
            "observations",
            "limitations",
            "captured_base",
            "candidate",
        },
    )
    if (
        envelope["schema_version"] != 3
        or envelope["origin"] != "attached"
        or any(
            envelope[key] != request[key] for key in ("task", "project", "attachment_id", "epoch")
        )
        or envelope["captured_base"] != request["expected"]
    ):
        raise SessionError("SESSION_OWNER_LOST")
    selection = envelope["selection"]
    parsed = parse_issue(bridge.controller.get_task(bridge.task_id).request)
    source_map = validate_sources(envelope["sources"])
    from harness.authoring import AuthoringService
    from harness.investigation_artifacts import derive, reject_source

    policy = derive(AuthoringService(bridge.controller, bridge.root, bridge.task_id))
    for source in source_map.values():
        if source["kind"] == "repository":
            reject_source(policy, source["locator"])

    if selection.get("kind") == "repository":
        exact(selection, {"schema_version", "kind"})
        if (
            type(selection["schema_version"]) is not int
            or selection["schema_version"] != 1
            or parsed
            or not source_map
            or any(source["kind"] != "repository" for source in source_map.values())
        ):
            raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
    else:
        exact(selection, {"issue", "site"})
        site = selection["site"]
        exact(site, {"id", "url"})
        text(site["id"], "site", 200)
        url = urlsplit(site["url"])
        if (
            not parsed
            or selection["issue"] != parsed["issue"]
            or url.scheme != "https"
            or not url.hostname
            or not url.hostname.endswith(".atlassian.net")
            or url.username
            or url.password
            or url.port
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or (parsed["site_url"] and parsed["site_url"] != site["url"])
        ):
            raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
        tickets = [s for s in source_map.values() if s["kind"] == "ticket"]
        if (
            len(tickets) != 1
            or tickets[0]["locator"] != site["url"].rstrip("/") + "/browse/" + parsed["issue"]
        ):
            raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
    validate_evidence(envelope["evidence"], envelope["sources"])
    if envelope["evidence"]["reads"]:
        raise ValueError("complete attached source publication cannot contain pending reads")
    for key in ("observations", "limitations"):
        if not isinstance(envelope[key], list) or len(envelope[key]) > 30:
            raise ValueError("bounded observations required")
        for item in envelope[key]:
            text(item, key, 4000)
    limitations = list(envelope["limitations"])
    for source in source_map.values():
        if source["kind"] != "repository":
            continue
        metadata = source["metadata"]
        required = {
            "truncated",
            "offset",
            "next_offset",
            "page_bytes",
            "size",
            "version",
            "sha256",
            "hash_status",
            "hash_bytes",
            "max_hash_bytes",
        }
        if not required <= metadata.keys():
            raise ValueError("repository observations must come from the confined export reader")
        if (
            metadata["hash_status"] != "complete"
            or metadata["sha256"] is None
            or metadata["truncated"]
            or metadata["offset"] != 0
        ):
            limitations.append("Partial repository source: " + source["locator"])
    envelope = envelope | {"limitations": list(dict.fromkeys(limitations))}
    repository = {
        "managed_policy": policy,
        "sources": [s for s in source_map.values() if s["kind"] == "repository"],
        "complete": not envelope["limitations"],
        "limitations": envelope["limitations"],
        "searched_paths": [s["locator"] for s in source_map.values() if s["kind"] == "repository"],
        "atomic_snapshot": False,
        "origin": "attached",
    }
    recheck_observation(bridge.root, repository)
    candidate = envelope["candidate"]
    exact(candidate, {"body", "graph"})
    text(candidate["body"], "body", MAX_BODY)
    if not candidate["body"].startswith("## "):
        raise ValueError("complete proposed specification required")
    refs = set(source_refs(bridge.controller, bridge.task_id)) | {
        f"investigation:{bridge.task_id}:{s}" for s in source_map
    }
    validate_graph(candidate["graph"], refs)
    return {"envelope": envelope, "repository": repository, "valid_refs": sorted(refs)}


def apply(bridge, service, request, kind, content, prepared):
    if kind in {"task_message", "progress"}:
        contribution = service.store.contribution(
            "assistant" if kind == "task_message" else "progress", "attached_agent", content
        )
        return {"contribution": contribution, **content, "origin": "attached"}
    if kind == "candidate":
        text(content["body"], "body", MAX_BODY)
        if not content["body"].startswith("## "):
            raise ValueError("complete proposed specification required")
        validate_graph(content["graph"], source_refs(service.controller, service.task_id))
        revision = service.store.add_revision(
            content["body"],
            actor="model_proposal",
            parent=service.store.base()["working"],
            graph=content["graph"],
            obligations=["Review attached-agent specification and graph proposal"],
        )
        service.store.bump()
        return {"revision": revision, "origin": "attached"}
    if kind == "user_response":
        from harness.authoring_context import resolve_target

        record = resolve_target(service, content["target"], content["target_revision"])
        if record["kind"] == "question_response":
            args = {
                "expected": service.store.base(),
                "request_id": request["request_id"],
                "question": int(content["target"].split(":")[1]),
                "mode": content["mode"],
                "answer": content["text"],
            }
            if content["mode"] == "defer":
                args.update(source=content["source"], ref=content["ref"])
            result = service.answer(**args)
        elif record["kind"] == "recommendation_decision" and content["mode"] in {
            "accepted",
            "rejected",
            "modified",
        }:
            result = service.decide(
                expected=service.store.base(),
                request_id=request["request_id"],
                proposal=int(content["target"].split(":")[1]),
                status=content["mode"],
                response=content["text"],
            )
        else:
            raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
        return {
            "target": content["target"],
            "target_revision": content["target_revision"],
            "mode": content["mode"],
            "text": content["text"],
            "contribution": result["contribution"],
        }
    if kind == "question_proposal":
        if content["citations"] == []:
            from harness.phases import Phase

            ws = service.controller.workspace(service.task_id)
            if service.controller.get_task(
                service.task_id
            ).phase is not Phase.INTERVIEW or not ws.latest_checkpoint("investigation"):
                raise SessionError("SESSION_INTERVIEW_PREREQUISITE")
            text(content["rationale"], "rationale", 4000)
            from harness.investigation_concerns import definitions

            catalogue = definitions()
            if (
                content["concern"] not in catalogue
                or content["supersedes"] is not None
                or content["changed_premise_reason"] is not None
            ):
                raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
            previous = next(
                (
                    e
                    for e in reversed(ws.events())
                    if e.kind == "session.clarification_proposed"
                    and e.payload["concern"] == content["concern"]
                ),
                None,
            )
            if previous:
                question = ws.question(previous.payload["question"])
            else:
                question = ws.ask(
                    catalogue[content["concern"]].topic, content["text"], kind="decision"
                )
                ws.record_event(
                    "session.clarification_proposed",
                    {
                        "question": question.id,
                        "concern": content["concern"],
                        "rationale": content["rationale"],
                        "origin": "attached_agent",
                        "citations": [],
                    },
                )
                service.store.bump()
            return {
                "question": question.id,
                "text": question.text,
                "origin": "attached",
                "citations": [],
            }
        store = InvestigationStore(service.controller, service.project, service.task_id)
        bundle = store.read_active()
        sources = validate_sources(bundle["content"]["sources"]) if bundle else {}
        validate_citations(content["citations"], sources)
        text(content["rationale"], "rationale", 4000)
        if content["supersedes"] is not None:
            text(content["changed_premise_reason"], "changed premise reason", 4000)
        evidence = {
            "decisions": [
                {
                    "concern": content["concern"],
                    "text": content["text"],
                    "citations": content["citations"],
                    "supersedes": content["supersedes"],
                    "reason": content["rationale"],
                }
            ],
            "coverage": [
                {
                    "key": content["concern"],
                    "disposition": "decision",
                    "statement": content["text"],
                    "rationale": content["rationale"],
                    "citations": content["citations"],
                }
            ],
            "reads": [],
        }
        validate_evidence(evidence, list(sources.values()))
        store._decisions(service, evidence, sources)
        service.store.bump()
        return {
            "question": next(
                d["question_id"] for d in store.decisions() if d["concern"] == content["concern"]
            ),
            "text": content["text"],
            "origin": "attached",
        }
    return _publish_sources(bridge, service, request, prepared)


def _publish_sources(bridge, service, request, prepared):
    envelope = prepared["envelope"]
    from harness.investigation_artifacts import require_policy

    require_policy(service, prepared["repository"]["managed_policy"])
    epoch = bridge.store.epoch()
    provenance = {
        "origin": "attached",
        "provider": epoch["provider"],
        "root_session_id": epoch["root_session_id"],
        "current_thread_id": epoch["current_thread_id"],
        "attachment_id": epoch["attachment_id"],
        "epoch": epoch["epoch"],
        "request_id": request["request_id"],
        "event_ids": request["consumes"],
        "captured_base": request["expected"],
        "provider_invoked": False,
        "process_started": False,
    }
    selection = {
        "schema_version": 1,
        "task": service.task_id,
        "project": service.project,
        "repository": str(service.root),
        "issue": envelope["selection"].get("issue"),
        "site": envelope["selection"].get("site"),
        "policy": digest(provenance),
        "fields": ["summary", "description", "status", "updated"],
        "reconnect_available": False,
    }
    if envelope["selection"].get("kind") == "repository":
        selection = {
            "schema_version": 1,
            "kind": "repository",
            "task": service.task_id,
            "project": service.project,
            "repository": str(service.root),
            "policy": digest(provenance),
            "reconnect_available": False,
        }
    store = InvestigationStore(service.controller, service.project, service.task_id)
    old = store.binding()
    self_parent = old["active_bundle"] if old else None
    job = identity()
    inputs = {
        "provenance": provenance,
        "investigation": {"request": selection},
        "base": request["expected"]["authoring"],
    }
    service.conn.execute(
        (
            "INSERT INTO "
            "authoring_jobs(id,task_id,project,kind,host,input_json,state,sett"
            "lement,created_at) "
            "VALUES(?,?,?,'attached_publication','attached',?,'succeeded','set"
            "tled',?)"
        ),
        (job, service.task_id, service.project, canonical(inputs), utcnow()),
    )
    service.conn.execute(
        (
            "INSERT INTO "
            "authoring_attempts(job_id,attempt,claim,owner,state,settlement,in"
            "put_json) VALUES(?,1,0,NULL,'succeeded','settled',?)"
        ),
        (job, canonical(inputs)),
    )
    service.conn.execute(
        "INSERT INTO planning_session_publications VALUES(?,?,?,?,?,?,?)",
        (
            service.task_id,
            service.project,
            epoch["epoch"],
            epoch["attachment_id"],
            job,
            1,
            request["request_id"],
        ),
    )
    service.conn.execute(
        (
            "INSERT INTO "
            "investigation_bindings(task_id,project,selection_json) "
            "VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "selection_json=excluded.selection_json,validation_generation=vali"
            "dation_generation+1,recovery_json=NULL"
        ),
        (service.task_id, service.project, canonical(selection)),
    )
    material = {
        "request": selection,
        "parent": self_parent,
        "sources": envelope["sources"],
        "evidence": envelope["evidence"],
        "repository": prepared["repository"],
        "complete": prepared["repository"]["complete"],
        "limitations": envelope["limitations"],
        "settlement": "settled",
        "body": envelope["candidate"]["body"],
        "graph": envelope["candidate"]["graph"],
        "assistant": "Attached agent supplied source observations and a proposed specification.",
        "valid_refs": prepared["valid_refs"],
        "origin": "attached",
        "provenance": provenance,
        "observations": envelope["observations"],
    }
    row = dict(service.conn.execute("SELECT * FROM authoring_jobs WHERE id=?", (job,)).fetchone())
    contribution = store.insert_bundle(service, row, material, request["expected"]["authoring"])
    service.store.bump()
    result = json.loads(
        service.conn.execute(
            "SELECT result_json FROM authoring_jobs WHERE id=?", (job,)
        ).fetchone()[0]
    )
    return {
        "job": job,
        "label": "Attached agent publication",
        "contribution": contribution,
        "origin": "attached",
        "provider_invoked": False,
        "process_started": False,
        "revision": result["revision"],
        "bundle": result["bundle"],
    }
