# Correlated execution timeline

Actual AL-Architect artifact: R2-CORRELATED-EXECUTION-TIMELINE-PLAN. SPEC_READY; STANDARD; Engineer then Builder sequentially; no Designer. This is the next increment of the full approved mission. No blanket Knowledge acceptance gate applies. Revalidate the actual R1 contracts before writing.

## Outcome and boundaries

The existing dashboard shows a persisted, scoped timeline linking execution and epoch, R1 operation attempts/incidents, owned subprocess launch/exit/cancellation request/timeout, validated semantic task/agent/check/decision/output events, and authorized retrieval bundle references. Distinguish dispatch return, process exit, semantic report and verified acceptance. Missing observations remain explicit. No historical publication, durable process recovery, budget enforcement or new observation authority.

Inspected seams: reliability/contracts.py, store.py begin/finish/_publication, query.py scope validation, migration0019, execution_operations.py actual process-start and Knowledge handlers, execution_processes.py start/drain, execution_projection.py ingest_event, knowledge/integration.py assignment_context. Cancellation and timeout must be captured at actual distinct control branches, never parsed from error prose.

## Durable contracts

Next unused schema-only migration after0019 adds:

- reliability_traces: random trace_id primary key, unique execution_id foreign key, created_at. One execution trace across epochs; explicit existing cross-execution lineage only, never inferred parentage from time/text.
- reliability_process_spans: random span_id; execution/epoch/generation; verified principal and admitted task/revision; parent R1 observation_id; server-generated process-attempt identity; lifecycle times; measured duration; closed launch/outcome/cleanup fields; unique attempt identity.
- reliability_timeline_links: random link ID; trace/execution/epoch; source_kind/source_id; proven parent observation/span if known; closed event_kind/provenance/time; unique execution/epoch/source_kind/source_id/event_kind.

Do not duplicate operation metadata from R1. Query joins existing observations/incidents. Never import operation_audit bodies. Existing R1 rows remain unchanged and readable through joins. Historical executions without a trace return historical correlation unavailable until an authorized current publication creates it. No mutation on GET, bulk reinterpretation of historical semantic payloads, or rewrite of immutable old snapshots. Missing optional R2 links are supported.

Provenance: runtime_observed, validated_semantic_report, historical_reference.

Closed event kinds: operation.started, operation.returned, operation.denied, operation.failed; process.launch_pending, process.started, process.launch_failed, process.cancel_requested, process.timeout_observed, process.exited, process.cleanup_unconfirmed; semantic.agent, semantic.task, semantic.check, semantic.decision, semantic.output, semantic.run; retrieval.bundle. These project existing events without replacing their authority.

Process launch_state: pending|started|not_started. terminal_state: null|exited|observation_failed. returncode nullable integer. stop_reason: null|cancel_requested|deadline_exceeded|output_limit|io_error|internal_error. cleanup_status: unobserved|observed|unconfirmed. Return code does not prove cleanup, check publication or acceptance. Cancellation request is not terminal. No raw argv/cwd/output/environment/exception prose or hashes of these values.

## Producers

Render R1 rows directly. succeeded means dispatch returned, including application failure dictionaries and process handles. Pass parent observation via immutable internal call-scoped context; mutable controller-wide current-observation state would cross-link concurrent dispatches and is prohibited.

ProcessManager.start gains an optional internal lifecycle sink and immutable correlation context. Existing no-sink callers remain compatible. Allocate attempt identity before spawn, persist launch_pending using current-epoch publication, then unchanged operation_popen. Mark started only after actual creation, not_started only on spawn failure before creation. Observe cancellation request and timeout at their actual branches; after existing cleanup/report handling publish the closed terminal snapshot. Callbacks run outside ProcessManager.lock; preserve cleanup/kill/report/poll behavior and lock order. Initial intent recording failure refuses the instrumented launch before spawn. Failure after spawn preserves ownership/cleanup, exposes telemetry unavailable and never replays. Retired callbacks refuse; prior spans remain incomplete. Measure durations with the supervisor's monotonic clock; restart/missing observations leave duration unavailable rather than wall-time subtraction.

At successful ingest_event transaction insert safe links for selected semantic families atomically with original event/cursor. Preserve validation/sequence/replay/authority. Duplicate replay yields no duplicate link. Only source IDs, closed kinds and explicitly validated references; no handoff/blocker/result prose. Label as semantic reports, not runtime measurement or independent acceptance.

