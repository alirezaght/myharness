# DEC-0010 — Dashboard component communication

- **ID:** DEC-0010
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:17:01+00:00
- **Scope:** component_communication
- **Supersedes:** none

## Decision

The browser talks to a local server over HTTP on localhost; the server calls the harness engines and SQLite in-process; live updates are pushed by the server. The exact framework stays open (SPEC §15).

## Rationale

"The dashboard receives live status updates without requiring manual page refreshes." (docs/SPEC.md §8.11, line 657); "Reads and writes the same SQLite state and Markdown specs as the CLI" (tasks/phase-2-visual-design-studio.md P2-01, line 11)

## Impact

One local process owns state access; the browser never touches SQLite directly.
