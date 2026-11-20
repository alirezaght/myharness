# R4 typed agent exchange contract

R4 adds typed companion metadata at existing AL and Harness exchange boundaries. It does not create a new service, general execution API or authority channel. The exchange envelope records what an existing authoritative producer created and binds it to context and immutable references. Existing approval, producer, revision, dependency, review, planning, evidence, completion and publication checks remain authoritative.

## Public exchange surfaces

- Standalone AL creates pre-execution companions for requirement-analysis, architecture-proposal and design-decision records from their actual task and decision state. These use `pre-execution` applicability for fields that do not exist yet; they do not invent an execution or agent identity.
- `al writer handoff` creates a task-assignment companion in the authenticated writer receipt alongside the existing prompt and evidence block. Harness `read_file` for the authenticated `harness:assignment-handoff` descriptor/pages validates and returns the selected immutable prompt buffer with its typed receipt.
- AL `al artifact output --name result.md --file result.md --contract-file contract.json --writer WRITER_ID` publishes an explicitly declared implementation-result companion. The Harness `submit_output` operation accepts the optional `contract` object. A generic supporting artifact without that field remains a legacy/free-form artifact; generic JSON is not automatically a contract.
- Typed `integration handoff` may reference an implementation-result artifact through `contract_ref`. Existing free-form `text` handoffs remain readable under their previous behavior.
- Existing interview, design, decision, planning, issue/blocker, evidence and publication seams produce or consume the remaining families through their actual adapters and specialized validators.

The versioned companion is stored with the existing artifact/receipt context. There is no separate HTTP endpoint for creating arbitrary contracts and no generic contract import route.

## Version 1 envelope

The object has exactly these top-level keys:

```json
{
  "schema_version": 1,
  "kind": "implementation-result",
  "context": {},
  "source_specification": {"reference": {}, "amendments": []},
  "required_inputs": [],
  "expected_outputs": [],
  "acceptance_criteria": [],
  "evidence_refs": [],
  "constraints": [],
  "status": "authored",
  "failure": null,
  "payload": {"record_type": "implementation-result", "record_id": "…", "record_digest": "…"},
  "field_applicability": {}
}
```

References are exact `{ "kind", "id", "sha256" }` objects. The digest binds the bytes actually resolved from the AL issuing root or Harness execution-artifact index selected by the server. Required inputs, evidence references and constraints carry a reference, a bounded purpose string and a boolean `required`. Expected outputs carry a bounded name and one recognized family kind. `payload.record_digest` fingerprints the authoritative record; it is not a hash of arbitrary source content used as a substitute for checking it.

`context` has the closed fields `al_run`, `al_session`, `task`, `execution`, `subtask`, `graph_revision`, `task_revision`, `producer`, `epoch`, `authority_generation` and `applicability`. Values come from the actual source lifecycle. A null field must have an applicability reason such as `standalone`, `pre-execution`, `not-assigned`, `not-observed` or `multiple-producers`. Empty reference/criteria/output collections have explicit `not-declared` or `not-observed` applicability. The envelope does not create missing context.

## Ten families and their current authoritative seams

| Family | Current producer and authoritative consumer | Closed status values |
| --- | --- | --- |
| `requirement-analysis` | `harness/interview.py` checkpoints/summaries and accepted AL task package; consumed against actual effective amendment lineage and producer state by pre-execution/requirement consumers. | `observed`, `incomplete` |
| `architecture-proposal` | `ArchitectureDesigner.propose()` in `harness/design.py`; consumed by the existing design/CLI review flow with its proposal and coverage state. | `proposed`, `observed` |
| `design-decision` | `DecisionWorkflow` and recorded interview decision registry; consumers recheck the actual version, verdict and registry provenance. | `recorded` |
| `implementation-plan` | Existing AL planning report/admission; `validate_admitted` still performs specialized planning and obligation checks. | `authored` |
| `task-assignment` | Both AL `al_writer_handoff.py` producers; Harness `assignment_handoff._selected()` validates the authenticated receipt before bounded pagination. | `assigned` |
| `implementation-result` | AL `al_artifact_cli.py` and Harness artifact submission; consumed by `agent_results.require_typed_result`, authored/dependency publication and typed `contract_ref` handoff. | `authored`, `failed` |
| `verification-result` | Existing terminal evidence ledger; `entry_verdict` and the existing log, candidate, prerequisite and specialized review validators remain decisive. | `observed` |
| `failure-report` | Existing `al operational-issues` diagnostic record; subsequent issue updates consume the exact prior companion. | `observed`, `resolved` |
| `escalation-request` | Existing integration blocker creation; duplicate blocker and task-admission consumers remain authoritative. | `requested`, `resolved` |
| `final-delivery` | Actual `publication_readiness` projection; AL/Harness publication validators retain their existing projection guards. | `observed` |

