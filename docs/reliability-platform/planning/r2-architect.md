SPEC_STATUS: SPEC_READY

OUTPUT_ARTIFACT: R2-CORRELATED-EXECUTION-TIMELINE-PLAN

TASK_CLASSIFICATION: STANDARD  
IMPLEMENTER: both, Engineer then Builder sequentially  
DESIGNER_REQUIRED: NO  
PARALLEL_SAFE: NO

This slice extends the actual R1 contracts inspected during this turn. Revalidate them after R1 stabilizes. It does not introduce historical publication, durable process recovery, budget enforcement or new observation authority.

## Outcome

The existing dashboard shows a persisted, scoped timeline linking:

- Execution and epoch.
- R1 operation attempts and incidents.
- Owned subprocess launch, exit, cancellation request and timeout observation.
- Validated semantic task/agent/check/decision/output events.
- Authorized retrieval bundle references.

It distinguishes dispatch return, process exit, semantic report and verified acceptance. Missing observations remain explicit.

## Inspected integration points

- `harness/reliability/contracts.py`: closed tools, outcome codes and R1 coverage.
- `harness/reliability/store.py`: `begin()`, `finish()` and `_publication()` already use short epoch claims and transactions.
- `harness/reliability/query.py`: complete task/execution/epoch scope validation and closed read projections.
- `harness/migrations/0019_reliability_observations.sql`: existing observation/incident storage.
- `harness/execution_operations.py`: dispatch instrumentation, process starts currently at approximately lines 671 and 923, Knowledge handlers at 435.
- `harness/execution_processes.py:17–89`: start and in-memory owner/task/revision records.
- `harness/execution_processes.py:96–171`: actual drain, timeout/cancellation, cleanup and terminal observation.
- `harness/execution_projection.py:239`: atomic validated event ingestion.
- `harness/knowledge/integration.py:136–175`: authorized assignment-context references.

The current process drain merges cancellation and timeout into one error string. R2 must record distinct closed causes at the actual branches; parsing that string is not acceptable.

## Data and compatibility

Add the next unused migration after R1, currently expected to follow `0019`, containing:

1. `reliability_traces`
   - `trace_id` random primary key.
   - `execution_id` unique foreign key.
   - `created_at`.
   - One trace per existing execution; epochs remain separate partitions.
2. `reliability_process_spans`
   - Random `span_id`.
   - Execution/epoch/generation, verified principal and admitted task/revision.
   - Parent R1 `observation_id`.
   - Server-generated process attempt ID.
   - Lifecycle timestamps and measured duration.
   - Closed launch/outcome/cleanup fields.
   - Unique process-attempt identity.
3. `reliability_timeline_links`
   - Random link ID.
   - Trace/execution/epoch.
   - Source kind and source ID.
   - Parent observation/span reference where proven.
   - Closed event kind, provenance class and timestamp.
   - Unique `(execution_id, epoch_id, source_kind, source_id, event_kind)`.

Do not duplicate R1 principal/tool/spec/policy fields into a second operation table. Timeline queries join existing observations and incidents. Do not import `operation_audit` bodies.

A trace covers one execution across its recorded epochs. Cross-execution lineage links may reference an existing explicit lineage relation; do not invent a shared root trace from proximity or matching text.

Migration rules:

- Schema-only startup migration.
- Existing R1 rows remain unchanged and readable.
- Historical R1 rows can be presented through joins without fabricating timing or process spans.
- Old executions lacking trace rows receive a truthful “historical correlation unavailable” result until a current authorized publication creates the execution’s trace.
- No mutation on GET.
- No bulk reinterpretation of historical semantic payloads.
- Readers tolerate absent optional R2 links; pinned older runtimes retain their existing behavior.

## Closed event vocabulary

Provenance:

- `runtime_observed`
- `validated_semantic_report`
- `historical_reference`

Event kinds:

