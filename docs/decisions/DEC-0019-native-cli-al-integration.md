# DEC-0019: Native CLI adapters for AL execution

- **ID:** DEC-0019
- **Status:** approved
- **Version:** 1
- **Timestamp:** 2026-10-19
- **Scope:** Native CLI adapters for AL execution
- **Supersedes:** none

The developer approved the Phase 3 design with “Approve the proposed design,”
including Claude as the default native host, Codex as the explicit alternative,
and the full P3-01 to P3-11 scope. The original request was “create a loop and
let's implement phase 3”. This record captures that choice; it does not replace
the immutable approved Phase 3 requirements or grant implementation authority
to any execution.

## Decision

Use thin adapters over the installed Claude Code and Codex CLIs, with a shared
versioned AL semantic event contract. Keep the existing Python 3.13, SQLite,
HTTP/SSE and host-native AL skills. Do not add an SDK, create a competing
implementation scheduler, silently fall back to another host, or treat host
output as AL approval.

The harness starts only from a current immutable specification approval and
approved test plan. A detached worker owns the selected native process; the
dashboard only reads the durable execution projection and requests explicit
actions. AL retains task planning, admission, evidence, review and guarded
completion. Task-scoped architecture decisions are written to the existing
Decision Registry, delivered idempotently and acknowledged by the bound AL run.

## Consequences and limits

Native process/session facts and AL semantic facts remain separate. Recovery
targets a recorded session identity; uncertain launch state requires explicit
reconciliation. Provider authentication, model entitlement and successful
delegation must be established by a separate bounded live smoke. CLI presence
or help output alone is not evidence of those capabilities. A source checkout
does not update the installed runtime.

Implementation and verification evidence is linked from
[`tasks/phase-3-al-integration.md`](../../tasks/phase-3-al-integration.md) when
recorded. This decision is historical provenance, not final AL review approval.
