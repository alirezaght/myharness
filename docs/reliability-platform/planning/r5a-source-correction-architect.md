PLAN_STATUS: REVISED

RESOLUTION

The earlier R5 plan had a real completeness gap: its investigation FK could only reference R1 operation incidents. Non-operation failures need their own canonical incident sources. Do not broaden `operation-dispatch`, fabricate observations, or treat a returned tool call as a failed test.

Use a canonical incident catalog with typed source relations. Preserve R1 tables and incident IDs as the operation-source compatibility layer.

### Revised persistence design

Add:

**`reliability_incident_catalog`**

- `incident_id` primary key.
- `schema_version`.
- `source_kind`: `operation_dispatch|al_test_result|al_review_finding|contract_rejection|budget_decision|operator_verdict|evaluation_result`.
- `task_id`, `execution_id`, `epoch_id`: actual Harness bindings for these sources.
- `category`, `classification_version`, `created_at`.
- Closed sanitized symptom code.
- Provenance: `runtime_observed|validated_ledger_result|validated_review_finding|operator_reported|evaluation_measured`.

Add a separate typed relation for each source family:

- `incident_operation_sources(incident_id, legacy_incident_id UNIQUE)`
- `incident_event_sources(incident_id, execution_id, epoch_id, event_id, item_id, source_revision)`
- `incident_operator_sources(incident_id, operator_request_id UNIQUE, verdict_revision)`
- `incident_evaluation_sources(incident_id, evaluation_result_id, finding_id)`

The event relation has a composite FK to the actual event identity. The current schema already provides `(execution_id,epoch_id,event_id)` in `0014_execution_epochs.sql:24–28`.

Use unique source keys such as:

`(source_kind, execution_id, epoch_id, event_id, item_id)`

These are safe identities, not hashes of arbitrary diagnostics. One review report can produce several incidents, one per stable finding ID.

Investigation heads/revisions reference **the catalog**, not the R1-only incident table.

An adapter must insert catalog and its source relation in the same transaction. Validate exactly one matching source relation before commit; do not expose a generic catalog insertion API.

### R1 compatibility

The additive migration copies existing R1 incident identities into the catalog and adds their operation-source relations. This is an identity-preserving index/projection migration—not new observations or historical diagnoses.

For future R1 incidents, `finish()` inserts the existing R1 row and matching catalog/source rows inside its existing current-epoch transaction.

Preserve:

- Existing incident IDs and observation IDs.
- Original R1 categories and facts.
- Existing API shapes and legacy detail URLs.
- Original observation state and authority checks.

The extended API reads the catalog and dispatches to the typed source projector. Existing operation-specific APIs may retain their old response contract.

### Actual producer adapters

| Source | Capture and validation | Result |
|---|---|---|
| Failed tests | `al_events.prepare_commit()` already emits `test.result` from ledger entries (`al_events.py:75–91`). Harness accepts it through `ingest_event()`. | Canonical test incident from the accepted event’s evidence ID/status/validity. Preserve scope and validity; stale evidence cannot establish a current candidate defect. |
| Review findings | `al_review.cmd_report()` validates and snapshots the report before saving state (`al_review.py:115–136`). Add a closed failure-reference event during that same save. | One incident per stable finding, linked to immutable report, criterion and candidate. Label it a validated review finding, not runtime-observed cause. |
| R4 contract rejection | Emit a closed rejection receipt at R4’s actual validation boundary after producer/execution identity is established. | Invalid-handoff incident containing safe code, contract kind and field pointer; no rejected payload. |
| R3 budget refusal | Emit from the actual persisted budget decision/reservation boundary. | Budget-exhaustion incident referencing budget pool/decision and measured dimension; unsupported measurement is a capability refusal, not fabricated exhaustion. |
| Human-rejected deliverable | Trusted operator POST with actual task/deliverable reference, request ID and verdict. | Operator-reported incident; historical execution is valid context and no epoch writer bypass is required. |
| Evaluation failures | R7’s committed measured result, with exact request/candidate/case binding. | Test/regression/security incidents based on actual result classification. |

Current `test.result` already carries `id,area,status,scope,validity`; it does **not** establish whether a check represents CI, staging or unit tests. Add a closed check-purpose field derived from registered check metadata, not the free-form area name. Existing events lacking that field remain generic verification failures.

Likewise:

- Architecture/specification violations require a finding bound to the relevant criterion/decision.
- Hallucinated APIs, missing context and wrong-tool findings require an attributed report or explicit operator verdict with evidence.
- Repeated unsuccessful edits/excessive retries require actual R2/R3 attempt identities and bounded grouping rules.
- Do not classify all these from `DISPATCH_INTERNAL_ERROR`.

