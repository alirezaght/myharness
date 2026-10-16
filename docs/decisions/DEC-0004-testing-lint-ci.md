# DEC-0004 — Testing, lint and CI

- **ID:** DEC-0004
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-16
- **Scope:** Repository quality gates
- **Supersedes:** none

## Decision

Tests use pytest; lint and format use ruff; dependencies are managed with uv and committed as `uv.lock`. GitHub Actions (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check` and `pytest` on push to main and on pull requests.

## Rationale

pytest, ruff and uv match the AL Orchestrator's test suite, and GitHub Actions matches this repository's actual remote.

## Impact

All tests in later tasks are pytest tests. CI gates every push to main and every pull request.
