"""Content-free query and explanation observations in the existing knowledge store."""

import secrets
from time import monotonic

from harness.db import utcnow
from harness.knowledge.models import canonical


def record(conn, scope, payload, bundle_hash, reads, started):
    conn.execute(
        "INSERT INTO knowledge_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            secrets.token_hex(16),
            scope.project_id,
            bundle_hash,
            payload["snapshot_id"],
            payload["policy_digest"],
            scope.purpose,
            canonical(sorted({c["version_id"] for c in payload["citations"]})),
            canonical(payload["budget"]),
            reads["count"],
            (monotonic() - started) * 1000,
            canonical(
                {
                    "staleness": len(payload["staleness"]),
                    "conflicts": len(payload["conflicts"]),
                    "ambiguities": len(payload["ambiguities"]),
                    "source_bytes": reads["bytes"],
                }
            ),
            utcnow(),
        ),
    )
