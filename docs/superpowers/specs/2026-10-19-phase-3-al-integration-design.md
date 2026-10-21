# Phase 3: AL integration and live monitoring

Date: 2026-10-19. Status: proposed for developer approval. Implementation has not
started. Source request: “create a loop and let's implement phase 3”.

## Goal and boundary

Implement P3-01 through P3-11 of
[the Phase 3 checklist](../../../tasks/phase-3-al-integration.md) and SPEC sections
8.2–8.5, 8.11, 9 and 12. An approved harness specification launches the existing
AL orchestrator, shows real execution activity, lets the developer resolve
task-scoped design blockers, and reconnects without relaunching completed work.

The harness owns requirements, approvals, durable supervision and presentation.
AL owns implementation planning, agent allocation, technical verification and
review. The harness must not create a second implementation scheduler or infer
an AL approval from prose, a successful host exit or a fabricated event.

No Phase 4 GitLab automation, Phase 5 staging validation, automatic merge, real
repository commit/tag/push, remote publication or new database service is part
of this change. Existing local SQLite remains the runtime store. Preserve all
pre-existing work, including the completed AL installer and performance fixes.

## Integration choice proposed for approval

Use thin native CLI host adapters and a common AL semantic event contract.
Claude is the default host, consistent with the repository's existing `/harness`
entry point; Codex is explicitly selectable. Do not silently switch hosts when
authentication, capabilities or an AL prerequisite fail.

An SDK/app-server integration is an alternative with richer host RPC, but is not
needed for this milestone. Terminal-only observation is another alternative,
with weaker unattended lifecycle control. The rationale and capability gaps are
in [the audit](../../phase-3-al-capability-audit.md).

The implementation retains Python 3.13, stdlib SQLite and HTTP/SSE, with pytest,
Ruff and the existing browser-test tooling. No frontend framework or model SDK
is introduced merely to wrap the native CLIs. Fixed argument arrays are used;
specification text is data, not interpolated shell code.

## Execution and approval flow

1. The developer enters through `/harness` and completes the existing interview,
   design, test plan and approval flow.
2. The dashboard's Approve & Implement action seals the spec through the existing
   approval transaction and then requests an idempotent execution launch. The
   CLI gains an explicit execution-start command for already approved tasks.
   Existing `harness approve` sealing semantics remain usable; it must never
   launch an unapproved or stale spec.
3. The adapter calls `require_approval`, verifies sealed bytes and test-plan
   approval, and binds the launch to task ID, approval ID, spec version, content
   hash, revision and review-state hash. It writes a private immutable handoff
   envelope, including the approved specification and test plan, outside Git.
4. One durable launch reservation per approval prevents repeated clicks,
   concurrent processes and retries from starting duplicate runs. A separate
   worker owns the native host process. Closing a dashboard or disconnecting a
   harness client does not terminate that worker.
5. The worker launches the selected installed AL skill with the exact envelope.
   Capability failures are explicit. An older installed skill without the event
   protocol cannot be treated as a working integration. Source package changes
   do not silently upgrade an actively running global installation.
6. Persist host session/thread identity and the distinct AL session/run identity.
   Native host JSON handles session/process facts; AL events carry semantic
   agent/task/evidence results. Bind every accepted event to the immutable
   harness execution and approved spec.
7. Recovery first reconnects to the recorded worker/run and replays committed
   events. Explicit host resume targets the recorded conversation; never use
   a “latest session” selector. An uncertain launch is exposed for reconciliation,
   never blindly launched again. Completed runs are read-only on resume.

Execution state is tracked separately from the pre-execution approval phase, so
the Phase 1 approval guard and tests retain their meaning. Display execution
status in task summaries once a run exists. Reapproval creates a new binding;
it does not retarget an existing run. A stale, draft, superseded or modified
specification cannot start or receive further execution authority.

## AL event and decision protocol

Introduce a versioned JSON contract shared by the Codex and Claude AL packages.
Each envelope contains schema version, event ID, sequence, timestamp, harness
execution ID, harness task ID, approved spec version/hash, AL run/session ID,
event kind and a validated payload. Agent/task identities are stable within a
run. Unsupported schema versions or foreign bindings are rejected explicitly.

Event families cover run lifecycle, phase changes, agent lifecycle, task
assignment/progress/completion, dependency/ownership/handoff changes,
blocker raised/resolved, tests, implementation result and delivery updates.
Delivery event support is contract readiness; it does not fabricate GitLab or
deployment integrations that belong to Phase 4.

AL records events durably at the same authoritative state mutation boundary as
the represented transition, using an outbox/replay mechanism that cannot lose
an acknowledged event between state persistence and export. A partial trailing
stream record is not accepted as a complete event. Duplicate identical events
are idempotent; conflicting duplicates, sequence gaps and identity mismatches
surface as errors rather than silently changing the projected state.

Existing phase, writer, check and review transitions emit their corresponding
facts. The AL supervisor also reports actual read-only agent lifecycle and
task/dependency/blocker facts through a bounded validated interface. Its skill
instructions require those reports. A planned role is not displayed as a
running agent until it actually starts. Existing evidence, gate and review
guards remain authoritative and cannot be bypassed by reporting an event.

Task-scoped design/product blockers identify affected tasks, required decision,
context, alternatives and a recommendation. AL refuses affected task admission
while unresolved; siblings without that dependency can continue under the
existing parallelism rules. This is an admission guard, not a harness scheduler.
The complete AL run is not falsely marked failed or completed because one task
is blocked. Dependency cycles and invalid task ownership must be rejected.

