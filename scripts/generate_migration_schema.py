"""Generate the checked schema identities from canonical SQL, using disposable memory only.

Run from the checkout with its qualified Python, redirecting stdout to the manifest.
This authoring tool is never imported by runtime migration or connection paths.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.migration_compatibility import execute_migration  # noqa: E402
from harness.migration_schema import identities  # noqa: E402
from harness.planning_session_store import register_session_protocol  # noqa: E402


def generate():
    scripts = {
        int(p.name[:4]): (p.name, p.read_bytes())
        for p in sorted((ROOT / "harness/migrations").glob("[0-9]*.sql"))
    }

    def connection():
        conn = sqlite3.connect(":memory:", autocommit=True)
        register_session_protocol(conn)
        return conn

    histories, managed = {}, set()
    conn = connection()
    try:
        histories["c0"] = identities(conn)
        for version in range(1, 19):
            execute_migration(conn, scripts[version][1].decode())
            if version < 18:
                histories[f"c{version}"] = identities(conn)
    finally:
        conn.close()
    for main in range(3):
        for reliability in range(6):
            conn = connection()
            try:
                versions = [*range(1, 19), *range(19, 19 + main), *range(21, 21 + reliability)]
                for version in versions:
                    execute_migration(conn, scripts[version][1].decode())
                value = identities(conn)
                histories[f"m{main}r{reliability}"] = value
                managed.update(value)
            finally:
                conn.close()
    return {
        "schema_version": 1,
        "managed_objects": sorted(managed),
        "managed_prefixes": [
            "knowledge_",
            "policy_",
            "reliability_",
            "incident_",
            "execution_",
            "planning_session_",
            "session_",
            "attached_publication_",
        ],
        "histories": histories,
        "migration_hashes": {
            name: hashlib.sha256(raw).hexdigest() for name, raw in scripts.values()
        },
    }


if __name__ == "__main__":
    print(json.dumps(generate(), indent=2))