```text
operation.started
operation.returned
operation.denied
operation.failed
process.launch_pending
process.started
process.launch_failed
process.cancel_requested
process.timeout_observed
process.exited
process.cleanup_unconfirmed
semantic.agent
semantic.task
semantic.check
semantic.decision
semantic.output
semantic.run
retrieval.bundle
```

These are projection labels, not replacements for existing AL semantic kinds.

Process span fields separate:

- `launch_state`: `pending`, `started`, `not_started`.
- `terminal_state`: null, `exited`, `observation_failed`.
- `returncode`: nullable integer.
- `stop_reason`: null, `cancel_requested`, `deadline_exceeded`, `output_limit`, `io_error`, `internal_error`.
- `cleanup_status`: `unobserved`, `observed`, `unconfirmed`.

An observed return code does not imply successful cleanup, successful check publication or task acceptance. A cancellation request is not a terminal result.

No raw argv, cwd, output, exception prose, process environment or hashes of these values enter the timeline.

## Producer boundaries

### R1 dispatch observations

Render existing R1 rows directly. Their `succeeded` means **dispatch returned**, including returned application errors or process handles.

Create process parentage through an internal call-scoped observation reference passed from dispatch to process-start helpers. Do not attach it through mutable controller-wide “current observation” state: concurrent dispatches must not cross-link.

### Owned process lifecycle

Extend `ProcessManager.start()` with an optional internal lifecycle sink and immutable correlation context. Existing callers without the sink remain compatible.

Sequence:

1. Allocate the process-attempt ID before spawn.
2. Persist `launch_pending` through the current-epoch publication pattern.
3. Spawn using unchanged `operation_popen()`.
4. Record `started` after actual successful creation.
5. Record `not_started` only when spawn itself fails before process creation.
6. Observe cancellation request and timeout at their actual control branches.
7. After existing cleanup and report publication handling, publish a closed terminal snapshot.

Persistence callbacks must run outside `ProcessManager.lock`; do not create a lock inversion between process-manager locks, SQLite transactions and epoch claims.

Do not change cleanup, kill, report validation or polling behavior to make telemetry easier.

If the initial durable intent cannot be recorded, refuse that instrumented launch before spawning. If recording fails after spawn, preserve the owned process and existing cleanup path, expose telemetry-unavailable status, and never replay the command.

If epoch authority changes, publication refuses and the prior span remains incomplete. No stale callback exception or historical append path is introduced.

Use monotonic time within the actual supervising process for duration. After restart or missing observation, duration is unavailable; do not subtract wall timestamps and claim measured latency.

### Semantic events

At the existing successful `ingest_event()` transaction, add safe source links for selected event families. Link insertion and original event/cursor commit together.

- Preserve original event validation, sequence, duplicate and authority behavior.
- Identical event replay creates no duplicate timeline link.
- Store only source identity, closed kind and safe explicitly validated references.
- Do not copy handoff text, blocker questions, result prose or arbitrary payload fields.
- UI labels these as reported/validated semantic events, not independent runtime measurements.
- Existing authoritative evidence validation remains responsible for acceptance claims.

### Retrieval

In authenticated `knowledge_query`/`knowledge_explain` handlers, link the actual returned bundle/reference only after normal scope validation succeeds.

For assignment context, use the existing authorized result from `assignment_context()`; do not introduce a second query or broaden source selection.

Links may include bundle hash, snapshot ID and receipt ID where those were actually returned. They must not expose bundle content or create a new read capability. Following evidence still uses existing Knowledge authorization.

Unavailable retrieval produces an availability indication, not a fabricated bundle or a failure of unrelated execution.

## API

Add a read-only route using the existing execution route conventions:

```text
GET /api/execution/timeline
GET /api/execution/timeline/span
```

Require explicit `task`, `execution`, `epoch`. Validate the tuple through the established reliability scope function.

List parameters:

