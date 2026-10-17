# DEC-0008 — Concurrent CLI and dashboard operations

- **ID:** DEC-0008
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:13:32+00:00
- **Scope:** edge_cases
- **Supersedes:** none

## Decision

CLI and dashboard may act on the same task. Every write is one SQLite transaction (DEC-0003). The dashboard re-reads after each write and marks outdated data; a comment or approval that refers to a diagram version that is no longer current is refused with a reload hint.

## Rationale

"The dashboard must clearly distinguish current information from stale or unavailable status data." (docs/SPEC.md §8.11, line 661). The rest is the harness recommendation; the sources do not settle it.

## Impact

No lost or misattributed comments when CLI and dashboard are used together.
