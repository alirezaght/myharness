# First reliability slice implementation specification

Implement the approved first vertical slice: a denied or failed supervised operation becomes a persisted, privacy-safe incident visible after restart in the existing dashboard. Architecture decisions are DEC-0048 through DEC-0052. The developer has authorized implementation now as the first increment of the full mission. This document is the implementation specification; it is not a test result.

## Scope and ownership

The developer approved implementation of the full mission now. DEC-0048 v2 removes the blanket Knowledge-acceptance prerequisite. Implement this independent increment on the current source and continue through the full roadmap. Validate specific Knowledge interfaces when used by later increments; preserve every existing observer/authority/approval restriction.

The separate Knowledge mission remains owned by its existing chat. Do not duplicate or import its unfinished work. R1 does not depend on a new Knowledge API and can be implemented, tested and independently reviewed now. Allocate the next unused migration on this current tree and reconcile numbering at later integration. The standalone Claude qualification plan remains unimplemented.

## AL route

Architect classification: **STANDARD**, Engineer then Builder, sequentially. No Designer is required for the narrowed protocol: it reuses the existing short epoch claims and SQLite transactions and deliberately promises no external operation replay. The Engineer owns contracts/store/dispatch/API/security checks; the Builder follows after those interfaces are stable with UI/browser evidence/documentation. Shared APIs prevent parallel writers.

## Behavior and capture limits

R1 begins after existing transport/principal authentication and the current epoch gate succeed. It records subsequent authority/admission denials and synchronous dispatch failures. Existing `await_assignment` early handling remains unchanged and unobserved by R1.

Explicit coverage flags:

```json
{
  "source": "operation-dispatch",
  "early_transport_rejections": false,
  "epoch_rejections": false,
  "assignment_wait_observation": false,
  "historical_backfill": false,
  "async_completion_classification": false
}
```

No raw historical audit/event import, private transcript reading, provider telemetry, Knowledge ingestion, new agent tools, permission changes, retries/recovery, proposals, approval, incident resolution or activation is included. Async processes and application-level failures inside returned dictionaries need later sensors; a dispatch return is not successful completion of the underlying work.

## Source integration points

- `harness/execution_operations.py:377`: existing dispatch, current epoch check, audit, authority and tool execution.
- `harness/execution_operations.py:249`: current principal/approval/policy/run authority checks.
- `harness/execution_epoch_lock.py`: existing shared epoch claim.
- `harness/execution_epochs.py:25`: exact current epoch/generation validation.
- `harness/execution_activity.py:39`: metadata-only audit consumer; preserve its principal/tool/error/status display behavior.
- `harness/dashboard/execution_view.py:36`: task/execution-scoped read pattern.
- `harness/dashboard/server.py:288`: explicit scoped execution GET routes.
- `harness/execution_projection.py:239`: existing semantic-event binding/sequence/replay checks remain unchanged.
- `harness/migrations/0012_delivery.sql:20`: existing operation audit schema.

At the inspected baseline, other operation-audit writers are `harness/command_runner.py` and `harness/diagnostics.py`. They are outside this dispatch-only sensor. The incident API never reads raw JSON from any audit writer. Re-run literal consumer/writer discovery against the current candidate; do not widen claims to all audit paths.

## Closed contracts

Only trusted Harness code can produce `boundary-observation-v1`; no agent submission endpoint is added.

| Field | Required meaning |
| --- | --- |
| `schema_version` | Integer 1 |
| `observation_id` | Random server-generated identity for one attempt |
| `source_kind` | Literal `operation-dispatch` |
| `source_id` | Server-generated audit/attempt identity, never request-selected |
| `execution_id`, `task_id` | Verified owning execution record |
| `epoch_id`, `authority_generation` | Exact current recorded generation |
| `principal_id`, `role` | Authenticated registry identity; capture is ineligible if identity is unavailable |
| `tool` | Known closed tool name or literal `unknown` |
| `policy_digest` | Trusted existing policy binding; unavailable is explicit null |
| `spec_version`, `approval_id` | Persisted execution/spec binding |
| `started_at`, `finished_at` | Harness UTC timestamps; end is null while pending |
| `state` | `pending`, `succeeded`, `denied`, `failed`; succeeded means dispatch returned |
| `code` | Closed code below; null while pending |
| `provenance` | Literal `runtime_observed` |

Never store arguments, command lines, source contents, arbitrary resource paths, provider prose, exception messages or hashes of those values. Execution IDs provide stable correlation in R1; full span/provider accounting is a later slice.

