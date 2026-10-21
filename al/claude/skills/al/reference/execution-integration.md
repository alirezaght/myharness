# Harness execution integration

This package implements `al-execution-v1` for approval-bound harness runs.
`SKILL.md` remains the entry point; this reference describes how the supervisor
reports real activity while AL keeps all planning, admission, evidence and
review authority.

## Reporting and admission

Use `integration capabilities` before launch and report only actual invocations.
Register each agent with its stable invocation ID after it starts, update status
when its lifecycle changes, and report its task and activity. Register AL work
as stable tasks with approved criteria, non-overlapping owned paths and existing
dependencies. Planned role allocation is not a running agent. Progress is
measured units, a labelled estimate with its source, or unknown.

Before work, call `integration task-admit --task ID`, then
`integration task-start --task ID`. Harness-bound writer starts must include
the admitted task and actual agent invocation IDs. Existing AL phase, writer,
overlap, gate, evidence and review checks still apply. Finish work through the
normal writer/check commands and `integration task-finish --task ID`; reporting
an agent as Completed is not task completion.

Raise an explicit `integration blocker --file JSON` when a new architectural or
product choice needs the developer. AL blocks that task and its dependents while
unrelated tasks remain eligible. Do not answer the question from model
preference. Poll the execution-bound delivery inbox and apply a decision only
through `integration decision --file JSON`. An acknowledgement records whether
the blocker remains rejected/open, is resolved, or is waiting for Architect
revalidation. An amendment preserves completed independent work and requires
the scoped revalidation packet to be inspected by an actual Architect.

Create handoff events when ownership moves between tasks. Keep task scopes and
dependency graphs accurate; updates are additive in v1. Do not claim test or
implementation success through reporting commands. The ordinary evidence and
fresh/comparison review transitions emit authoritative results.

## Event source and recovery

The run state and event outbox are atomically persisted together. Export pages
with `integration events --after N --limit N`; never edit or truncate retained
outbox records. The harness binds the first `run.started` event to the reserved
AL session and execution. Events are immutable and sequence ordered. Read-only
exports work after a run stops.

Harness decisions carry a frozen delivery ID and exact Registry provenance.
Retry the same bytes after lost acknowledgement; do not create another
decision. Reconnect replays and dispatches pending deliveries without creating
a native conversation. Explicit `execution resume` targets the saved native
session. If process creation is uncertain, stop and request inspected
reconciliation; a missing PID or old heartbeat is not proof that no process
started.

Both Codex and Claude retain their existing host/session behavior. This Codex
package does not change Claude continuation pointers or select a latest host
session. See [the v1 event contract](../../../../../docs/al-event-contract-v1.md)
for envelope, bounds, kinds and authority rules.
