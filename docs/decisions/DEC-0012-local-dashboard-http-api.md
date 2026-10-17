# DEC-0012 — Local dashboard HTTP API

- **ID:** DEC-0012
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:17:10+00:00
- **Scope:** api_contracts
- **Supersedes:** none

## Decision

A new local HTTP API used only by the dashboard frontend: read task, spec, decisions and diagrams; add a comment; resolve or re-open a comment; approve. No CLI command changes.

## Rationale

"Select a component or connection and add a comment (clarification / correction / redesign)" (tasks/phase-2-visual-design-studio.md P2-05, line 33); "The developer resolves or re-opens each comment (AC 8)" (line 40); "No regressions in the Phase 1 CLI flow" (line 49)

## Impact

Internal localhost API, no external consumers; CLI contract unchanged.