Terminal codes:

- `AUTHORITY_REJECTED`: denial at the existing authority check.
- `DISPATCH_REJECTED`: validation/lookup denial inside dispatch; it is not automatically a policy violation.
- `DISPATCH_OS_ERROR`: caught operating-system error.
- `DISPATCH_PROCESS_ERROR`: caught subprocess error.
- `DISPATCH_INTERNAL_ERROR`: unexpected dispatch exception, recorded if publication remains possible while preserving the existing exception behavior.
- `RETURNED`: normal dispatch return, including application-level failure dictionaries or asynchronous handles.

Classify by boundary/stage and exception type. Do not match exception prose. Distinguish transport success, dispatch return, semantic task result and external side-effect outcome.

An incident has `incident_id`, unique `observation_id`, schema version, `category`, `status`, creation/update timestamps and classification version. Categories are `operation_denied` and `operation_failed`; status is `open`. Exactly one incident projects each terminal denied/failed observation. Human correction/resolution is later work. Symptoms use code-owned templates; root cause is `not_assessed`, with no invented hypothesis or affected component. Evidence references safe observation/source/execution identities.

Pending observations are separate **Outcome not recorded** entries. They do not count as incidents, imply a failed operation, prove cancellation or grant retry authority.

## Durable state and transaction protocol

Add two normalized tables in the current per-repository database using the next available numbered migration against the current candidate:

1. `reliability_observations`: primary observation ID, unique `(source_kind, source_id)`, trusted bindings, closed lifecycle fields and CHECK constraints.
2. `reliability_incidents`: primary incident ID, unique observation foreign key, closed category/status/classification version.

Indexes cover observations `(execution_id, epoch_id, started_at, observation_id)` and incident pagination `(created_at, incident_id)`. Join through validated observation identity for scope; introduce redundant scope columns only if measured query plans require them and insertion enforces equality. Keep startup migration schema-only and add no historical backfill.

The authoritative publication sequence is:

1. Allocate a new random attempt/source identity for each real dispatch invocation.
2. Acquire the existing shared `epoch_action_lock`, recheck exact epoch/generation through `require_active_epoch()`, then use a short SQLite transaction to insert the pending observation and compatible safe audit metadata.
3. Commit and release the transaction and epoch lock before tool execution. No long-running tool holds either lock.
4. Run existing authority/admission and dispatch checks unchanged.
5. Reacquire the shared epoch claim and revalidate exact current generation. In one short transaction finalize the observation, update safe audit outcome metadata and insert the incident when denied/failed.
6. Identical trusted result ingestion is a no-op. A conflicting terminal result for the same source identity refuses. Every distinct invocation gets a distinct source ID even when arguments match.

Crash/transition behavior:

| Boundary | Persisted meaning |
| --- | --- |
| Before pending commit | No dispatch has started |
| Pending commit before dispatch | Outcome not recorded |
| During/after side effects before terminal commit | Outcome not recorded; no automatic retry |
| After terminal commit | Observation and incident persist together |
| Epoch retires between pending and final publication | Final publication refuses; old pending row remains; successor state is untouched |
| Terminal persistence fails after possible side effect | Recording failure/unknown outcome through the existing error boundary; no tool replay |

A stale or inactive controller cannot create or finalize observations. There is no historical append writer and no weakening of `require_active_epoch()`. Already-persisted historical records may be read under explicit historical scope. Missing PID, elapsed time and host exit alone cannot resolve pending state. This is idempotent evidence ingestion, not exactly-once tool execution.

## Privacy and existing audit compatibility

For newly instrumented dispatch audit rows, persist only verified principal, closed tool, observation identity and fixed outcome/error code. Preserve the allowlisted environment status required by `execution_activity.py`; do not copy arbitrary result dictionaries. Verify literal consumers on the actual implementation candidate before changing these fields.

Existing audit rows and semantic events remain historical and unchanged. Their arbitrary text is excluded from incident import, query, API, UI and Knowledge. This slice does not claim that every historical persistence/export path has been remediated.

R1 collects no optional diagnostic detail and adds no detail table or cleanup daemon. Durable closed incident/observation facts remain. DEC-0051's 30-day limit is binding if a later slice adds derived diagnostic detail; that later feature must exclude expired detail from reads and perform bounded cleanup. No data is collected merely to exercise retention.

## Read API

Extend the existing server with:

```text
GET /api/execution/incidents
GET /api/execution/incidents/detail
```

