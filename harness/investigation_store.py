"""Task-owned immutable evidence and transaction-owned activation; no external reads."""

import json
import sqlite3

from harness.authoring_contract import AuthoringConflict, canonical, digest
from harness.authoring_store import identity
from harness.db import utcnow
from harness.interview_coverage import validate_evidence, validate_sources

TABLES = {
    "investigation_bindings",
    "investigation_bundles",
    "investigation_sources",
    "investigation_acceptances",
    "investigation_decisions",
    "investigation_progress",
}


def schema_available(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    present = TABLES & tables
    if present and present != TABLES:
        raise sqlite3.OperationalError("Partial investigation schema")
    return present == TABLES


def review_basis(controller, task_id):
    from harness.authoring_store import AuthoringStore, adopted_revision

    row = controller.conn.execute(
        "SELECT * FROM authoring_tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    revision = adopted_revision(controller, task_id)
    if not revision and row:
        revision = AuthoringStore(controller, row["project"], task_id).revision(row["working_id"])
    from harness.authoring_store import source_state
    from harness.planning_session_material import basis

    return (
        {
            "session_material": basis(controller.conn, task_id),
            "semantic": digest(source_state(controller, task_id)),
            "body": digest(revision["body"]),
            "graph": revision["graph"]["digest"] if revision["graph"] else None,
            "revision": revision["id"],
        }
        if revision
        else None
    )


class InvestigationStore:
    def __init__(self, controller, project, task_id):
        self.controller, self.conn, self.project, self.task_id = (
            controller,
            controller.conn,
            project,
            task_id,
        )

    def writing(self):
        if not self.conn.in_transaction or not schema_available(self.conn):
            raise RuntimeError("migrated caller transaction required")

    def binding(self):
        if not schema_available(self.conn):
            return None
        row = self.conn.execute(
            "SELECT * FROM investigation_bindings WHERE task_id=?", (self.task_id,)
        ).fetchone()
        if not row:
            return None
        if self.project and row["project"] != self.project:
            raise LookupError("foreign investigation project")
        return {
            "task": row["task_id"],
            "project": row["project"],
            "selection": json.loads(row["selection_json"]),
            "active_bundle": row["active_bundle"],
            "validation_generation": row["validation_generation"],
            "recovery": json.loads(row["recovery_json"]) if row["recovery_json"] else None,
        }

    def semantic(self):
        binding = self.binding()
        return (
            {k: binding[k] for k in ("selection", "active_bundle", "validation_generation")}
            if binding
            else None
        )

    def read_bundle(self, bundle_id):
        row = self.conn.execute(
            "SELECT * FROM investigation_bundles WHERE task_id=? AND id=?",
            (self.task_id, bundle_id),
        ).fetchone()
        if not row or self.project and row["project"] != self.project:
            raise LookupError("unknown task source bundle")
        return dict(row) | {"content": json.loads(row["content_json"])}

    def read_active(self):
        binding = self.binding()
        return (
            self.read_bundle(binding["active_bundle"])
            if binding and binding["active_bundle"]
            else None
        )

    def acceptance(self):
        if not self.binding():
            return None
        row = self.conn.execute(
            "SELECT * FROM investigation_acceptances WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
            (self.task_id,),
        ).fetchone()
        return dict(row) | {"basis": json.loads(row["basis_json"])} if row else None

    def decisions(self, effective=None):
        if not self.binding():
            return []
        rows = self.conn.execute(
            "SELECT * FROM investigation_decisions WHERE task_id=? ORDER BY revision",
            (self.task_id,),
        ).fetchall()
        from harness.investigation_lineage import source_lineage

        return source_lineage(self.controller, self.task_id, effective, rows=rows)["decisions"]

    def read_attempt(self, job_id, attempt):
        row = self.conn.execute(
            (
                "SELECT p.* FROM investigation_progress p JOIN authoring_jobs"
                " j ON j.id=p.job_id WHERE j.task_id=? AND j.project=? AND "
                "p.job_id=? AND p.attempt=?"
            ),
            (self.task_id, self.project, job_id, attempt),
        ).fetchone()
        if not row:
            return None
        return {
            "stage": row["stage"],
            "completed": json.loads(row["completed_json"]),
            "diagnostic": json.loads(row["diagnostic_json"]) if row["diagnostic_json"] else None,
            "observations": json.loads(row["observations_json"])
            if row["observations_json"]
            else None,
        }

    def progress(self, claim, stage, *, diagnostic=None, observations=None):
        self.writing()
        from harness.authoring_jobs import job_row, matches

        row = job_row(
            type(
                "Task", (), {"conn": self.conn, "task_id": self.task_id, "project": self.project}
            )(),
            claim["id"],
        )
        if not matches(row, claim):
            return False
        prior = self.read_attempt(claim["id"], claim["attempt"])
        completed = list(prior["completed"]) if prior else []
        if prior and prior["stage"] not in completed and prior["stage"] != stage:
            completed.append(prior["stage"])
        self.conn.execute(
            (
                "INSERT INTO investigation_progress VALUES(?,?,?,?,?,?) ON "
                "CONFLICT(job_id,attempt) DO UPDATE SET "
                + (
                    "stage=excluded.stage,completed_json=excluded.completed_json,diagn"
                    "ostic_json=excluded.diagnostic_json,observations_json=COALESCE(ex"
                    "cluded.observations_json,investigation_progress.observations_json"
                    ")"
                )
            ),
            (
                claim["id"],
                claim["attempt"],
                stage,
                canonical(completed),
                canonical(diagnostic) if diagnostic else None,
                canonical(observations) if observations else None,
            ),
        )
        return True

    def invalidate(self, expected_bundle, reason):
        self.writing()
        binding = self.binding()
        if not binding or binding["active_bundle"] != expected_bundle:
            raise AuthoringConflict("source bundle changed")
        self.conn.execute(
            (
                "UPDATE investigation_bindings SET "
                "validation_generation=validation_generation+1,recovery_json=?"
                " WHERE task_id=?"
            ),
            (canonical(reason), self.task_id),
        )

    def publish(self, service, claim, prepared, expected):
        self.writing()
        from harness.authoring_jobs import job_row, matches, settle
        from harness.investigation_eligibility import require_eligible

        binding = self.binding()
        row = job_row(service, claim["id"])
        if row["kind"] == "attached_publication":
            from harness.planning_session_contract import SessionError

            raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
        if row["applied_id"]:
            return row["applied_id"]
        if not matches(row, claim) or service.store.base() != expected:
            if matches(row, claim):
                settle(service, claim, "stale", "Investigation input changed")
            return None
        inputs = json.loads(claim["input_json"])
        request = inputs["investigation"]["request"]
        if (
            not binding
            or binding["validation_generation"] != inputs["investigation"]["validation_generation"]
            or binding["selection"] != request
            or prepared["request"] != request
            or prepared["parent"] != binding["active_bundle"]
            or prepared["settlement"] != "settled"
        ):
            raise AuthoringConflict("investigation authority changed")
        from harness.planning_session_events import native_binding

        inputs = json.loads(claim["input_json"])
        if inputs["investigation"].get("parent_hash"):
            parent = self.read_active()
            if not parent or parent["content_hash"] != inputs["investigation"]["parent_hash"]:
                raise AuthoringConflict("investigation parent content changed")
        current_owner = native_binding(service.conn, service.task_id)
        if current_owner != inputs.get("planning_owner") or (
            current_owner is not None and current_owner["mode"] != "standalone"
        ):
            raise AuthoringConflict("investigation owner changed")
        require_eligible(service)
        from harness.investigation_artifacts import require_policy

        require_policy(service, inputs["managed_artifacts"])
        if prepared["repository"].get("managed_policy") != inputs["managed_artifacts"]:
            raise AuthoringConflict("managed artifact policy differs from captured acquisition")
        from harness.investigation_repository import recheck_observation

        recheck_observation(service.root, prepared["repository"])
        from harness.planning_session_material import record
        from harness.planning_session_material import validate_output as validate_handling

        entries = prepared.get("handling", [])
        validate_handling(
            entries, inputs, {"questions": [], "body": prepared["body"], "graph": prepared["graph"]}
        )
        contribution = self.insert_bundle(service, row, prepared, expected)
        if entries:
            record(
                self.conn,
                self.task_id,
                [entry["input_id"] for entry in entries],
                "standalone_result",
                canonical(
                    {
                        "job": row["id"],
                        "attempt": row["attempt"],
                        "contribution": contribution,
                        "handling_digest": digest(entries),
                    }
                ),
                inputs["session_material"],
            )
        self.progress(claim, "ready")
        settle(service, claim, "succeeded")
        service.store.bump()
        return contribution

    def insert_bundle(self, service, row, prepared, expected):
        from harness.logical_diagram import validate_graph

        self.writing()
        if row["task_id"] != self.task_id or row["project"] != self.project:
            raise AuthoringConflict("source publication task mismatch")
        request = prepared["request"]
        sources = validate_sources(prepared["sources"])
        evidence = validate_evidence(prepared["evidence"], prepared["sources"])
        if evidence["reads"]:
            raise ValueError("intermediate reads cannot publish a complete investigation")
        refs = {f"investigation:{self.task_id}:{s}" for s in sources} | set(prepared["valid_refs"])
        validate_graph(prepared["graph"], refs)
        if not prepared["body"].startswith("## "):
            raise ValueError("complete proposed specification required")
        content = {
            k: prepared[k]
            for k in ("request", "sources", "evidence", "repository", "complete", "limitations")
        }
        if "acquisition" in prepared:
            content["acquisition"] = prepared["acquisition"]
        if prepared.get("origin") == "attached":
            content["origin"] = "attached"
            content["provenance"] = prepared["provenance"]
            content["observations"] = prepared["observations"]
        ident = identity()
        self.conn.execute(
            "INSERT INTO investigation_bundles VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                ident,
                self.task_id,
                self.project,
                prepared["parent"],
                row["id"],
                row["attempt"],
                request["policy"],
                canonical(content),
                digest(content),
                utcnow(),
            ),
        )
        for source in prepared["sources"]:
            self.conn.execute(
                "INSERT INTO investigation_sources VALUES(?,?,?,?)",
                (self.task_id, ident, source["id"], canonical(source)),
            )
        self._decisions(service, evidence, sources)
        self.conn.execute(
            "UPDATE investigation_bindings SET active_bundle=?,recovery_json=NULL WHERE task_id=?",
            (ident, self.task_id),
        )
        ws = self.controller.workspace(self.task_id)
        if not ws.latest_checkpoint("investigation"):
            ws.checkpoint(
                "investigation", {"repo_root": str(service.root), "decisions": [], "bundle": ident}
            )
        if self.controller.get_task(self.task_id).phase == "investigation":
            from harness.phases import Phase

            self.controller._move(
                self.task_id, Phase.INVESTIGATION, Phase.INTERVIEW, "Source investigation published"
            )
        contribution = service.store.contribution(
            "assistant",
            "attached_agent" if prepared.get("origin") == "attached" else "model",
            {
                "job": row["id"],
                "text": prepared["assistant"],
                "bundle": ident,
                "content_complete": True,
            },
        )
        candidate = service.store.add_revision(
            prepared["body"],
            actor="model_proposal",
            parent=expected["working"],
            graph=prepared["graph"],
            obligations=["Review exact source-bound specification and graph"],
        )
        result = {
            "revision": candidate,
            "bundle": ident,
            "output": {"graph": {"concrete": prepared["graph"]}, "body": prepared["body"]},
        }
        self.conn.execute(
            "UPDATE authoring_jobs SET applied_id=?,result_json=? WHERE id=?",
            (contribution, canonical(result), row["id"]),
        )
        self.conn.execute(
            "UPDATE authoring_attempts SET result_json=? WHERE job_id=? AND attempt=?",
            (canonical(result), row["id"], row["attempt"]),
        )
        return contribution

    def _decisions(self, service, evidence, sources):
        from harness.effective_records import effective_records
        from harness.investigation_concerns import definitions

        catalogue = definitions()
        prior = {
            r["concern"]: r
            for r in self.decisions(effective_records(service.controller.workspace(self.task_id)))
        }
        ws = self.controller.workspace(self.task_id)
        for row in evidence["decisions"]:
            premise = [
                {"locator": sources[c["source"]]["locator"], "quote": c["quote"]}
                for c in row["citations"]
            ]
            old = prior.get(row["concern"])
            if old and old["premise"] == premise:
                if row["supersedes"] is not None:
                    raise ValueError("unchanged premise cannot reopen a decision")
                continue
            if old and row["supersedes"] != old["question_id"]:
                raise ValueError("changed decision premise needs explicit prior question identity")
            if not old and row["supersedes"] is not None:
                raise ValueError("unknown decision predecessor")
            question = ws.ask(catalogue[row["concern"]].topic, row["text"], kind="decision")
            self.conn.execute(
                "INSERT INTO investigation_decisions VALUES(?,?,?,?,?,?)",
                (
                    self.task_id,
                    row["concern"],
                    old["revision"] + 1 if old else 1,
                    question.id,
                    canonical(premise),
                    old["question_id"] if old else None,
                ),
            )