The developer resolves a blocker in the Approval Center or CLI. The decision is
recorded in the existing versioned Decision Registry before delivery to AL,
with a durable delivery ID and acknowledgement. Retrying cannot duplicate the
decision or apply it to another blocker/run/spec. AL must validate acknowledgement
and applicability before affected work resumes. If the decision changes approved
requirements or architecture, it is an explicit authorized amendment with
traceability; it must not silently mutate the original approved spec or grant
unrelated implementation authority. Rejecting a recommendation does not mean
it was accepted. A missing developer answer remains blocking.

## Persistence and recovery

Add local migrations and focused persistence modules for executions, host/run
bindings, event ingestion cursors, normalized agents/tasks/dependencies/blockers,
decisions awaiting acknowledgement and worker/launch ownership. Use uniqueness
constraints and short transactions for idempotence and concurrency control.

The event and projected state update commit together. A crash can replay from
the last committed cursor without losing or applying an event twice. Durable
private logs retain useful diagnostics with bounded input and output sizes;
secrets are excluded/redacted from presentation. Arbitrary client-supplied paths
must not select log files or executable commands.

A liveness timestamp or PID alone is not proof that a run completed. Report
Disconnected, Interrupted, Unavailable or recovery-required state accurately.
Successful implementation requires the actual AL guarded approval/result bound
to this execution; host success without it is incomplete. Host failures preserve
partial evidence, identity and actionable diagnostics. No unbounded automatic
retry loop or default permission-bypass flags are introduced.

## Dashboard and CLI

Extend the existing dashboard and SSE transport rather than add another web
server. The selected task gains an execution summary, live agent list, measured
progress, dependency/ownership graph, activity feed and scoped detailed logs.
Existing spec/decision/diagram views remain available.

Each agent shows name, role, assigned task, activity, status, progress, duration,
dependencies, blockers and latest meaningful output. Support Idle, Running,
Waiting, Blocked, Failed and Completed. Progress shows completed/total measurable
units, or an explicitly labelled estimate with its source. Unknown progress is
shown as unknown; elapsed time or generated prose cannot manufacture percentages.

The graph shows actual dependencies, parallel activity, ownership and handoffs,
including blocked and completed paths. The feed contains meaningful structured
events; raw reasoning is not presented as a progress signal. Details and logs
are available on demand through execution-bound routes.

Blockers appear in the existing Approval Center with context, recommendation,
developer decision input, delivery/acknowledgement state and retryable error
messages. A decision action cannot accidentally approve an entire new spec.
Keep the existing same-origin/Host checks, request validation and idempotent
write conventions. DOM rendering uses safe text for untrusted content.

Dashboard connection status and AL source freshness are separate: a live SSE
connection does not imply that AL is still connected. Preserve the last known
state while clearly labelling stale/unavailable sources. Inspect desktop and
mobile layouts, long content, graph overflow and control alignment during
browser acceptance. Fix regressions caused by this work and necessary touched
layout constraints; report unrelated baseline issues explicitly.

Provide CLI commands for execution start, status, events/logs, reconnect/resume,
and blocker decisions, following the repository's current parser conventions.
Exact internal names/file placement remain delegated to AL. Update `/harness`
and resume guidance so a current approval can enter execution rather than report
that execution is an unimplemented future phase.

## Verification and acceptance

- P3-01: record the inspected AL capabilities, gaps, approved integration choice
  and limits without claiming help output proves a live host run.
- P3-02/P3-03: validate event schema and real state transitions for both AL
  platforms, durable replay, malformed/duplicate/foreign events, guards and
  pre-existing runtime behavior. Keep package parity/distribution manifests
  current where their contract requires it.
- P3-04/P3-10: use real local SQLite/spec approval and real disposable AL state
  for handoff identity, stale/draft/superseded refusals, concurrent launch,
  reconnect and uncertain-launch recovery. Fake only the external model/provider
  boundary where necessary; distinguish this evidence from live provider tests.
- P3-05: raise a new architecture decision for task A, demonstrate that affected
  admission blocks while independent task B completes, then record the explicit
  developer decision and acknowledge it before A resumes. Cover lost/replayed
  acknowledgements, rejected recommendations and scope-changing amendments.
- P3-06 through P3-09: HTTP and headless Chrome acceptance for agent status,
  progress, graph, feed, source freshness and blocker actions; inspect captured
  desktop/mobile screenshots for visual alignment and UX issues.
- P3-11: approve a spec, launch through the adapter, observe live agents, exercise
  the scoped blocker and reconnect after dashboard/worker-client interruption.
  Demonstrate no completed work relaunch. Record a reproducible disposable-state
  demo and link every Phase 3 checklist item to evidence.
- Run a narrowly scoped live native-host smoke on disposable state when available
  to establish the real launcher/skill/event path. Authentication or capability
  failures are reported accurately, never converted into a simulated pass or
  silently worked around by switching credentials or hosts.
- Run repository lint, format check, complete harness tests and build against the
  final candidate, plus appropriate focused/broad AL suites for changed runtime
  modules. Use AL's ledger and gates; reuse current evidence where valid.

## Bounded implementation loop

After design approval: AL Architect specification gate and plan, Designer for
this complex cross-boundary task, Engineer core implementation, Builder support
and integration, verification, independent fresh Architect review, independent
comparison review, and bounded corrections. Sequential work is the default where
interfaces or shared state depend on one another. AL permits at most two
pre-review rework returns per batch and six review correction iterations.

The goal remains active until all authorized Phase 3 requirements and acceptance
checks are complete and Architect approval is recorded. A material new product
or architecture choice returns to the developer with a concrete proposal.
No run is declared complete merely because its budget or a tool limit is near.

## Design self-review

The proposal fixes host selection, source of semantic truth, approval binding,
durability, worker lifetime, blocker authority, recovery and acceptance scope.
The audit and design are draft artifacts, not an approval record. Internal class
names, table names and exact endpoint spelling remain implementation details.
There are no placeholder decisions presented as approved requirements.
