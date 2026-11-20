"""Prepare exact accepted immutable buffers before the existing event publication transaction."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from harness.execution_contract import canonical, loads
from harness.execution_handoff import read_handoff, same_path
from harness.execution_hosts import al_source
from harness.knowledge.sources import read_artifact
from harness.reliability.catalog import IncidentConflict, _insert, atomic_source

ERROR = "accepted incident source unavailable or binding differs"


@dataclass(frozen=True)
class PreparedSource:
    event: str
    source_kind: str
    items: tuple[str, ...]
    reference: str


def _read_state(path):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError(ERROR)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            maximum = 32 * 1024 * 1024
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
                raise ValueError(ERROR)
            chunks, size = [], 0
            while size <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            after = os.fstat(fd)
            if size > maximum or any(
                getattr(before, k) != getattr(after, k)
                for k in ("st_size", "st_mtime_ns", "st_ctime_ns")
            ):
                raise ValueError(ERROR)
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def prepare(execution, event):
    kind, payload = event["kind"], event["payload"]
    if kind != "review.accepted" and not (kind == "test.result" and "source_receipt" in payload):
        return None
    try:
        envelope = read_handoff(execution["handoff_path"], execution["handoff_hash"])
        source = Path(al_source(execution, envelope))
        state = loads(_read_state(source), max_bytes=32 * 1024 * 1024)
        integration = state["integration"]
        if (
            not same_path(integration["handoff"], execution["handoff_path"])
            or not same_path(state["identity"]["root"], envelope["repository"])
            or integration["binding"] != event["binding"]
            or state["session_id"] != execution["al_session_id"]
            or state["run_id"] != event["binding"]["al_run_id"]
            or event["sequence"] > len(integration["outbox"])
            or canonical(integration["outbox"][event["sequence"] - 1]) != canonical(event)
        ):
            raise ValueError(ERROR)
        reference = payload["reference"] if kind == "review.accepted" else payload["source_receipt"]
        actual = (
            integration.get("incident_reviews", {}).get(reference["sha256"])
            if kind == "review.accepted"
            else integration.get("incident_results", {}).get(payload["id"])
        )
        if actual is None or {k: actual[k] for k in ("kind", "id", "sha256")} != reference:
            raise ValueError(ERROR)
        directory = source.parent / execution["al_session_id"] / "artifacts"
        # Derive from this exact buffer; no reopened report or caller-selected root.
        value = loads(
            read_artifact(directory, actual, maximum=4 * 1024 * 1024), max_bytes=4 * 1024 * 1024
        )
        if kind == "test.result":
            if (
                set(value)
                != {
                    "schema_version",
                    "source_kind",
                    "binding",
                    "result_id",
                    "status",
                    "counts",
                    "purpose",
                    "validity_at_capture",
                }
                or type(value["schema_version"]) is not int
                or value["schema_version"] != 1
                or value["source_kind"] != "al_test_result"
                or value["binding"] != event["binding"]
                or value["result_id"] != payload["id"]
                or value["status"] not in {"PASS", "FAIL", "EMPTY", "INFRA", "TORN"}
                or value["purpose"]
                not in {
                    "verification",
                    "unit",
                    "integration",
                    "security",
                    "ci",
                    "staging",
                    "build",
                    "lint",
                }
            ):
                raise ValueError(ERROR)
            if value["purpose"] != payload.get("purpose", "verification"):
                raise ValueError(ERROR)
            items = (payload["id"],) if value["status"] == "FAIL" else ()
            family = "al_test_result"
        else:
            if (
                value.get("review_id") != payload["review_id"]
                or value.get("stage") != payload["stage"]
            ):
                raise ValueError(ERROR)
            findings = value.get("findings")
            if not isinstance(findings, list) or len(findings) > 1000:
                raise ValueError(ERROR)
            from harness.execution_contract import identity

            ids = [identity(f["id"]) for f in findings if f["severity"] == "BLOCKING"]
            if len(ids) != len(set(ids)):
                raise ValueError(ERROR)
            items = tuple(reference["sha256"] + ":" + fid for fid in ids)
            family = "al_review_finding"
        return PreparedSource(canonical(event), family, tuple(items), canonical(reference))
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        raise ValueError(ERROR) from None


def publish(conn, execution, event, prepared):
    if prepared is None:
        return
    if prepared.event != canonical(event):
        raise ValueError(ERROR)
    from datetime import UTC, datetime

    try:
        created = datetime.fromtimestamp(event["timestamp"], UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        raise ValueError(ERROR) from None
    for item in prepared.items:
        with atomic_source(conn):
            prior = conn.execute(
                "SELECT s.incident_id,e.record FROM incident_event_sources s JOIN "
                "execution_events e "
                "ON e.execution_id=s.execution_id AND e.epoch_id=s.epoch_id AND "
                "e.event_id=s.event_id "
                "WHERE s.source_kind=? AND s.execution_id=? AND s.epoch_id=? AND s.item_id=? "
                "AND s.source_revision=1",
                (prepared.source_kind, execution["id"], execution["epoch_id"], item),
            ).fetchone()
            if prior:
                old_payload = loads(prior["record"].encode())["payload"]
                old_ref = old_payload.get("source_receipt", old_payload.get("reference"))
                if canonical(old_ref) != prepared.reference:
                    raise IncidentConflict("accepted incident source identity changed")
                continue
            test = prepared.source_kind == "al_test_result"
            incident = _insert(
                conn,
                prepared.source_kind,
                execution["id"],
                execution["epoch_id"],
                "verification_failed" if test else "review_finding",
                "VERIFICATION_FAILED" if test else "REVIEW_FINDING",
                "validated_ledger_result" if test else "validated_review_finding",
                created,
            )
            conn.execute(
                "INSERT INTO incident_event_sources VALUES (?,?,?,?,?,?,1)",
                (
                    incident,
                    prepared.source_kind,
                    execution["id"],
                    execution["epoch_id"],
                    event["event_id"],
                    item,
                ),
            )
