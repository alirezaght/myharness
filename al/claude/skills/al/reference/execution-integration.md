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

## Bounded recovery and task admission
A pending internal `REWORK_REASSESSMENT` records engineering repair work and cannot by itself block otherwise authorized scoped implementation or require a developer continuation. Preserve the actual decision, budget and failure history; never reset it to simulate a grant. Final evidence and approval checks remain strict. Preserve independent blockers and their task scope; a recovery credit cannot clear them. After a valid Architect allow, the runtime consumes exactly the pending first return in the new two-return batch. A decline or automatic-cap stop records a checkpoint; only an explicit bound user continuation may grant another two-return batch. Old immutable runtime issuers do not gain these commands after installation update.

## Portable inputs and ownership audit

In Harness, use its `read_file` tool with `harness:skill/index` to enumerate the
exact protected pin. Read `harness:skill` for SKILL.md and
`harness:skill/reference/handoffs.md` or `harness:skill/reference/schemas/blocker.json`
for references and schemas. Follow bounded offsets until `truncated` is false;
retain resource hashes. Repository file confinement still applies. Required
inputs do not require a global filesystem or shell capability.

Before final classification/planning, read `harness:repository-audit`. Standalone
AL uses `routing-audit --request "APPROVED REQUEST"`. Inspect manifest ownership,
literal call sites, applicable nested AGENTS.md, existing implementation leads
and locally known main divergence. Remote freshness is unverified without an
explicit fetch. Keyword matches, test volume and top-level folder names do not
establish ownership or complexity. Inspect the cited implementation and classify
its remaining design work; resolve material ownership uncertainty before planning.

Malformed integration inputs return an `input_schema` diagnostic with the
operation, missing/unexpected fields and recovery reference. For blocker input,
read `reference/schemas/blocker.json` through the same portable resource mapping.
Correct the input and retry; a validation failure does not register a blocker.

## Prior standalone planning
A developer may request `harness execution adopt-planning` with exact source
repository/platform/session/run/task/report hashes, the target handoff hash, and
complete target-to-source criterion/invariant mappings. The protected
`alctl planning-proof` command independently validates the current standalone
planning occurrence and its original pin, report, obligations and source. Changed
source, policy or incomplete mappings refuse transfer with a recovery diagnostic.

Before authoring a Harness planning report, read `harness:planning-adoptions`;
retrieve any named receipt through its hash-bound artifact resource. These are
verified reference inputs for reuse. The current Architect must inspect them,
revalidate the current approved requirements/source and author a new current
planning report and obligations. No old admission, approval, task state, evidence
result or native identity is copied; neither workflow is restarted.


## Fresh invocation and authenticated capability release

A terminal or replaced native invocation is never revived. For a supported fresh
registration on an already assigned task, `integration agent-register` accepts
`replaces` (the exact predecessor invocation ID) and a substantive `release_reason`.
Harness validates the new observed identity, creation provenance and role, the
exact old assignment, and settled writers, checks and owned processes before
issuing immutable capability-release and assignment-history receipts. The old
capability is irrevocably fenced, including cached principals.

This is capability release, not evidence of host-thread termination. Preserve the
old actual agent status and the receipt's `host_termination_observed: false` when
no trusted host event establishes termination. An empty wait result or completed
wait tool call does not prove that a child agent finished. Unsupported creation
provenance is a technical capability problem; never manufacture a native event or
ask a developer product decision to bypass it.
