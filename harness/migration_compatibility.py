"""Recognize published main/checkpoint schemas without inventing migration history."""

import json
import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files

from harness.migration_schema import identities

OLD_NAMES = (
    "0019_reliability_observations.sql", "0020_reliability_timeline.sql",
    "0021_runtime_policy.sql", "0022_dependency_launch_fencing.sql", "0023_incident_catalog.sql",
)
CANONICAL_NAMES = tuple(f"{i+21:04d}" + name[4:] for i, name in enumerate(OLD_NAMES))
MAIN_NAMES = ("0019_investigation_sources.sql", "0020_planning_sessions.sql")


@dataclass(frozen=True)
class History:
    common: int
    main: int
    reliability: int
    checkpoint: bool

    @property
    def key(self):
        return f"c{self.common}" if self.common < 18 else f"m{self.main}r{self.reliability}"


def _refuse(reason):
    raise RuntimeError("unrecognized migration history: " + reason)


def validate_history(conn, migrations):
    expected = {v: name for v, name, _ in migrations}
    exists = conn.execute("SELECT 1 FROM sqlite_schema WHERE name='schema_migrations'").fetchone()
    rows = dict(conn.execute("SELECT version,name FROM schema_migrations ORDER BY version")) if exists else {}
    common = 0
    for v in range(1, 19):
        if v not in rows:
            break
        if rows[v] != expected.get(v):
            _refuse(f"version {v} filename differs")
        common = v
    extra = {v: name for v, name in rows.items() if v > common}
    if common < 18:
        if extra:
            _refuse("common prefix has a hole")
        history = History(common, 0, 0, False)
    elif extra and extra.get(19) == OLD_NAMES[0]:
        count = len(extra)
        if count > 5 or extra != {i+19: name for i, name in enumerate(OLD_NAMES[:count])}:
            _refuse("checkpoint prefix has a hole or destination collision")
        history = History(18, 0, count, True)
    else:
        main = int(extra.get(19) == MAIN_NAMES[0]) + int(extra.get(20) == MAIN_NAMES[1])
        if main == 1 and 20 in extra:
            _refuse("main prefix has a hole")
        reliability = 0
        while reliability < 5 and extra.get(21+reliability) == CANONICAL_NAMES[reliability]:
            reliability += 1
        allowed = {i+19: n for i, n in enumerate(MAIN_NAMES[:main])}
        allowed.update({i+21: n for i, n in enumerate(CANONICAL_NAMES[:reliability])})
        if extra != allowed:
            _refuse("unknown filename, hole, or mixed migration identity")
        history = History(18, main, reliability, False)
    manifest = json.loads((files("harness") / "migration_schema_manifest.json").read_text())
    wanted = manifest["histories"][history.key]
    actual = identities(conn)
    managed = set(manifest["managed_objects"])
    # New conflicting objects in application-owned namespaces must not hide behind a ledger.
    prefixes = tuple(manifest["managed_prefixes"])
    observed = {k: v for k, v in actual.items() if k in managed or k.startswith(prefixes)}
    if observed != wanted:
        differences = sorted(k for k in observed.keys() | wanted.keys() if observed.get(k) != wanted.get(k))
        _refuse("schema differs at " + ", ".join(differences[:8]))
    return history


def execute_migration(conn, sql):
    pending = ""
    for char in sql:
        pending += char
        if char != ";" or not sqlite3.complete_statement(pending):
            continue
        # Comments are immaterial only when finding the leading control keyword.
        head = re.sub(r"--[^\n]*|/\*.*?\*/", " ", pending, flags=re.S).lstrip()
        if re.match(r"(?i)(BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE|VACUUM|ATTACH|DETACH)\b", head):
            _refuse("migration contains transaction control")
        conn.execute(pending)
        pending = ""
    trailing = re.sub(r"--[^\n]*|/\*.*?\*/", " ", pending, flags=re.S).strip()
    if trailing:
        _refuse("incomplete migration statement")


def reconcile_checkpoint_history(conn, migrations):
    history = validate_history(conn, migrations)
    if history.checkpoint:
        for index in reversed(range(history.reliability)):
            changed = conn.execute(
                "UPDATE schema_migrations SET version=?,name=? WHERE version=? AND name=?",
                (index+21, CANONICAL_NAMES[index], index+19, OLD_NAMES[index]),
            ).rowcount
            if changed != 1:
                _refuse("checkpoint ledger moved during reconciliation")
    return history
