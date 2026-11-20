"""Foreground delivery, acknowledgements and atomic result application."""

import json
import time
import uuid

from harness.db import utcnow
from harness.planning_session_contract import (
    BUSY_LEASE_SECONDS,
    LISTENING_LEASE_SECONDS,
    MAX_EVENT_BATCH,
    MAX_EVENT_BATCH_BYTES,
    MAX_WAIT_SECONDS,
    SessionError,
    identifier,
    validate_publication,
    validate_request,
)


class SessionDelivery:
    def wait(self, request, caller, capability, *, timeout_seconds=25, cancelled=lambda: False):
        validate_request(request)
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 <= timeout_seconds <= MAX_WAIT_SECONDS
        ):
            raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
        scope = self._scope("wait", request["epoch"])
        payload = request | {"timeout_seconds": timeout_seconds}
        token = str(uuid.uuid4())
        deadline = time.monotonic() + timeout_seconds
        with self._transaction():
            self._authorize(request, caller, capability)
            prior = self.store.receipt(scope, request["request_id"], payload)
            if prior:
                return prior
            contact = self.store.contact()
            wall, mono = self.clock()
            if contact.get("wait_id") and contact["wait_deadline_monotonic"] > mono:
                raise SessionError("SESSION_WAIT_ACTIVE")
            self.store.touch(
                wall,
                mono,
                "listening",
                LISTENING_LEASE_SECONDS,
                wait_id=token,
                wait_deadline=mono + timeout_seconds + 1,
            )
        try:
            while not self.store.pending() and time.monotonic() < deadline:
                if cancelled():
                    raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
                owner = self.store.owner()
                if owner["epoch"] != request["epoch"] or owner["mode"] != "attached":
                    raise SessionError("SESSION_OWNER_LOST")
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            with self._transaction():
                self._authorize(request, caller, capability)
                if self.store.contact()["wait_id"] != token:
                    raise SessionError("SESSION_OWNER_LOST")
                events, size = [], 0
                for row in self.store.pending():
                    event = self.store.event_dto(row)
                    length = len(json.dumps(event).encode())
                    if length > MAX_EVENT_BATCH_BYTES:
                        raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
                    if len(events) >= MAX_EVENT_BATCH or size + length > MAX_EVENT_BATCH_BYTES:
                        break
                    events.append(event)
                    size += length
                ident = str(uuid.uuid4())
                ids = [e["id"] for e in events]
                self.conn.execute(
                    "INSERT INTO planning_session_deliveries VALUES(?,?,?,?,?,?,?,?)",
                    (
                        ident,
                        self.task_id,
                        self.project,
                        request["epoch"],
                        request["request_id"],
                        json.dumps(self.base()),
                        json.dumps(ids),
                        utcnow(),
                    ),
                )
                self.conn.executemany(
                    (
                        "INSERT INTO "
                        "planning_session_acknowledgements(delivery_id,event_id) "
                        "VALUES(?,?)"
                    ),
                    [(ident, e) for e in ids],
                )
                self.store.touch(
                    *self.clock(),
                    "busy" if events else "listening",
                    BUSY_LEASE_SECONDS if events else LISTENING_LEASE_SECONDS,
                )
                result = {
                    "task": self.task_id,
                    "delivery_id": ident,
                    "events": events,
                    "base": self.base(),
                    "timed_out": not events,
                    "session": self.status(),
                }
                self.store.record_receipt(scope, request["request_id"], payload, result)
                return result
        finally:
            with self._transaction():
                self.conn.execute(
                    (
                        "UPDATE planning_session_contacts SET "
                        "wait_id=NULL,wait_deadline_monotonic=NULL WHERE task_id=? AND "
                        "epoch=? AND wait_id=?"
                    ),
                    (self.task_id, request["epoch"], token),
                )

    def ack_received(self, request, caller, capability):
        validate_request(request, {"delivery_id", "event_ids"})
        identifier(request["delivery_id"])
        if (
            not isinstance(request["event_ids"], list)
            or len(request["event_ids"]) > MAX_EVENT_BATCH
        ):
            raise SessionError("SESSION_PROTOCOL_UNSUPPORTED")
        for event in request["event_ids"]:
            identifier(event)
        with self._transaction():
            self._authorize(request, caller, capability)
            scope = self._scope("ack")
            prior = self.store.receipt(scope, request["request_id"], request)
            if prior:
                return prior
            delivery = self.conn.execute(
                "SELECT * FROM planning_session_deliveries WHERE id=? AND task_id=? AND epoch=?",
                (request["delivery_id"], self.task_id, request["epoch"]),
            ).fetchone()
            ids = request["event_ids"]
            if (
                not delivery
                or not isinstance(ids, list)
                or len(ids) != len(set(ids))
                or not set(ids) <= set(json.loads(delivery["event_ids_json"]))
            ):
                raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
            for ident in ids:
                self.conn.execute(
                    (
                        "UPDATE planning_session_acknowledgements SET "
                        "received_at=coalesce(received_at,?) WHERE delivery_id=? AND "
                        "event_id=?"
                    ),
                    (utcnow(), delivery["id"], ident),
                )
            self.store.touch(*self.clock(), "busy", BUSY_LEASE_SECONDS)
            result = {"task": self.task_id, "received": ids, "base": self.base()}
            self.store.record_receipt(scope, request["request_id"], request, result)
            return result

    def publish(self, request, caller, capability):
        kind, content = validate_publication(request)
        if kind == "progress" and request["consumes"]:
            raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
        from harness.planning_session_publication import apply, prepare

        with self._transaction():
            self._authorize(request, caller, capability)
            prior = self.store.receipt(self._scope("publish"), request["request_id"], request)
            if prior:
                return prior
            self._check_base(request["expected"])
            from harness.authoring import AuthoringService
            from harness.investigation_eligibility import require_eligible

            require_eligible(AuthoringService(self.controller, self.root, self.task_id))
        prepared = prepare(self, request, kind, content)
        with self._transaction(
            origin="tui_user" if kind == "user_response" else "attached_agent"
        ) as (service, _):
            self._authorize(request, caller, capability)
            scope = self._scope("publish")
            prior = self.store.receipt(scope, request["request_id"], request)
            if prior:
                return prior
            self._check_base(request["expected"])
            from harness.investigation_eligibility import require_eligible

            require_eligible(service)
            sequences = []
            for ident in request["consumes"]:
                sequences.append(
                    self.conn.execute(
                        "SELECT sequence FROM planning_session_events WHERE task_id=? AND id=?",
                        (self.task_id, ident),
                    ).fetchone()
                )
                row = self.conn.execute(
                    (
                        "SELECT 1 FROM planning_session_acknowledgements a JOIN "
                        "planning_session_deliveries d ON d.id=a.delivery_id WHERE "
                        "d.task_id=? AND d.epoch=? AND a.event_id=?"
                    ),
                    (self.task_id, request["epoch"], ident),
                ).fetchone()
                if (
                    not row
                    or self.conn.execute(
                        (
                            "SELECT 1 FROM planning_session_event_results WHERE task_id=? AND "
                            "event_id=?"
                        ),
                        (self.task_id, ident),
                    ).fetchone()
                ):
                    raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
            if any(row is None for row in sequences) or [row[0] for row in sequences] != sorted(
                row[0] for row in sequences
            ):
                raise SessionError("SESSION_OPERATION_INCOMPATIBLE")
            from harness.planning_session_material import projection, record

            material = projection(self.conn, self.task_id, include_inputs=False)
            result = apply(self, service, request, kind, content, prepared)
            event = self.store.append_event(
                "session." + kind,
                result,
                origin="tui_user" if kind == "user_response" else "attached_agent",
                deliverable=kind == "user_response",
                canonical_reference="publication:"
                + request["request_id"]
                + ":"
                + str(request["epoch"]),
            )
            for ident in request["consumes"]:
                self.conn.execute(
                    "INSERT INTO planning_session_event_results VALUES(?,?,?,?,?,?)",
                    (self.task_id, ident, event, kind, request["epoch"], utcnow()),
                )
                self.conn.execute(
                    (
                        "UPDATE planning_session_acknowledgements SET "
                        "applied_result_id=?,applied_at=? WHERE event_id=?"
                    ),
                    (event, utcnow(), ident),
                )
            handled = [
                ident for ident in request["consumes"] if ident in material["pending_material_ids"]
            ]
            record(self.conn, self.task_id, handled, "attached_application", event, material)
            self.store.touch(*self.clock(), "busy", BUSY_LEASE_SECONDS)
            response = {
                "task": self.task_id,
                "result": result,
                "event_id": event,
                "base": self.base(),
                "session": self.status(),
            }
            self.store.record_receipt(scope, request["request_id"], request, response)
            return response
