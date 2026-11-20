"""Persistent user-owned authoring, isolated from execution and explicit adoption."""

import secrets
import sqlite3
from dataclasses import asdict
from pathlib import Path

from harness.authoring_contract import MAX_BODY, AuthoringConflict, canonical, digest, text
from harness.authoring_store import AuthoringStore, adopted_revision, authority_state, source_state
from harness.db import transaction
from harness.design import ArchitectureDesigner
from harness.interview import InterviewEngine
from harness.phases import Phase, is_backward, next_phase
from harness.spec_manager import SpecificationManager
from harness.testplan import TestPlanner


class NonAnswer(ValueError):
    pass


class TransactionInterview(InterviewEngine):
    _mutation = InterviewEngine.mutation_in_transaction


class TransactionDesign(ArchitectureDesigner):
    _mutation = ArchitectureDesigner.mutation_in_transaction


class TransactionTestPlan(TestPlanner):
    _mutation = TestPlanner.mutation_in_transaction


def project_identity(root):
    return digest({"repository": str(Path(root).resolve())})


class AuthoringService:
    def __init__(self, controller, root, task_id=None, *, specs=None):
        self.controller, self.conn = controller, controller.conn
        self.root = Path(root).resolve()
        self.project = project_identity(self.root)
        self.task_id = task_id
        self.specs = specs
        self.store = AuthoringStore(controller, self.project, task_id) if task_id else None
        if task_id:
            checkpoint = controller.workspace(task_id).latest_checkpoint("investigation")
            recorded = checkpoint.payload.get("repo_root") if checkpoint else None
            if recorded and Path(recorded).resolve() != self.root:
                raise LookupError("task repository differs from configured project")

    def manager(self):
        from harness.cli_spec import spec_store
        from harness.investigation_artifacts import stored

        policy = stored(self.conn, self.task_id)

        return SpecificationManager(
            self.controller,
            self.task_id,
            spec_store(
                self.controller,
                self.task_id,
                self.specs
                or (policy["specs_root"] if policy else str(self.root / "docs" / "specs")),
            ),
            self.root,
        )

    def snapshot(self):
        if not self.task_id:
            return {
                "project": {"id": self.project, "repository": str(self.root)},
                "task": None,
                "initialized": False,
            }
        store = self.store
        binding = store.binding()
        ws = self.controller.workspace(self.task_id)

        def rows(table):
            import json

            result = []
            for row in self.conn.execute(
                f"SELECT * FROM {table} WHERE task_id=? ORDER BY rowid DESC LIMIT 200",
                (self.task_id,),
            ):
                value = dict(row)
                for name in list(value):
                    if name.endswith("_json"):
                        value[name[:-5]] = json.loads(value.pop(name)) if value[name] else None
                result.append(value)
            return list(reversed(result))

        from harness.authoring_context import projection, question_presentation

        context = projection(self)
        targets = context["revision_targets"]
        from harness.effective_records import effective_records

        semantic = effective_records(ws)
        contributions = rows("authoring_contributions")
        question_revisions = {str(q.id): digest(asdict(q)) for q in ws.questions()}
        for contribution in self.conn.execute(
            "SELECT id,json_extract(payload_json,'$.target') AS target FROM "
            "authoring_contributions WHERE task_id=? AND kind='question_revision' ORDER BY rowid",
            (self.task_id,),
        ):
            question_revisions[contribution["target"].split(":")[1]] = contribution["id"]
        from harness.planning_session_store import PlanningSessionStore

        session = PlanningSessionStore(self.conn, self.task_id, self.project).snapshot(
            repository=str(self.root)
        )
        jobs = rows("authoring_jobs")
        for job in jobs:
            job.pop("input", None)
            if job["kind"] == "attached_publication":
                job["label"] = "Attached agent publication"
                job["origin"] = "attached"
                job["provider_invoked"] = False
                job["process_started"] = False
            result = job.pop("result", None)
            job["candidate"] = result.get("revision") if result else None
            job["content_complete"] = bool(result and result.get("output", {}).get("graph"))
            job.pop("owner", None)
        from harness.authoring_context import model_recovery

        recovery = model_recovery(ws, [j for j in jobs if j["kind"] != "attached_publication"])
        from harness.investigation_sources import snapshot as investigation_snapshot

        investigation = investigation_snapshot(self)
        if investigation.get("synthesis_recovery"):
            recovery = investigation["synthesis_recovery"]
        from harness.investigation_lineage import source_lineage

        source_history = source_lineage(self.controller, self.task_id, semantic)
        retired_source_ids = set(source_history["retired_ids"])
        physical_questions = {q.id: q for q in ws.questions()}
        historical_questions = {
            q.id: q for q in semantic.historical_questions if q.id in semantic.question_successors
        }
        historical_questions.update(
            {ident: physical_questions[ident] for ident in retired_source_ids}
        )
        historical_proposals = {
            p.id: p
            for p in semantic.historical_proposals
            if p.id in semantic.proposal_successors or p.question_id in retired_source_ids
        }
        working = store.revision(binding["working_id"]) if binding else None
        adopted = adopted_revision(self.controller, self.task_id)
        manager = self.manager()
        approved = manager.store.current()
        current_source = digest(source_state(self.controller, self.task_id))
        return {
            "project": {"id": self.project, "repository": str(self.root)},
            "task": self.task_id,
            "phase": str(self.controller.get_task(self.task_id).phase),
            "initialized": binding is not None,
            "session": session,
            "standalone_model_recovery": recovery,
            "base": store.base(),
            "working": working,
            "adopted": adopted,
            "effective_body": manager.body(),
            "approved": {
                "body": approved.body,
                "version": approved.version,
                "hash": approved.content_hash,
            }
            if approved
            else None,
            "execution": authority_state(self.controller, self.task_id)["execution"],
            "interview": InterviewEngine(self.controller, self.task_id).question_projection(),
            "questions": [
                asdict(q) | question_presentation(q, self.controller.get_task(self.task_id).request)
                for q in semantic.questions
                if q.id not in retired_source_ids
            ],
            "recommendations": [
                asdict(p) for p in semantic.proposals if p.question_id not in retired_source_ids
            ],
            "historical": {
                "questions": [asdict(q) for q in historical_questions.values()],
                "recommendations": [asdict(p) for p in historical_proposals.values()],
            },
            "source_lineage": source_history,
            "effective_lineage": context["lineage"],
            "contributions": contributions,
            "question_revisions": question_revisions,
            "revision_targets": targets,
            "jobs": jobs,
            "model_recovery": None if session["mode"] == "attached" else recovery,
            "investigation": investigation,
            "revisions": [
                {key: value for key, value in row.items() if key not in {"body", "source"}}
                for row in rows("authoring_revisions")
            ],
            "history_limit": 200,
            "history_truncated": self.conn.execute(
                "SELECT count(*) FROM authoring_contributions WHERE task_id=?", (self.task_id,)
            ).fetchone()[0]
            > 200,
            "projection": dict(
                self.conn.execute(
                    "SELECT * FROM authoring_projections WHERE task_id=?", (self.task_id,)
                ).fetchone()
                or {}
            ),
            "findings": self.findings(working),
            "source_changed": bool(working and working["source_digest"] != current_source),
            "planning": {
                "design": [asdict(e) for e in ws.design_entries()],
                "scenarios": [asdict(s) for s in ws.scenarios()],
                "test_plan": TestPlanner(self.controller, self.task_id).document().to_dict(),
            },
            "actions": [
                "answer",
                "revise",
                "revise-record",
                "decide",
                "followup",
                "save-spec",
                "regenerate",
                "reconcile-preview",
                "reconcile",
                "cancel-job",
                "retry-job",
                "planning",
            ],
        }

    def history(self, *, before=9223372036854775807, limit=100):
        import json

        if type(before) is not int or before < 1 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid history cursor")
        rows = self.conn.execute(
            "SELECT rowid AS cursor,* FROM authoring_contributions WHERE task_id=? AND rowid<"
            "? ORDER BY rowid DESC LIMIT ?",
            (self.task_id, before, limit + 1),
        ).fetchall()
        records = []
        for row in rows[:limit]:
            record = dict(row)
            record["payload"] = json.loads(record.pop("payload_json"))
            records.append(record)
        return {
            "records": records,
            "next_before": records[-1]["cursor"] if len(rows) > limit else None,
        }

    def findings(self, revision):
        if revision is None:
            return ["No working revision; start the interview or save a specification"]
        findings = list(revision["obligations"])
        if not revision["body"].startswith("## "):
            findings.append('Specification body must begin with "## " before adoption')
        return findings

    def _initial(self):
        self.store.initialize()
        binding = self.store.binding()
        if binding["working_id"] is None:
            ident = self.store.add_revision(self.manager().body(proposed=True), actor="system")
            self.conn.execute(
                "UPDATE authoring_tasks SET working_id=? WHERE task_id=?", (ident, self.task_id)
            )

    def start(self, *, request_id, host, request=None, expected=None):
        if host not in {"codex", "claude"}:
            raise ValueError("invalid host")
        payload = {
            "action": "start",
            "host": host,
            "request": request,
            "expected": expected,
            "task": self.task_id,
        }
        if self.task_id:
            return self._mutate(
                "start",
                expected,
                request_id,
                {"host": host},
                lambda: self._start_existing(host),
            )
        text(request, "request", 16000)
        for attempt in range(3):
            try:
                return self._reserve_start(request_id, host, request, payload)
            except sqlite3.IntegrityError as error:
                if "tasks.id" not in str(error) or attempt == 2:
                    raise

    def _reserve_start(self, request_id, host, request, payload):
        from harness.authoring_jobs import queue_job
        from harness.authoring_policy import availability

        scope = "startup:" + self.project
        with transaction(self.conn):
            import json

            from harness.authoring_contract import request_identity

            request_identity(request_id)
            prior = self.conn.execute(
                "SELECT * FROM authoring_receipts WHERE scope=? AND request_id=?",
                (scope, request_id),
            ).fetchone()
            if prior:
                if prior["payload_hash"] != digest(payload):
                    raise AuthoringConflict("request_id already used with different content")
                return json.loads(prior["result_json"])
            task = self.controller.create_task_in_transaction(
                request, task_id="t-" + secrets.token_hex(4)
            )
            service = AuthoringService(self.controller, self.root, task.id, specs=self.specs)
            service._initial()
            service.store.bump()
            from harness.investigation_policy import parse_issue

            if parse_issue(request):
                from harness.investigation_sources import start

                prepared = start(service, host, next_generation=False)
                job = prepared.get("job")
            else:
                job = queue_job(service, "investigate", host)
            result = {
                "task": task.id,
                "base": service.store.base(),
                "job": job,
                "host": availability(host),
            }
            service.store.record_receipt(request_id, payload, result, scope=scope)
            return result

    def _start_existing(self, host):
        from harness.authoring_startup import start_existing

        return start_existing(self, host)

    def _mutate(self, action, expected, request_id, payload, change):
        if not self.store:
            raise ValueError("explicit task required")
        from harness.planning_session_events import current_scope, mutation_scope

        scope = current_scope(self.conn, self.task_id)
        if self.conn.in_transaction and scope and scope["origin"] in {"attached_agent", "tui_user"}:
            return self._mutate_in_transaction(action, expected, request_id, payload, change)
        from harness.authoring_transaction import committing
        from harness.decision_workflow import discard_on_failure
        from harness.registry_mutation import registry_mutation

        with (
            registry_mutation() as mutations,
            discard_on_failure() as written,
            committing(self.conn),
            mutation_scope(self.conn, self.task_id),
        ):
            self._decision_records, self._registry_mutations = written, mutations
            return self._mutate_in_transaction(action, expected, request_id, payload, change)

    def _mutate_in_transaction(self, action, expected, request_id, payload, change):
        if not self.conn.in_transaction:
            raise RuntimeError("authoring mutation requires its caller transaction")
        from harness.planning_session_events import current_scope
        from harness.planning_session_store import PlanningSessionStore

        complete = {"action": action, "expected": expected, "payload": payload}
        prior = self.store.receipt(request_id, complete)
        if prior is not None:
            return prior
        self.store.check(expected)
        if action in {"investigate", "reconnect-jira"}:
            from harness.investigation_eligibility import require_eligible
            from harness.planning_session_events import require_standalone

            require_eligible(self)
            require_standalone(self.conn, self.task_id)
        self._initial()
        result = change() or {}
        from harness.investigation_artifacts import bind

        bind(self)
        no_bump = result.pop("_no_bump", False)
        if not no_bump:
            self.store.bump()
        result = {"task": self.task_id, "base": self.store.base(), **result}
        scope = current_scope(self.conn, self.task_id)
        if not no_bump and scope and scope["origin"] == "browser":
            public_input = dict(payload)
            if action in {"save-spec", "revise"}:
                public_input.pop("body", None)
            event_id = PlanningSessionStore(self.conn, self.task_id, self.project).append_event(
                "authoring." + action,
                {
                    "request_id": request_id,
                    "result": result,
                    "input": public_input,
                    "workspace_events": scope["events"],
                },
                origin="browser",
                canonical_reference="authoring:" + request_id,
            )
            if event_id:
                result["session_event"] = event_id
        self.store.record_receipt(request_id, complete, result)
        return result

    def _append_working(self, body, *, graph_id=None, obligations=(), actor="developer"):
        previous = self.store.revision(self.store.binding()["working_id"])
        ident = self.store.add_revision(
            body, actor=actor, parent=previous["id"], graph_id=graph_id, obligations=obligations
        )
        self.conn.execute(
            "UPDATE authoring_tasks SET working_id=? WHERE task_id=?", (ident, self.task_id)
        )
        return ident

    def save_spec(self, *, expected, request_id, body):
        text(body, "body", MAX_BODY, empty=True)

        def change():
            previous = self.store.revision(self.store.binding()["working_id"])
            obligations = list(previous["obligations"])
            if body != previous["body"] and previous["graph_id"]:
                obligations = sorted(
                    set(obligations + ["Confirm graph/spec consistency during reconciliation"])
                )
            ident = self._append_working(
                body, graph_id=previous["graph_id"], obligations=obligations
            )
            return {"revision": ident}

        return self._mutate("save-spec", expected, request_id, {"body": body}, change)

    def answer(self, *, expected, request_id, question, mode, answer="", source=None, ref=None):
        if type(question) is not int or mode not in {"answer", "unknown", "defer"}:
            raise ValueError("invalid question or answer mode")
        text(answer, "answer", 16000, empty=mode != "answer")
        from harness.interview import _is_non_answer

        if mode == "answer" and _is_non_answer(answer):
            raise NonAnswer("Use I don’t know yet to save a non-answer as unresolved")
        payload = {
            "question": question,
            "mode": mode,
            "answer": answer,
            "source": source,
            "ref": ref,
        }

        def change():
            ws = self.controller.workspace(self.task_id)
            from harness.effective_records import effective_records

            effective_records(ws).require_current("question", question)
            original = ws.question(question)
            if mode == "defer" and source not in {"decision", "codebase", "not_relevant"}:
                raise ValueError("defer requires a supported skip source")
            if self.controller.get_task(self.task_id).phase is Phase.INTERVIEW:
                engine = TransactionInterview(self.controller, self.task_id)
                if mode == "answer":
                    if any(
                        p.question_id == question and p.status in {"accepted", "modified"}
                        for p in TransactionInterview(
                            self.controller, self.task_id
                        ).effective_proposals()
                    ):
                        raise ValueError(
                            "Revise the decided question separately; recorded decisions remai"
                            "n authoritative"
                        )
                    engine.answer(question, answer)
                elif mode == "unknown":
                    engine.unknown(question)
                else:
                    engine.skip(question, source, ref)
            ident = self.store.contribution(
                "answer", "developer", payload | {"original": asdict(original)}
            )
            self._mark_reconciliation("Answer contributions require reconciliation")
            return {"contribution": ident}

        return self._mutate("answer", expected, request_id, payload, change)

    def _mark_reconciliation(self, obligation):
        previous = self.store.revision(self.store.binding()["working_id"])
        self._append_working(
            previous["body"],
            graph_id=previous["graph_id"],
            obligations=sorted(set(previous["obligations"] + [obligation])),
        )

    def revise(self, *, expected, request_id, target, target_revision, body):
        text(target, "target", 100)
        text(body, "body", 16000)
        payload = {"target": target, "target_revision": target_revision, "body": body}

        def change():
            import json

            if target.startswith("question:"):
                q = self.controller.workspace(self.task_id).question(int(target.split(":")[1]))
                from harness.authoring_context import resolve_target

                resolve_target(self, target, target_revision)
                original = asdict(q)
                current = digest(original)
                heads = self.conn.execute(
                    "SELECT * FROM authoring_contributions WHERE task_id=? AND kind='question"
                    "_revision' ORDER BY rowid DESC",
                    (self.task_id,),
                ).fetchall()
                head = next(
                    (row for row in heads if json.loads(row["payload_json"])["target"] == target),
                    None,
                )
                current = head["id"] if head else current
                supersedes = head["id"] if head else None
                kind = "question_revision"
            else:
                row = self.conn.execute(
                    "SELECT * FROM authoring_contributions WHERE task_id=? AND id=?",
                    (self.task_id, target),
                ).fetchone()
                if row is None:
                    raise LookupError("unknown task contribution")
                if row["actor"] != "developer":
                    raise ValueError("only developer contributions may be revised")
                from harness.authoring_context import projection

                logical = next(
                    (
                        item
                        for item in projection(self)["revision_targets"]
                        if item["kind"] == "contribution" and row["id"] in item["lineage"]
                    ),
                    None,
                )
                if logical and not logical["editable"]:
                    raise AuthoringConflict(
                        "Retired contribution; use the current target",
                        current_target=logical["current_target"],
                    )
                if row["kind"] in {"record_revision", "question_revision"}:
                    raise AuthoringConflict("Use revise-record for typed record corrections")
                while True:
                    child = self.conn.execute(
                        "SELECT * FROM authoring_contributions WHERE task_id=? AND supersedes=?",
                        (self.task_id, row["id"]),
                    ).fetchone()
                    if child is None:
                        break
                    row = child
                if row["kind"] in {"record_revision", "question_revision"}:
                    raise AuthoringConflict("Use revise-record for typed record corrections")
                current = row["id"]
                supersedes = row["id"]
                kind = "revision"
                original = json.loads(row["payload_json"])
            if target_revision != current:
                raise AuthoringConflict("target revision changed")
            ident = self.store.contribution(
                kind, "developer", payload | {"original": original}, supersedes
            )
            self._mark_reconciliation(
                "Revised contributions require explicit reconciliation; prior decisions remai"
                "n recorded"
            )
            return {"contribution": ident}

        return self._mutate("revise", expected, request_id, payload, change)

    def revise_record(self, *, expected, request_id, target, target_revision, value):
        from harness.authoring_context import resolve_target, validate_value

        payload = {"target": target, "target_revision": target_revision, "value": value}

        def change():
            record = resolve_target(self, target, target_revision)
            validate_value(self, record["kind"], value)
            ident = self.store.contribution(
                "record_revision",
                "developer",
                payload
                | {
                    "original": record["original"],
                    "provenance": record["provenance"],
                    "kind": record["kind"],
                },
                record["contribution"],
            )
            self._mark_reconciliation(
                "Proposed record corrections require explicit application and review"
            )
            return {"contribution": ident, "target": target, "status": "pending"}

        return self._mutate("revise-record", expected, request_id, payload, change)

    def decide(self, *, expected, request_id, proposal, status, response):
        text(response, "response", 16000, empty=True)

        def change():
            from harness.effective_records import effective_records

            effective_records(self.controller.workspace(self.task_id)).require_current(
                "proposal", proposal
            )
            original = self.controller.workspace(self.task_id).proposal(proposal)
            if status not in {"accepted", "rejected", "modified"}:
                raise ValueError("invalid decision status")
            if self.controller.get_task(self.task_id).phase is Phase.INTERVIEW:
                from harness.registry import DecisionRegistry

                engine = TransactionInterview(
                    self.controller,
                    self.task_id,
                    DecisionRegistry(self.root / "docs" / "decisions"),
                )
                engine.decide(proposal, status, response)
                if engine.last_record:
                    self._decision_records.append(engine.last_record)
            ident = self.store.contribution(
                "decision",
                "developer",
                {
                    "proposal": proposal,
                    "status": status,
                    "response": response,
                    "original": asdict(original),
                },
            )
            self._mark_reconciliation("Decision contribution requires reconciliation")
            return {"contribution": ident}

        return self._mutate(
            "decide",
            expected,
            request_id,
            {"proposal": proposal, "status": status, "response": response},
            change,
        )

    def followup(self, *, expected, request_id, body):
        text(body, "body")
        return self._mutate(
            "followup",
            expected,
            request_id,
            {"body": body},
            lambda: {
                "contribution": self.store.contribution("followup", "developer", {"body": body})
            },
        )

    def request_regeneration(self, *, expected, request_id, host):
        from harness.authoring_jobs import queue_job

        if host not in {"codex", "claude"}:
            raise ValueError("invalid host")

        def change():
            from harness.planning_session_store import PlanningSessionStore

            owner = PlanningSessionStore(self.conn, self.task_id).owner()
            if owner and owner["mode"] == "attached":
                return {"state": "waiting_for_attached_agent", "job": None, "origin": "attached"}
            from harness.investigation_store import InvestigationStore

            source_store = InvestigationStore(self.controller, self.project, self.task_id)
            if source_store.binding() and not source_store.read_active():
                from harness.authoring_jobs import require_available
                from harness.authoring_policy import availability
                from harness.investigation_sources import start

                require_available(host, availability(host))
                return start(self, host)
            # Queue against the generation that will be committed by _mutate.
            return {"job": queue_job(self, "generate", host, next_generation=True)}

        return self._mutate("regenerate", expected, request_id, {"host": host}, change)

    def reconcile_preview(self, revision):
        selected = self.store.revision(revision)
        if selected is None:
            raise LookupError("select a revision")
        from harness.authoring_context import projection as effective_projection
        from harness.investigation_store import review_basis
        from harness.planning_session_events import native_binding
        from harness.planning_session_material import projection

        preview = {
            "managed_artifacts": __import__(
                "harness.investigation_artifacts", fromlist=["derive"]
            ).derive(self),
            "material": projection(self.conn, self.task_id),
            "owner": native_binding(self.conn, self.task_id),
            "source_review_basis": review_basis(self.controller, self.task_id),
            "decision_lineage": effective_projection(self)["revision_targets"],
            "revision": revision,
            "body_hash": selected["body_hash"],
            "graph": selected["graph_id"],
            "base": self.store.base(),
            "findings": self.findings(selected),
            "source_changed": selected["source_digest"] != self.store.base()["source"],
            "downstream": ["Final design review and explicit approval required"],
        }
        return preview | {"token": digest(preview)}

    def reconcile(
        self,
        *,
        expected,
        request_id,
        revision,
        token,
        confirm_graph,
        acknowledge,
        actions,
        handling=None,
    ):
        payload = {
            "handling": handling or [],
            "revision": revision,
            "token": token,
            "confirm_graph": confirm_graph,
            "acknowledge": acknowledge,
            "actions": actions,
        }
        if (
            type(confirm_graph) is not bool
            or type(acknowledge) is not bool
            or not isinstance(actions, list)
            or len(actions) > 100
        ):
            raise ValueError("invalid reconciliation choices")

        def change():
            preview = self.reconcile_preview(revision)
            if token != preview["token"]:
                raise AuthoringConflict("reconciliation preview changed")
            self._refuse_execution()
            from harness.planning_session_material import validate_manual

            selected = self.store.revision(revision)
            validate_manual(handling or [], preview, selected)
            if not selected["body"].startswith("## "):
                raise ValueError('Specification body must begin with "## "')
            if (selected["obligations"] or preview["source_changed"]) and not acknowledge:
                raise ValueError("explicit acknowledgment of affected material required")
            if selected["graph_id"] and not confirm_graph:
                raise ValueError("explicit graph/spec consistency confirmation required")
            from harness.authoring_actions_contract import validate_planning
            from harness.authoring_context import resolve_target

            self._selected_decisions = set()
            for action in actions:
                validate_planning(action)
                if action["op"] == "revision-apply":
                    selected_target = resolve_target(
                        self, action["target"], action["target_revision"]
                    )
                    if selected_target["kind"] == "recommendation_decision":
                        self._selected_decisions.add(selected_target["original"]["id"])
            try:
                for action in actions:
                    self._planning(action)
            finally:
                self._selected_decisions = set()
            current = self.controller.get_task(self.task_id).phase
            if current is Phase.APPROVAL:
                self.controller._move(
                    self.task_id,
                    current,
                    Phase.FINAL_REVIEW,
                    "Explicit authoring reconciliation requires fresh review",
                )
            from harness.investigation_artifacts import bind

            bind(self)
            adopted = self.store.add_revision(
                selected["body"],
                actor="developer_adopted",
                parent=revision,
                graph_id=selected["graph_id"],
            )
            self.conn.execute(
                "UPDATE authoring_tasks SET adopted_id=?,working_id=? WHERE task_id=?",
                (adopted, adopted, self.task_id),
            )
            self.conn.execute(
                "INSERT INTO authoring_projections VALUES(?,?,'pending') ON CONFLICT(task_id)"
                " DO UPDATE SET revision_id=excluded.revision_id,state='pending'",
                (self.task_id, adopted),
            )
            self.store.contribution("reconciliation", "developer", payload | {"adopted": adopted})
            if handling:
                from harness.planning_session_material import record

                record(
                    self.conn,
                    self.task_id,
                    [entry["input_id"] for entry in handling],
                    "developer_reconciliation",
                    canonical(
                        {
                            "adopted": adopted,
                            "graph": selected["graph_id"],
                            "decision_lineage_digest": digest(preview["decision_lineage"]),
                            "handling_digest": digest(handling),
                        }
                    ),
                    preview["material"],
                )
            return {"adopted": adopted, "projection": "pending"}

        result = self._mutate("reconcile", expected, request_id, payload, change)
        self.project_draft()
        return result

    def project_draft(self):
        from harness.planning_session_events import mutation_scope

        with transaction(self.conn), mutation_scope(self.conn, self.task_id, "system"):
            row = self.conn.execute(
                "SELECT * FROM authoring_projections WHERE task_id=? AND state='pending'",
                (self.task_id,),
            ).fetchone()
            if row and row["revision_id"] == self.store.binding()["adopted_id"]:
                manager = self.manager()
                manager.store.write_draft(manager.preview())
                self.conn.execute(
                    "UPDATE authoring_projections SET state='projected' WHERE task_id=?",
                    (self.task_id,),
                )

    def _refuse_execution(self):
        from harness.execution_store import latest_execution

        execution = latest_execution(self.conn, self.task_id)
        if execution is None:
            return
        if execution.get("status") not in {"Completed", "Failed"}:
            raise AuthoringConflict("active or unsettled execution must finish before adoption")
        from harness.execution_runtime_recovery import _settled
        from harness.execution_settlement import closed_context

        attempts = self.conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=? AND epoch_id=?",
            (execution["id"], execution.get("epoch_id", "legacy-0")),
        ).fetchall()
        if not attempts or any(
            row["status"] not in {"Exited", "Reconciled", "PreSpawnFailed"} for row in attempts
        ):
            raise AuthoringConflict("terminal execution has unverified attempt settlement")
        try:
            _settled(self.controller, execution)
            for attempt in attempts:
                if (
                    attempt["child_pid"]
                    and attempt["mode"] != "reconnect"
                    and not closed_context(execution, attempt)
                ):
                    raise AuthoringConflict("terminal execution teardown remains unverified")
        except (OSError, ValueError, KeyError) as error:
            raise AuthoringConflict("terminal execution teardown remains unverified") from error

    def planning(self, *, expected, request_id, operation):
        def change():
            self._refuse_execution()
            self._planning(operation)
            return {"phase": str(self.controller.get_task(self.task_id).phase)}

        return self._mutate("planning", expected, request_id, operation, change)

    def _planning(self, operation):
        from harness.authoring_actions_contract import validate_planning

        validate_planning(operation)
        op = operation["op"]
        if op == "revision-apply":
            from harness.authoring_application import apply_record

            return apply_record(self, operation["target"], operation["target_revision"])
        args = {k: v for k, v in operation.items() if k != "op"}
        if op in {"advance", "back"}:
            current = self.controller.get_task(self.task_id).phase
            if op == "advance":
                target = next_phase(current)
                if target is None:
                    raise ValueError("authoring cannot start implementation")
                if target is Phase.INTERVIEW:
                    TransactionInterview(self.controller, self.task_id)._approved_decision_ids()
                if target is Phase.APPROVAL:
                    from harness.review import pass_gate

                    pass_gate(self.controller, self.task_id)
            else:
                target = Phase(args["phase"])
                text(args["reason"], "reason")
                if not is_backward(current, target):
                    raise ValueError("back requires an earlier phase")
            self.controller._move(self.task_id, current, target, args.get("reason"))
            if target is Phase.INTERVIEW:
                TransactionInterview(self.controller, self.task_id).next()
            return
        if op.startswith("question-") or op == "interview-propose":
            engine = TransactionInterview(self.controller, self.task_id)
            method = {
                "question-ask": "ask",
                "question-answer": "answer",
                "question-unknown": "unknown",
                "question-defer": "skip",
                "interview-propose": "propose",
            }.get(op)
            if op == "question-answer":
                if any(
                    p.question_id == args["question_id"] and p.status in {"accepted", "modified"}
                    for p in engine.effective_proposals()
                ):
                    raise ValueError(
                        "A recorded decision must be explicitly changed; revising an answer d"
                        "oes not replace it"
                    )
            if op == "question-wording":
                with engine.mutation_in_transaction():
                    engine.effective().require_current("question", args["question_id"])
                    original = engine.ws.question(args["question_id"])
                    engine._require_source_current(original.id)
                    self.store.contribution(
                        "wording_applied",
                        "developer",
                        {"original": asdict(original), "text": args["text"]},
                    )
                    self.conn.execute(
                        "UPDATE questions SET text=? WHERE id=? AND task_id=?",
                        (args["text"], original.id, self.task_id),
                    )
                    engine.ws.record_event(
                        "question.wording_revised",
                        {"id": original.id, "original": original.text, "text": args["text"]},
                    )
            else:
                getattr(engine, method)(**args)
            return
        engine = (
            TransactionDesign(self.controller, self.task_id)
            if op.startswith("design-")
            else TransactionTestPlan(self.controller, self.task_id)
        )
        method = {
            "design-add": "add",
            "design-none": "none",
            "design-decide": "decide",
            "scenario-add": "add",
            "scenario-decide": "decide",
            "test-plan-approve": "approve",
        }[op]
        getattr(engine, method)(**args)

    def cancel_job(self, *, expected, request_id, job):
        from harness.authoring_jobs import cancel_job

        return self._mutate(
            "cancel-job", expected, request_id, {"job": job}, lambda: cancel_job(self, job)
        )

    def retry_job(self, *, expected, request_id, job, acknowledge_unknown):
        from harness.authoring_jobs import retry_job

        return self._mutate(
            "retry-job",
            expected,
            request_id,
            {"job": job, "acknowledge_unknown": acknowledge_unknown},
            lambda: retry_job(self, job, acknowledge_unknown),
        )

    def investigate(self, *, expected, request_id, host, site=None):
        from harness.investigation_sources import start

        return self._mutate(
            "investigate",
            expected,
            request_id,
            {"host": host, "site": site},
            lambda: start(self, host, site),
        )

    def reconnect_jira(self, *, expected, request_id, host, site=None):
        from harness.investigation_sources import start

        return self._mutate(
            "reconnect-jira",
            expected,
            request_id,
            {"host": host, "site": site},
            lambda: start(self, host, site, reconnect=True),
        )

    def investigation_review(self, *, expected, request_id):
        from harness.review_package import package

        return self._mutate(
            "investigation-review",
            expected,
            request_id,
            {},
            lambda: {"review": package(self.controller, self.task_id), "_no_bump": True},
        )

    def accept_investigation(self, *, expected, request_id, approved_sha256, provenance):
        from harness.review_package import accept_investigation

        return self._mutate(
            "accept-investigation",
            expected,
            request_id,
            {"approved_sha256": approved_sha256, "provenance": provenance},
            lambda: accept_investigation(
                self.controller, self.task_id, approved_sha256, source=provenance
            ),
        )

    def jira_handoff(self, *, expected, request_id, job):
        from harness.authoring_contract import request_identity
        from harness.authoring_jobs import job_row
        from harness.investigation_native import HANDOFFS

        request_identity(request_id)
        self.store.check(expected)
        row = job_row(self, job)
        if row["kind"] != "jira_reconnect":
            raise ValueError("explicit reconnect job required")
        # Intentionally outside _mutate/receipt persistence: OAuth URLs are ephemeral only.
        return {"task": self.task_id, "job": job, "handoff": HANDOFFS.get(row)}