- Closed `kind` filter.
- Optional verified `principal` filter.
- `limit` 1–100, default 25.
- Paired keyset cursor `(before_time, before_id)`.

Response:

```text
schema_version
scope
trace_id | null
items[]
next_cursor | null
coverage
measurement_availability
```

Each item exposes safe source references, provenance, known parent linkage and explicit missing fields. Span detail must validate membership in the selected scope.

Coverage names which producers are enabled, historical sources lack links and native child/usage observation is unavailable. Token/cost fields remain unavailable—not zero—and are not inferred from byte counts.

Use fixed safe errors, reject duplicate/unknown parameters, and return generic scoped 404s. No route starts work, creates a trace, finalizes a span or refreshes Knowledge.

## UI

Extend the current execution view with one timeline using the existing refresh lifecycle.

- Group by authenticated principal where available.
- Separate operation return from process outcome.
- Show cancellation request, timeout and cleanup uncertainty distinctly.
- Link R1 incidents and authorized semantic/retrieval evidence.
- Display provenance badges and unavailable measurements.
- Preserve pagination, focus, expanded details and selected filters across refresh.
- Discard delayed responses after task/execution/epoch changes.
- No new retry, approval or activation controls.

For historical comparisons, compare only existing closed counts and measured durations with matching coverage. If insufficient, show the reason rather than a misleading percentage.

## Ownership

Engineer:

- New focused trace/process/link modules under `harness/reliability/`.
- Narrow extensions to R1 query/store abstractions.
- Next migration.
- `harness/execution_processes.py`.
- Both actual process-start call sites and Knowledge result-link seams.
- `harness/execution_projection.py`.
- Dashboard timeline read handler/server registration.
- Persistence, fault, identity, privacy and integration tests.

Builder after interface freeze:

- Existing reliability/execution frontend modules and styling.
- Timeline browser fixtures and screenshots.
- Documentation and full stable-candidate verification.

No parallel writers across changing contracts.

## Acceptance criteria

- R2-01: A real owned command links its R1 dispatch observation to persisted launch/start/exit facts.
- R2-02: Nonzero exit, cancellation request, timeout and cleanup uncertainty remain distinct.
- R2-03: Restart preserves known records; missing outcome remains incomplete, with no replay or false settlement.
- R2-04: Two concurrent commands cannot exchange parent observations or principals.
- R2-05: Current-epoch checks fence every runtime publication; retirement prevents later writes without affecting successor state.
- R2-06: Repeated identical semantic ingestion is idempotent; conflicting source events retain existing rejection behavior.
- R2-07: Semantic test reports are labelled separately from runtime process observations and verified acceptance.
- R2-08: Retrieval links reference actual authorized bundles without exporting their contents or granting access.
- R2-09: Old R1 databases upgrade without losing incidents; missing historical correlation is explicit; GET performs no writes.
- R2-10: Canary secrets and their hashes are absent from new tables, API and rendered content.
- R2-11: List/detail pagination and identity switching cannot expose another scope.
- R2-12: Token/cost/native child observations remain unavailable unless independently qualified.
- R2-13: Desktop/narrow Chrome passes functional, literal-rendering, alignment, overflow, scrolling, keyboard and focus checks.
- R2-14: Existing R1, process ownership, event replay, epoch, Knowledge scope and dashboard regressions pass.

Required tests drive actual `ProcessManager` launches with owned disposable commands, the real ingestion interface and real HTTP handlers. Include fault injection around spawn and terminal publication, current-to-retired epoch races and independent privacy canaries. Record source-bound CI verification on the stable candidate; no test execution or passing result is claimed by this plan.

### Reassessment boundary

STANDARD is justified while this remains observational and preserves incomplete outcomes. If implementation seeks to reconstruct lost process ownership, guarantee terminal recording across epoch retirement, add a durable process supervisor or infer private native child lifecycle, that is a new coupled protocol: stop that expansion and route it to Designer. It is not required to deliver this complete R2 slice.
