# DEC-0007 — Dashboard outputs and side effects

- **ID:** DEC-0007
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:13:25+00:00
- **Scope:** inputs_outputs
- **Supersedes:** none

## Decision

The dashboard writes only: comments in the task's SQLite state tied to the element id and diagram version, versioned diagrams, and the approval snapshot. Nothing else is written.

## Rationale

"Comments persist in task state and are tied to the element id and the diagram version" (tasks/phase-2-visual-design-studio.md P2-05, line 34); "Version history for diagrams" (line 28); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (line 45)

## Impact

Bounds the dashboard write surface to task state, diagram versions and the existing approval snapshot.
