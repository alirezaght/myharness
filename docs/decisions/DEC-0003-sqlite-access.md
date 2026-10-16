# DEC-0003 — SQLite access

- **ID:** DEC-0003
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-16
- **Scope:** Local runtime state (SPEC §7)
- **Supersedes:** none

## Decision

Local state uses stdlib `sqlite3` in WAL mode with raw numbered `.sql` migrations tracked in a `schema_migrations` table. No ORM.

## Rationale

State is single-user and local, so the standard library is sufficient and adds no dependency or abstraction layer.

## Impact

P1-03 implements the migration runner and schema on this mechanism. The database file is never committed (ignored by `.gitignore`).
