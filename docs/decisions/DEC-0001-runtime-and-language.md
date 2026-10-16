# DEC-0001 — Runtime and language

- **ID:** DEC-0001
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-16
- **Scope:** Whole harness; all phases
- **Supersedes:** none

## Decision

The harness is written in Python 3.13, pinned via uv (`.python-version`). `pyproject.toml` lives at the repository root and the package is `harness/` (flat layout, no `src/`).

## Rationale

Python matches the AL Orchestrator's own Python/pytest scripts (`~/.claude/skills/al`), so the harness and AL share tooling and conventions.

## Impact

All harness code, tests and scripts are Python 3.13. The build backend is `uv_build` with `module-root = ""` for the flat layout. P1-02 builds the Controller on this package.
