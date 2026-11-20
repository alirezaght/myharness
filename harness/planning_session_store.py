"""Explicit transaction-owned planning persistence and read-only public projection."""

import json
import sqlite3
import time
import uuid

from harness.authoring_contract import canonical, digest
from harness.db import utcnow
from harness.planning_session_clock import (
    boot_observation,
    public_unavailable,
    require_boot_identity,
)
from harness.planning_session_contract import CLOCK_TOLERANCE_SECONDS, SessionError

TABLES = {
    "planning_session_" + name
    for name in (
        "owners",
        "epochs",
        "contacts",
        "events",
        "deliveries",
        "acknowledgements",
        "event_results",
        "receipts",
        "publications",
        "handling",
    )
}


def register_session_protocol(conn):
    from harness.planning_session_events import write_allowed

    conn.create_function("harness_session_protocol_version", 0, lambda: 1)
    conn.create_function("harness_session_write_allowed", 1, lambda task: write_allowed(conn, task))


def schema_available(conn):
    present = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    } & TABLES
    if present and present != TABLES:
        raise sqlite3.OperationalError("Incomplete planning session schema; upgrade Harness")
    return bool(present)


def clock_state(contact, wall, monotonic, observation=None):
    if not contact:
        return "disconnected"
    observation = observation if observation is not None else boot_observation()
    if observation.identity is None:
        return "clock_unavailable"
    if contact["boot_id"] != observation.identity:
        return "clock_uncertain"
    elapsed_wall = wall - contact["last_wall"]
    elapsed_mono = monotonic - contact["last_monotonic"]
    if (
        elapsed_mono < 0
        or elapsed_wall < -CLOCK_TOLERANCE_SECONDS
        or abs(elapsed_wall - elapsed_mono) > CLOCK_TOLERANCE_SECONDS
    ):
        return "clock_uncertain"
    if wall >= contact["deadline_wall"] or monotonic >= contact["deadline_monotonic"]:
        return "expired"
    return contact["state"]


