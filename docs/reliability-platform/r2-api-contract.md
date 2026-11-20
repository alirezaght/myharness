# R2 execution timeline HTTP contract

The dashboard exposes a read-only execution timeline over `GET /api/execution/timeline` and `GET /api/execution/timeline/span`. Both requests require an exact task, execution, and epoch scope. `task` is the task ID; `execution` is the execution ID; `epoch` is the selected runtime epoch ID. They are separate required values. The selected execution must belong to the task, and the epoch must be recorded for that execution (with the existing selected `legacy-0` exception). Detail lookup additionally requires a process span in that exact execution and epoch.

The server opens SQLite in read-only mode and enables `query_only`; GET does not create traces, backfill facts, finalize spans, migrate the database, or issue Knowledge reads.

## List

```http
GET /api/execution/timeline?task=<task-id>&execution=<execution-id>&epoch=<epoch-id>
```

Optional query fields:

| Field | Contract |
| --- | --- |
| `kind` | One closed event kind: `operation.started`, `operation.returned`, `operation.denied`, `operation.failed`; `process.launch_pending`, `process.started`, `process.launch_failed`, `process.cancel_requested`, `process.timeout_observed`, `process.exited`, `process.cleanup_unconfirmed`; `semantic.agent`, `semantic.task`, `semantic.check`, `semantic.decision`, `semantic.output`, `semantic.run`; or `retrieval.bundle`. Empty means all kinds. |
| `principal` | Optional authenticated principal ID, 1–128 characters matching `[A-Za-z0-9_.:-]+`; the principal must be present in the selected execution/epoch or the request is out of scope. |
| `limit` | Decimal integer 1–100; default 25. |
| `before_time`, `before_id` | Both or neither. `before_time` is an ISO timestamp with a timezone, at most 64 characters. `before_id` matches `[a-z0-9:]{1,128}`. Together they are the descending keyset cursor returned by the previous page. |

Unknown parameters, duplicate parameters, malformed query strings, invalid values, and unpaired cursor fields are rejected. The cursor predicate is `(at,item_id) < (before_time,before_id)` with descending `(at,item_id)` order. The server reads one extra row to determine whether `next_cursor` is present.

The JSON envelope contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | Integer `1`. |
| `scope` | `{ "task": ..., "execution": ..., "epoch": ... }` for the validated request. |
| `trace_id` | Stable execution trace ID, or `null` when historical correlation is unavailable. |
| `correlation_status` | `available` or `historical_correlation_unavailable`. |
| `items` | Descending page of safe event metadata. |
| `next_cursor` | `{ "before_time": <at>, "before_id": <item_id> }`, or `null` when no further page exists. |
| `coverage` | Versioned booleans for observed source families and known gaps. |
| `measurement_availability` | `duration_ms: "monotonic_when_observed"`; `tokens`, `cost`, and `native_children`: `"unavailable"`. |
| `comparison` | Currently `{ "available": false, "reason": "no_qualified_comparison" }`. |

Each item has `item_id`, `at`, `event_kind`, `provenance`, `source_kind`, and `source_id`. The safe projection may also include `principal_id`, `role`, `parent_observation_id`, `parent_span_id`, `observation_id`, `span_id`, `incident_id`, `duration_ms`, `returncode`, `stop_reason`, `cleanup_status`, `reference_id`, `bundle_hash`, `snapshot_id`, `receipt_id`, `tool`, and `code`; inapplicable values are JSON `null`. `missing_fields` names absent `principal_id`, parent observation/span, and duration fields, and always includes `tokens`, `cost`, and `native_children` because those measurements are not collected by this contract.

## Process detail

```http
GET /api/execution/timeline/span?task=<task-id>&execution=<execution-id>&epoch=<epoch-id>&span=<span-id>
```

Detail accepts only the four required query fields; list filters and cursors are invalid here. A successful response has the common envelope, `span` (the safe process-span record with `missing_fields`) and up to ten linked `events`, ordered by recorded time and link ID. Missing `duration_ms`, `returncode`, or `finished_at` are identified in the span's `missing_fields`; `tokens`, `cost`, and `native_children` are always listed as unavailable. Process lifecycle fields distinguish launch state, terminal observation, return code, stop reason, and cleanup status. A cancellation request is not a terminal state, and a return code does not assert cleanup or acceptance.

## Errors and nulls

| HTTP status | Fixed response | Meaning |
| --- | --- | --- |
| `400` | `{"error":"Invalid timeline query"}` | Missing, duplicate, unknown, or malformed query fields. |
| `404` | `{"error":"Timeline scope not found"}` | Task/execution/epoch mismatch, unknown selected principal, or span outside the selected scope. |
| `503` | `{"error":"Timeline storage unavailable"}` | SQLite or filesystem read failure. |

The endpoint does not expose storage exception text. JSON `null` means a field is not present for that item; `missing_fields` explains selected optional measurement gaps. `"unavailable"` means the measurement class is not collected. Neither representation means a measured zero. No comparison ratios are produced without qualified matching coverage.
