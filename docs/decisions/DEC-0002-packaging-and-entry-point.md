# DEC-0002 — Packaging and entry point

- **ID:** DEC-0002
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-16
- **Scope:** Harness distribution and activation (SPEC §2, AC 1)
- **Supersedes:** none

## Decision

The harness ships as a Python CLI package exposing a `harness` console script with `start`, `resume` and `approve` subcommands. A repo-local `.claude/commands/harness.md` is the only entry point and shells out to the CLI. There is no daemon in Phase 1.

## Rationale

A CLI plus slash command mirrors the packaging of the AL Orchestrator's skill and keeps resume a plain process restart over persisted state.

## Impact

P1-02 defines the real CLI shape behind these subcommands. The slash command must stay the sole activation path; nothing activates implicitly.
