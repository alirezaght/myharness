# DEC-0018 — Phase 2 verification approach

- **ID:** DEC-0018
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:20:27+00:00
- **Scope:** acceptance_criteria
- **Supersedes:** none

## Decision

Automated tests for each Phase 2 task, an end-to-end test of the wrong-connection scenario, the Phase 1 test suite as the regression check, and a recorded demo.

## Rationale

"Scenario: a diagram with a wrong connection → comment → harness fixes it → approval only becomes possible once every comment is resolved" (tasks/phase-2-visual-design-studio.md P2-08, line 48); "No regressions in the Phase 1 CLI flow" (line 49); "Docs and demo" (line 50)

## Impact

Each acceptance criterion has an automated check plus a demo.