Every request requires one `task`, `execution` and explicit `epoch`. Detail additionally requires `incident`; verify the entire tuple, not just possession of an ID. Read-only historical access uses existing valid epoch history, with no current-epoch fallback for a mismatched ID.

List accepts optional closed `category`, literal `q` (maximum 128 characters), integer `limit` (1–100, default 25), and paired `before_time`/`before_id` for descending keyset pagination. Reject duplicate or unknown query fields. Search only safe IDs/tool/code/category/code-owned labels using parameterized SQL with literal `%` and `_`; do not expose arbitrary FTS or SQL syntax. Reject syntax with 400; mismatched scopes use a generic 404; unavailable storage uses fixed diagnostics without SQL/exception prose.

Responses include schema version, scope, items, nullable next cursor and explicit coverage flags. Pending outcomes appear in a separate bounded section and do not inflate incident counts. GET never reconciles, retries, resolves, starts work or writes state.

## Existing dashboard integration

Add an **Operation incidents** section to the selected execution view through a focused frontend module and minimal existing page integration. Show category, closed symptom, time, safe tool label and scoped evidence detail. Provide labelled search/category controls and Older pagination. Separate pending outcomes with no retry button.

Display: **Observed operation outcomes; no root-cause assessment or approval authority.** Keep history navigation explicit, use literal DOM text, preserve keyboard focus/drafts/disclosures through refresh, and reject delayed responses after task/execution/epoch changes. Reuse the existing refresh/SSE lifecycle; add no separate service or agent permissions.

R1 does not publish incidents into Knowledge. Stable safe references allow a later explicit sourced adapter without creating a second memory platform or granting cross-agent visibility now.

## File ownership and check plan

Engineer owns `execution_operations.py`, new focused reliability contracts/store/observation/query modules, migration, `dashboard/reliability_view.py`, server routes and focused store/dispatch/API/security checks. Modify activity projection only if required for the safe metadata contract.

Builder follows the frozen API with `dashboard/static/reliability.js`, small CSS and execution/index integration, browser tests/screenshots and documentation. No parallel writers on shared schema/API files.

Checks must drive the real dispatcher and HTTP handlers, not only classifiers. Include fault injection before/after each durable boundary, concurrent duplicate/conflicting terminal writes, side-effect sentinels, authentic/foreign/stale identities, canaries in rejected arguments/results/exceptions, scope/pagination/search and rapid UI identity switches. Run existing operation/activity/event/epoch/dashboard regressions and source-backed CI lint/format/nonbrowser/build obligations. Chrome checks desktop and narrow function plus visual alignment, focus, overflow, scrolling, literal rendering and navigation.

## Acceptance criteria

- R1-01: A real eligible denial creates one closed observation/incident and no forbidden effect.
- R1-02: Synchronous dispatch errors classify by boundary/type; returned application error dictionaries and process handles are not guessed into terminal incidents.
- R1-03: Identical trusted evidence ingestion is idempotent; conflicting terminal evidence refuses; separate invocations stay distinct.
- R1-04: Fault injection cannot create false success or automatic replay; pending remains unknown after restart.
- R1-05: Capture binds authenticated principal/current epoch/task/execution/policy/spec from trusted state. Caller fields cannot redirect persistence or disclosure.
- R1-06: Stale/inactive controllers cannot create/finalize records; a transition between intent and terminal publication preserves pending and leaves the successor untouched.
- R1-07: Independent secret canaries and their hashes are absent from new instrumented audit/observation/incident fields and API/UI responses.
- R1-08: Existing semantic event sequence/binding/replay and activity presentation remain compatible.
- R1-09: Scoped list/detail/search and pagination are bounded, stable, literal and read-only; delayed UI responses cannot cross scope.
- R1-10: No optional diagnostics are collected; the approved future derived-detail retention limit remains binding.
- R1-11: Desktop/narrow Chrome functional, keyboard/focus, scrolling/overflow and visual checks pass with retained screenshots.
- R1-12: No provider, observer, authentication, permission, Knowledge-visibility, approval or activation change; existing refusals remain.
- R1-13: Early transport/epoch/assignment and asynchronous coverage gaps are visible, not described as complete observability.
- R1-14: Current source integration, relevant required checks and independent AL review precede verified delivery; no blanket Knowledge acceptance gate applies.

## Planning review correction

The first Architect draft proposed historical append after epoch rejection. Supervisor review identified the new observation-authority risk; the Architect removed it before implementation. This specification includes the revised current-epoch-only protocol. No such historical writer or runtime sensor was implemented.
