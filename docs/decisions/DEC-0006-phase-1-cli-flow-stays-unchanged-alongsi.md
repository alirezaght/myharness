# DEC-0006 — Phase 1 CLI flow stays unchanged alongside the dashboard

- **ID:** DEC-0006
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:13:10+00:00
- **Scope:** functional_behavior
- **Supersedes:** none

## Decision

The Phase 1 CLI flow stays unchanged; the dashboard works on the same SQLite state and Markdown specs, and its approval writes the same immutable snapshot as the CLI gate.

## Rationale

"No regressions in the Phase 1 CLI flow" (tasks/phase-2-visual-design-studio.md P2-08, line 49); "Reads and writes the same SQLite state and Markdown specs as the CLI" (line 11); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45)

## Impact

CLI and dashboard are two front ends over one state; no CLI behaviour or output changes.
