# DEC-0016 — Only the local developer may act in the dashboard

- **ID:** DEC-0016
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-17T11:20:17+00:00
- **Scope:** security
- **Supersedes:** none

## Decision

Only the local developer may act: enforced by binding to localhost only and by approvals going through the same gate as the CLI. No login.

## Rationale

"Binds to localhost only" (tasks/phase-2-visual-design-studio.md P2-01, line 12); "Each developer sees their own local Harness execution state." (docs/SPEC.md §8.10, line 649); "Approval writes the same immutable snapshot as the Phase 1 CLI gate" (tasks/phase-2-visual-design-studio.md P2-07, line 45)

## Impact

No authentication layer; exposure limited to the local machine.