The E90 implementation-plan graph-context failure is historical and repaired in the current source: planning publication retains a `contract_observation` snapshot, and `consume_plan` authenticates that exact companion and permits only a captured graph revision no later than the current revision while rechecking current specification lineage. The exact E90 attribution correction and repaired-source evidence are recorded in [implementation-progress.md](implementation-progress.md).

The adapters are split across `harness/agent_contracts.py`, `agent_preexecution.py`, `agent_exchange.py`, `agent_results.py`, `execution_publication_contract.py`, existing authoritative Harness producers/consumers, and mirrored standalone AL modules in both `al/codex/skills/al/scripts` and `al/claude/skills/al/scripts`. Supporting artifacts and private reasoning remain free-form.

## Validation and refusal

The current stdlib validator rejects additional or missing fields, unknown versions/families/statuses, malformed references, mismatched context/specification, inconsistent applicability, invalid family record digests and unresolved/tampered required bytes. The public error is bounded to a fixed code, family or null, schema version, field pointer, expected shape, repair action and `state_changed: false`; rejected payload values, secrets and arbitrary names are not echoed.

Failed validation does not publish a new accepted typed artifact or make a task ready. Where dispatch has already crossed its existing start boundary, ordinary attempt accounting, consumed reservations and settlement remain in effect; typed rejection is not an accounting rollback. An unknown required contract version refuses affected consumption, while historical artifacts without a declaration retain legacy behavior and are not rewritten or retroactively called typed.

Contract validity never supplies a developer approval, capability, producer identity, verification verdict, task completion, release or publication acceptance. The design-decision companion cannot approve a decision; a verification envelope cannot replace actual ledger evidence; a failure payload cannot create a developer blocker by assertion; a final-delivery envelope cannot replace publication readiness.

## Immutable exchange and mutable source are different guarantees

Assignment presentation and immutable typed artifact reads validate and return the same exact cached buffer from the authenticated server-selected root/index. Their coverage can report `immutable_snapshot_validated` and `mutable_source_not_checked`; this permits useful assignment presentation even when an old handoff has an unresolved legacy admission. It does not claim current mutable repository files are unchanged or that a process tree is settled.

Where an operation actually consumes mutable dependency source, it must use the existing R3 attempt and connection. The server derives `mutable_dependency` scope from authenticated dependency receipts; atomically checks legacy and native launch predecessors; reserves claims before source hashes; validates descriptor identity/digest; retains claims through use; and revalidates authority/receipt at start. Only that path may report `coordinated_source_consumption`. A source safety refusal preserves unknown liabilities. `legacy_uncoordinated` identifies uncovered historical execution and is not a pass. Coverage labels are factual metadata only and never grant authority.

R4 does not qualify general writer start, task-agent/task-authored lifecycle commands or the outer native process. Existing typed metadata does not create a no-start receipt. An additive `execution_attempts` migration leaves prior launch state unknown; null PID, a status label, parent exit or group disappearance do not prove no launch. Strict source use refuses unresolved legacy, pending-native or unsupported old-bridge conditions. Exact-buffer immutable exchange can remain available independently.

## Automatic setup is a separate effect dispatch

The real AL registration integration can enqueue dependency setup through `auto_prepare()`. It derives bounded repository-relative units from task metadata and validates the concrete request without checking dependency outputs. The public outcome is `Queued`; it means only that setup is pending. This path does not read target dependency manifests or run an installer while the outer `al_command` Attempt is active.