Thus every requested category has a concrete producer route where evidence exists. Unsupported coverage applies only to genuinely absent telemetry or unavailable external CI/staging evidence.

### Atomic event integration

`harness/execution_projection.py:239–317` already commits accepted event, projection and cursor together under current-epoch validation. Add the incident adapter **inside this transaction**, after event insertion and before cursor advance.

Properties:

1. Invalid binding, stale epoch, sequence gap or conflicting duplicate produces no incident.
2. Accepted event and derived incidents commit together.
3. Crash before commit leaves neither; replay can retry.
4. Identical replay returns without duplicates.
5. Several findings from one event are all-or-none.
6. Terminal/retired execution callbacks retain existing refusal behavior.

Do not add a second scanner that writes historical runtime incidents after epoch retirement.

For new review/contract events, add producer and consumer schema support together in both AL distributions. Existing pinned runtimes continue their old event vocabulary. No silent upgrade.

### Artifact integrity and outbox ordering

The producer snapshots the immutable report first, then saves the report reference and closed event in the guarded AL state. An orphaned immutable artifact after failed state save is harmless; an event referring to unpublished bytes is not allowed.

The bridge validates the referenced artifact through the existing trusted AL source and bounded artifact resolver before accepting the event. It projects only safe reference metadata and classification fields into incident storage.

Do not retrieve arbitrary paths or import the report’s raw text into incident facts.

This uses the existing AL outbox plus SQLite inbox transaction; it does not promise exactly-once external execution.

### Standalone AL is a separate provenance domain

`al_events.queue()` does nothing without `integration`; `prepare_commit()` likewise has no Harness outbox in standalone runs. Therefore standalone ledger or review records cannot be presented as Harness events.

For R5:

- Add a public read-only AL incident-report command that derives typed incident records from the selected run’s actual ledger and accepted review artifacts.
- Bind records to real repository identity, AL session/run, task-package revision and immutable source references.
- Use `domain:"standalone_al"` with **no Harness execution/task/epoch binding**.
- Preserve original records and expose coverage explicitly.

Do **not** import these reports into the Harness catalog through an arbitrary upload endpoint or infer a Harness execution from matching repository/task text.

If unified dashboard ingestion of standalone reports is included in R5 implementation, it is a separate **COMPLEX / Designer-required** sub-scope: trusted run selection, immutable export receipt, repository ownership, cross-store replay/conflict handling, source retention and standalone-domain queries must be specified. The initial R5 implementation can deliver standalone incident reporting without pretending that this ingestion exists.

### Routing correction

**Harness-bound R5 remains STANDARD**, because event ingestion already provides the required transaction, identity and replay protocol. The catalog and adapters extend that established pattern.

**Standalone-to-Harness ingestion is COMPLEX if undertaken.** It must not be hidden inside a generic schema extension. No new user decision is needed; route that engineering sub-scope through Designer.

### Ownership additions

Engineer:

- Canonical catalog migration and typed source relations.
- `harness/reliability/incident_sources.py` and source-specific projectors.
- Minimal R1 `finish()` catalog insertion.
- `execution_projection.ingest_event()` transaction hook.
- Both AL event validators/producers and review publication hook.
- Read-only standalone AL incident-report command.

Builder, sequentially:

- Existing R1 migration/API compatibility fixtures.
- Public AL→Harness event integration tests.
- Category producer matrix and catalog query/UI coverage.
- Both-platform parity and packaging checks.

### Revised acceptance criteria

- AC1: Existing R1 incident IDs, observations and API behavior survive migration.
- AC2: An actual failed ledger check creates a canonical incident without any operation observation.
- AC3: Accepted review findings, contract rejections, budget decisions and operator verdicts each create correctly attributed incidents through their real producer.
- AC4: Event/incident/cursor publication is atomic; duplicate/conflicting/stale delivery behaves correctly.
- AC5: Missing or altered report bytes prevent acceptance; no arbitrary path import occurs.
- AC6: Categories derive from supporting evidence, not generic process failure or an area-name heuristic.
- AC7: Historical operator reports use operator authority; stale runtime callbacks remain refused.
- AC8: Standalone reports contain real AL identities and no fabricated Harness binding.
- AC9: A unified standalone ingestion claim requires the separately designed and verified protocol.
- AC10: Investigation, grouping and R6 candidate generation consume the canonical catalog across source types, while retaining facts versus findings versus human-report distinctions.

No implementation or test execution was performed for this correction.
