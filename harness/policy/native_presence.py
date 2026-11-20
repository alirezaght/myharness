"""Reciprocal native launch permission and exact fenced no-start disposition."""

import json
import os
import secrets
import threading

from harness.execution_contract import canonical
from harness.policy.contracts import require

_ISSUED = {}
_GUARD = threading.Lock()


class LaunchPermit:
    """A DB nonce is not proof that the original worker has canceled its future Popen."""

    def __init__(self, execution, token, manifest):
        self.id, self.token = secrets.token_hex(24), token
        self.binding = (
            execution["id"],
            execution["epoch_id"],
            execution["authority_generation"],
            manifest,
        )
        self.owner = (os.getpid(), threading.get_ident())
        self.state = "issued"

    def _owned(self):
        require(
            _ISSUED.get(self.id) is self and self.owner == (os.getpid(), threading.get_ident()),
            "ATTEMPT_NOT_EXECUTABLE",
        )

    def begin_spawn(self):
        with _GUARD:
            self._owned()
            require(self.state == "issued", "ATTEMPT_NOT_EXECUTABLE")
            self.state = "spawning"

    def spawn_failed(self):
        with _GUARD:
            self._owned()
            require(self.state == "spawning", "ATTEMPT_NOT_EXECUTABLE")
            self.state = "failed"

    def spawned(self):
        with _GUARD:
            self._owned()
            require(self.state == "spawning", "ATTEMPT_NOT_EXECUTABLE")
            self.state = "running"

    def fence_no_start(self):
        with _GUARD:
            self._owned()
            require(self.state in {"issued", "failed", "canceled"}, "ATTEMPT_NOT_EXECUTABLE")
            self.state = "canceled"
            return self.id


def require_no_strict_consumers(conn):
    from harness.policy.claims import open_claims

    for row in open_claims(conn):
        require(
            json.loads(row["claim"]).get("purpose") != "mutable_dependency",
            "ATTEMPT_NOT_EXECUTABLE",
        )


def permit(conn, execution, token, manifest):
    from harness.execution_epochs import require_active_epoch

    require(conn.in_transaction, "ATTEMPT_NOT_EXECUTABLE")
    current = require_active_epoch(
        conn,
        execution["id"],
        expected_epoch_id=execution["epoch_id"],
        expected_authority_generation=execution["authority_generation"],
        mutation=True,
    )
    require(current["worker_token"] == token, "AUTHORITY_STALE")
    require_no_strict_consumers(conn)
    require(isinstance(manifest, str) and manifest.startswith("sha256:") and len(manifest) == 71)
    grant = LaunchPermit(execution, token, manifest)
    permit_id = grant.id
    require(
        conn.execute(
            "UPDATE execution_attempts SET dependency_launch_state='permitted',"
            "dependency_launch_receipt=?,dependency_launch_manifest=? "
            "WHERE execution_id=? AND epoch_id=? AND token=? "
            "AND status='Reserved' AND child_pid IS NULL AND dependency_launch_state='unknown'",
            (
                canonical({"permit": permit_id}),
                manifest,
                execution["id"],
                execution["epoch_id"],
                token,
            ),
        ).rowcount
        == 1,
        "ATTEMPT_NOT_EXECUTABLE",
    )

    with _GUARD:
        _ISSUED[grant.id] = grant
    return grant


def not_started(conn, execution, token, receipt, *, launch_permit=None):
    from harness.db import transaction
    from harness.execution_epoch_lock import epoch_action_lock
    from harness.execution_epochs import require_active_epoch

    require(not conn.in_transaction, "ATTEMPT_NOT_EXECUTABLE")
    require(
        receipt
        == {
            "kind": "native-not-started-v1",
            "execution_id": execution["id"],
            "epoch_id": execution["epoch_id"],
            "authority_generation": execution["authority_generation"],
            "token": token,
            "stage": receipt.get("stage"),
            "native_started": False,
        },
        "AUTHORITY_STALE",
    )
    require(receipt["stage"] in {"worker-spawn", "native-setup", "native-spawn", "reconnect"})
    with epoch_action_lock(execution), transaction(conn):
        current = require_active_epoch(
            conn,
            execution["id"],
            expected_epoch_id=execution["epoch_id"],
            expected_authority_generation=execution["authority_generation"],
            mutation=True,
        )
        require(current["worker_token"] == token, "AUTHORITY_STALE")
        row = conn.execute(
            "SELECT * FROM execution_attempts WHERE execution_id=? AND epoch_id=? AND token=?",
            (execution["id"], execution["epoch_id"], token),
        ).fetchone()
        require(row is not None and row["child_pid"] is None, "ATTEMPT_NOT_EXECUTABLE")
        prior = row["dependency_launch_state"]
        same = prior == "not_started" and row["dependency_launch_receipt"] == canonical(receipt)
        unissued = prior == "unknown" and row["status"] == "Reserved" and launch_permit is None
        owned = False
        if prior == "permitted" and isinstance(launch_permit, LaunchPermit):
            require(
                launch_permit.token == token
                and launch_permit.binding[:3]
                == (execution["id"], execution["epoch_id"], execution["authority_generation"]),
                "AUTHORITY_STALE",
            )
            permit_id = launch_permit.fence_no_start()
            owned = (
                row["dependency_launch_receipt"] == canonical({"permit": permit_id})
                and row["status"] == "Launching"
            )
        require(same or unissued or owned, "ATTEMPT_NOT_EXECUTABLE")
        require(
            (receipt["stage"] == "reconnect") == (row["mode"] == "reconnect"), "AUTHORITY_STALE"
        )
        conn.execute(
            "UPDATE execution_attempts SET dependency_launch_state='not_started',"
            "dependency_launch_receipt=? WHERE id=?",
            (canonical(receipt), row["id"]),
        )