`OperationController.tick()` resumes queued work only when there is no ambient Attempt. For a retained policy-bound setup it revalidates and restores that exact setup owner. For fresh work it invokes the normal `environment_operation` dispatch, independent of optional budget-policy presence. `prepare()` verifies that the current Attempt owns the exact execution, principal, task and revision before reading setup inputs. No unrelated outer Attempt is transferred, nested or relabeled. Polling and cancellation do not prove setup or legacy process liabilities closed; unsupported native/process-tree behavior remains outside the qualification.

The regression history distinguishes the production defect from fixture repairs: E51 is the causal red for missing owning dispatch; E56 is a root-task-scope fixture refusal; E61 is a test postcondition failure after the safety refusal. E59/E60 passed functionally but became stale after a later test-only correction. Current post-correction evidence is listed in [implementation-progress.md](implementation-progress.md); none of these results grants generic installer or native-process qualification.

## R4 review repair behavior

When a terminal check has been interrupted, `check-reconcile` authenticates the exact stored entry and its existing verification companion before it records reconciliation. It preserves that original observation and companion, stores a versioned reconciliation record with the bounded proof, and validates the resulting state before producing a new verification result. A status change alone is insufficient; generic save logic does not regenerate a missing or mismatched companion. The recorded outcome remains `INFRA` when the original check did not complete.

An operational issue update validates the previous issue payload and companion against their historical context, then separately validates current task authority and scope before writing the next revision. The previous companion remains in history and the new companion binds the new revision and current context. A later graph revision does not rewrite historical provenance.

Both standalone AL artifact-output CLIs compare submitted acceptance criteria with the actual task criteria or accepted package before snapshotting, indexing, or returning an idempotent result. An invented, empty, or mismatched criterion set is refused before publication. The supported invocation is `al artifact output --name result.md --file result.md --contract-file contract.json --writer WRITER_ID`; the contract must bind the current writer assignment, its actual criteria, and the exact result content.

## Escalation history and authenticated decision bytes

A new blocker registration records its exact escalation row and version-1 companion in an `escalation-observation-v1` snapshot. Authorized resolution or scoped revalidation appends an observation with its transition reference. Consumption validates the original registration, each allowed transition and its durable delivery acknowledgement or revalidation artifact, then requires the live blocker and companion to match the newest observation. The history is bounded to the original observation, a decision and an optional revalidation. Captured graph revision may be historical within current graph growth; accepted specification lineage and current authority checks still apply. Missing or altered observation inputs refuse. Legacy records without observation history keep their legacy handling; no provenance is synthesized.

For `operation="decision"`, the trusted Harness authority bridge returns the closed decision-only receipt `delivery_receipt: {"schema_version": 1, "sha256": "<64 lowercase hex>"}`. It derives the digest from the trusted persisted delivery bytes after checking that the fixed inbox contains those same bytes; Registry provenance is checked separately. The AL consumer validates the reply shape and uses one bounded, no-follow inbox read: its raw SHA-256 must match the receipt, and the parsed delivery must equal the submitted object. The consumer then snapshots those same authenticated bytes. The ordinary nondecision authority response shape is unchanged. This establishes the tested local bridge and file path; it does not identify or authenticate a named human, prove arbitrary native filesystem behavior, or qualify hosted/provider execution.

## Evidence and support boundaries

The focused and compatibility evidence is recorded in [implementation-progress.md](implementation-progress.md). Both vendored AL manifests and source copies are covered by parity and isolated-source checks. Harness trusted snapshots list the new contract/exchange modules. The formatter excludes only captured planning Markdown from format input; production Python lint and formatter coverage remain enabled.

These checks establish only the recorded local paths and source state. Hosted CI, complete service/check discovery, formal AL review/approval, broader native/provider boundaries and full-mission completion remain separate obligations. R5a remains a future plan; it is not implemented by this contract.

`WRITER_ID` is the current active writer. `contract.json` must bind the actual current assignment, its acceptance criteria and the exact content of `result.md`.