class PlanningSessionStore:
    def __init__(self, conn, task_id, project=None):
        self.conn, self.task_id, self.project = conn, task_id, project

    def owner(self):
        if not schema_available(self.conn):
            return None
        row = self.conn.execute(
            "SELECT * FROM planning_session_owners WHERE task_id=?", (self.task_id,)
        ).fetchone()
        result = dict(row) if row else None
        if result and self.project is not None and result["project"] != self.project:
            raise SessionError("SESSION_OWNER_LOST")
        return result

    def epoch(self, epoch=None):
        owner = self.owner()
        if not owner:
            return None
        row = self.conn.execute(
            "SELECT * FROM planning_session_epochs WHERE task_id=? AND project=? AND epoch=?",
            (self.task_id, owner["project"], epoch or owner["epoch"]),
        ).fetchone()
        return dict(row) if row else None

    def contact(self):
        owner = self.owner()
        if not owner or owner["mode"] != "attached":
            return None
        row = self.conn.execute(
            "SELECT * FROM planning_session_contacts WHERE task_id=? AND epoch=?",
            (self.task_id, owner["epoch"]),
        ).fetchone()
        return dict(row) if row else None

    def touch(self, wall, monotonic, state, duration, *, wait_id=None, wait_deadline=None):
        owner = self.owner()
        boot = require_boot_identity()
        self.conn.execute(
            (
                "INSERT INTO planning_session_contacts "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id,project,epoch) "
                "DO UPDATE SET "
                "state=excluded.state,last_wall=excluded.last_wall,last_monotonic="
                "excluded.last_monotonic,deadline_wall=excluded.deadline_wall,dead"
                "line_monotonic=excluded.deadline_monotonic,wait_id=excluded.wait_"
                "id,wait_deadline_monotonic=excluded.wait_deadline_monotonic,boot_"
                "id=excluded.boot_id"
            ),
            (
                self.task_id,
                owner["project"],
                owner["epoch"],
                state,
                wall,
                monotonic,
                wall + duration,
                monotonic + duration,
                wait_id,
                wait_deadline,
                boot,
            ),
        )
        self.conn.execute(
            "UPDATE planning_session_owners SET status_revision=status_revision+1 WHERE task_id=?",
            (self.task_id,),
        )

    def receipt(self, scope, request_id, payload):
        row = self.conn.execute(
            "SELECT * FROM planning_session_receipts WHERE scope=? AND request_id=?",
            (scope, request_id),
        ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != digest(payload):
            raise SessionError("SESSION_REQUEST_CONFLICT")
        return json.loads(row["result_json"])

    def record_receipt(self, scope, request_id, payload, result):
        self.conn.execute(
            "INSERT INTO planning_session_receipts VALUES(?,?,?,?,?)",
            (scope, request_id, digest(payload), canonical(result), utcnow()),
        )

    def base(self):
        from harness.authoring_store import AuthoringStore

        owner = self.owner()
        if not self.conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='authoring_tasks'"
        ).fetchone():
            return {"authoring": None, "input_revision": owner["input_revision"] if owner else 0}
        return {
            "authoring": AuthoringStore(
                self._controller(), self.project or (owner or {}).get("project"), self.task_id
            ).base(),
            "input_revision": owner["input_revision"] if owner else 0,
        }

    def _controller(self):
        from harness.controller import HarnessController

        return HarnessController(self.conn)

    def append_event(
        self, kind, payload, *, origin="system", deliverable=True, canonical_reference=None
    ):
        if not self.conn.in_transaction:
            raise RuntimeError("session event requires the domain transaction")
        owner = self.owner()
        if not owner:
            return None
        if canonical_reference:
            row = self.conn.execute(
                "SELECT id FROM planning_session_events WHERE task_id=? AND canonical_reference=?",
                (self.task_id, canonical_reference),
            ).fetchone()
            if row:
                return row[0]
        if len(canonical(payload).encode()) > 60000:
            payload = {
                "operation": kind,
                "canonical_reference": canonical_reference,
                "payload_digest": digest(payload),
                "result": payload.get("result") if isinstance(payload, dict) else None,
            }
        ident = str(uuid.uuid4())
        if deliverable:
            self.conn.execute(
                (
                    "UPDATE planning_session_owners SET "
                    "input_revision=input_revision+1 WHERE task_id=?"
                ),
                (self.task_id,),
            )
        from harness.planning_session_material import is_material

        material = is_material(kind, origin, payload)
        self.conn.execute(
            "INSERT INTO planning_session_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.task_id,
                owner["next_sequence"],
                ident,
                kind,
                origin,
                owner["epoch"],
                canonical(self.base()),
                canonical(payload),
                utcnow(),
                canonical_reference,
                int(deliverable),
                int(material),
            ),
        )
        self.conn.execute(
            (
                "UPDATE planning_session_owners SET "
                "next_sequence=next_sequence+1,status_revision=status_revision+1 "
                "WHERE task_id=?"
            ),
            (self.task_id,),
        )
        return ident

    def pending(self):
        return self.conn.execute(
            (
                "SELECT e.* FROM planning_session_events e LEFT JOIN "
                "planning_session_event_results r ON r.task_id=e.task_id AND "
                "r.event_id=e.id WHERE e.task_id=? AND e.deliverable=1 AND "
                "r.event_id IS NULL ORDER BY e.sequence LIMIT 32"
            ),
            (self.task_id,),
        ).fetchall()

    def event_dto(self, row):
        epoch = self.owner()["epoch"]
        received_row = self.conn.execute(
            (
                "SELECT a.received_at,d.epoch FROM "
                "planning_session_acknowledgements a JOIN "
                "planning_session_deliveries d ON d.id=a.delivery_id WHERE "
                "a.event_id=? AND a.received_at IS NOT NULL ORDER BY d.epoch "
                "DESC,a.received_at DESC LIMIT 1"
            ),
            (row["id"],),
        ).fetchone()
        historical_received = received_row[0] if received_row else None
        received = historical_received if received_row and received_row[1] == epoch else None
        result = self.conn.execute(
            "SELECT * FROM planning_session_event_results WHERE task_id=? AND event_id=?",
            (self.task_id, row["id"]),
        ).fetchone()
        return {
            "material": bool(row["material"]),
            "id": row["id"],
            "sequence": row["sequence"],
            "kind": row["kind"],
            "origin": row["origin"],
            "epoch": row["epoch"],
            "base": json.loads(row["semantic_base_json"]),
            "payload": json.loads(row["payload_json"]),
            "createdAt": row["created_at"],
            "deliveryState": "applied" if result else "received" if received else "saved",
            "receivedAt": received,
            "receivedEpoch": epoch if received else None,
            "historicalReceivedAt": historical_received,
            "historicalReceivedEpoch": received_row[1] if received_row else None,
            "appliedEpoch": result["epoch"] if result else None,
            "appliedAt": result["created_at"] if result else None,
            "resultId": result["result_id"] if result else None,
            "requiresResponse": bool(row["deliverable"]),
        }

    def snapshot(self, *, clock=None, before=None, limit=50, repository=None):
        wall, monotonic = clock() if clock else (time.time(), time.monotonic())
        owner = self.owner()
        mode = owner["mode"] if owner else "manual"
        epoch = self.epoch() if owner and mode == "attached" else None
        contact = self.contact()
        rows = []
        if owner:
            rows = self.conn.execute(
                (
                    "SELECT * FROM planning_session_events WHERE task_id=? AND "
                    "sequence<? ORDER BY sequence DESC LIMIT ?"
                ),
                (self.task_id, before or 9223372036854775807, min(max(limit, 1), 50)),
            ).fetchall()
        events, size = [], 0
        for row in rows:
            event = self.event_dto(row)
            length = len(canonical(event).encode())
            if size + length > 131072:
                break
            events.append(event)
            size += length
        events.reverse()
        pending = (
            self.conn.execute(
                (
                    "SELECT count(*) FROM planning_session_events e LEFT JOIN "
                    "planning_session_event_results r ON r.task_id=e.task_id AND "
                    "r.event_id=e.id WHERE e.task_id=? AND e.deliverable=1 AND "
                    "r.event_id IS NULL"
                ),
                (self.task_id,),
            ).fetchone()[0]
            if owner
            else 0
        )
        applied = (
            self.conn.execute(
                "SELECT count(*) FROM planning_session_event_results WHERE task_id=?",
                (self.task_id,),
            ).fetchone()[0]
            if owner
            else 0
        )
        received = (
            self.conn.execute(
                (
                    "SELECT count(DISTINCT a.event_id) FROM "
                    "planning_session_acknowledgements a JOIN planning_session_events "
                    "e ON e.id=a.event_id JOIN planning_session_deliveries d ON "
                    "d.id=a.delivery_id WHERE e.task_id=? AND d.epoch=? AND "
                    "a.received_at IS NOT NULL"
                ),
                (self.task_id, owner["epoch"]),
            ).fetchone()[0]
            if owner
            else 0
        )
        latest_received = (
            self.conn.execute(
                (
                    "SELECT e.id FROM planning_session_events e WHERE e.task_id=? AND "
                    "EXISTS(SELECT 1 FROM planning_session_acknowledgements a JOIN "
                    "planning_session_deliveries d ON d.id=a.delivery_id WHERE "
                    "a.event_id=e.id AND d.epoch=? AND a.received_at IS NOT NULL) "
                    "ORDER BY e.sequence DESC LIMIT 1"
                ),
                (self.task_id, owner["epoch"]),
            ).fetchone()
            if owner
            else None
        )
        latest_applied = (
            self.conn.execute(
                (
                    "SELECT e.id FROM planning_session_events e JOIN "
                    "planning_session_event_results r ON r.event_id=e.id WHERE "
                    "e.task_id=? ORDER BY e.sequence DESC LIMIT 1"
                ),
                (self.task_id,),
            ).fetchone()
            if owner
            else None
        )
        recovery = None
        if epoch:
            command = [
                "harness",
                "--db",
                self.conn.execute("PRAGMA database_list").fetchone()[2],
                "session",
                "attach",
                "--provider",
                epoch["provider"],
                "--file",
                "<request.json>",
            ]
            if repository:
                command += ["--repo", repository]
            recovery = {
                "command": command,
                "request_template": {
                    "schema_version": 1,
                    "project": owner["project"],
                    "task": self.task_id,
                    "request_id": "<new-request-id>",
                    "expected": self.base(),
                    "previous_epoch": owner["epoch"],
                },
                "requiresOriginalContext": True,
                "takeoverFlag": "--takeover",
                "instruction": (
                    "Save a request file with a new request ID and run in the "
                    "original invoking provider context. A healthy owner can only be "
                    "replaced with explicit --takeover. This creates a new owner-only "
                    "capability artifact."
                ),
            }
        from harness.planning_session_material import projection

        observation = boot_observation() if mode == "attached" else None

        return {
            "material": projection(self.conn, self.task_id),
            "protocolVersion": 1,
            "mode": mode,
            "providerLabel": epoch["provider"] if epoch else None,
            "attachmentId": owner["attachment_id"] if owner else None,
            "epoch": owner["epoch"] if owner else 0,
            "state": clock_state(contact, wall, monotonic, observation)
            if mode == "attached"
            else mode,
            "clockUnavailable": public_unavailable(observation)
            if observation and observation.identity is None
            else None,
            "lastContact": contact["last_wall"] if contact else None,
            "statusRevision": owner["status_revision"] if owner else 0,
            "inputRevision": owner["input_revision"] if owner else 0,
            "pendingCount": pending,
            "receivedCount": received,
            "appliedCount": applied,
            "latestReceivedEvent": latest_received[0] if latest_received else None,
            "latestAppliedEvent": latest_applied[0] if latest_applied else None,
            "events": events,
            "nextBefore": events[0]["sequence"]
            if events and (len(events) < len(rows) or len(rows) == min(max(limit, 1), 50))
            else None,
            "actions": {
                "canPause": mode == "attached",
                "canRequestReattach": mode == "attached",
                "canTakeover": mode == "attached",
                "canChooseStandalone": mode == "attached",
                "canRegenerate": mode == "attached",
            },
            "recovery": recovery,
            "contextCommand": [
                "harness",
                "--db",
                self.conn.execute("PRAGMA database_list").fetchone()[2],
                "session",
                "status",
                "--task",
                self.task_id,
                "--context",
            ]
            + (["--repo", repository] if repository else []),
        }