Knowledge query/explain links the actual returned bundle/reference after normal scope validation. Assignment context uses the existing authorized assignment_context result, no new query or broader source selection. Include bundle hash/snapshot/receipt only when actually returned; never bundle contents or new read capability. Evidence following retains existing authorization. Retrieval unavailability is explicit, not fabricated success or an unrelated failure.

## Read API and dashboard

GET /api/execution/timeline and /api/execution/timeline/span require exact task/execution/epoch through established scope validation; span detail additionally verifies selected membership. List accepts closed kind, optional verified principal, limit1..100 default25, paired before_time/before_id descending keyset cursor. Reject duplicate/unknown parameters, fixed safe errors and generic404. Response: schema_version, scope, trace_id|null, items[], next_cursor|null, coverage, measurement_availability. Items expose safe source refs, provenance, known parent linkage and explicit missing fields. No route starts work/creates trace/finalizes span/refreshes Knowledge. Native child/token/cost data unavailable, never zero or byte-derived.

Extend current execution refresh lifecycle: group authenticated principals, separate dispatch return/process outcomes, show cancel/timeout/cleanup uncertainty, R1 incident links, authorized semantic/retrieval refs, provenance and unavailable measurements. Preserve paging/focus/disclosures/filters and discard delayed cross-scope responses. No retry/approval/activation controls. Historical comparison only closed counts/measured durations with matching coverage; otherwise show reason instead of misleading percentages.

## Ownership and qualification

Engineer owns focused new reliability trace/process/link/query modules, narrow existing store/query integration, next migration, execution_processes.py, actual process and Knowledge call sites, execution_projection.py, dashboard backend routes, meaningful persistence/fault/identity/privacy/integration tests. Builder follows frozen contracts with frontend, trusted explicit snapshot closure, migration inventory integration, browser evidence and docs. Sequential writers.

Ownership refinement after R1 review: asynchronous frontend request ownership, investigation-window navigation and scope/focus state are correctness-sensitive core work. Engineer owns timeline client logic and minimum page/static wiring, backend, explicit snapshot closure and migration-inventory tests. Builder follows with styling, documentation and stable-candidate verification support. This changes responsibility, not behavior or approval scope. Preserve sequential writers and the existing incident client.

Acceptance:

- R2-01 real owned command links R1 observation to durable launch/start/exit.
- R2-02 nonzero exit, cancel request, timeout and cleanup uncertainty distinct.
- R2-03 restart preserves facts/incomplete outcomes, no replay or false settlement.
- R2-04 concurrent commands cannot exchange parents/principals.
- R2-05 exact epoch/generation fences every runtime publication; no successor mutation.
- R2-06 semantic duplicate idempotency and conflicting-event refusal preserved.
- R2-07 semantic reports distinct from runtime observations and verified acceptance.
- R2-08 actual authorized retrieval references without content/access expansion.
- R2-09 old R1 migration/read compatibility, explicit unavailable historical correlation, read-only GET.
- R2-10 secret canaries/hashes absent from new tables/API/UI.
- R2-11 scoped list/detail/pagination/delayed-response fencing.
- R2-12 unqualified token/cost/native-child observation stays unavailable.
- R2-13 desktop/narrow Chrome function/literal rendering/alignment/overflow/scroll/focus.
- R2-14 R1/process/event/epoch/Knowledge/dashboard regressions.
- R2-15 newly reserved executions receive a stable trace atomically before launch; early failure/cancellation retains it, duplicate reservation preserves it, and rollback creates neither execution nor trace.

Tests drive actual owned ProcessManager launches, real ingestion and HTTP, fault injection around spawn/terminal publication and epoch races. Retain source-bound validation evidence; this plan claims no test outcome.

Reassessment: reconstructing lost ownership, guaranteeing terminal publication across retirement, durable supervision or private native-child inference would introduce a new coupled protocol needing Designer. Those expansions are unnecessary here. Incomplete observations remain truthful.

## Architect correction: trace identity before launch

The actual creator is start_execution() in harness/execution.py, whose outer transaction calls reserve_execution() in harness/execution_store.py. Allocate the stable trace row in reserve_execution's new-execution branch after the initial epoch insertion and before return, within the same transaction. Duplicate reservations retain the original trace and perform no repair mutation. No execution authority field or handoff change is needed. Engineer additionally owns execution_store.py. This preserves the existing creation protocol and STANDARD route. Tests cover launch=False, launch failure, duplicate reservation and rollback. Historical executions remain explicitly unavailable until an authorized current producer publishes; never repair them on GET or mutate old immutable runtimes.
