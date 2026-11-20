# Typed agent exchange contracts

Host capture of actual AL-Architect R4 plan from `/root/architecture_audit`. SPEC_READY, STANDARD, Engineer then Builder sequentially. No Designer: existing immutable artifacts, validation, admission and writer receipt protocols suffice; metadata introduces no authority. This is future implementation guidance, not implemented functionality or passing evidence. Full original mission remains authoritative; no blanket Knowledge gate.

Implement all ten requested families at actual exchange boundaries: requirement analysis, architecture proposal, design decision, implementation plan, agent assignment, implementation result, verification result, failure report, escalation request, final delivery. Reference existing authoritative typed payloads and specialized validators rather than inventing a universal execution API. Internal reasoning, Markdown narrative and general supporting artifact storage remain free-form. Storage alone does not make an output eligible for orchestration consumption.

## Concrete producers and consumers

Requirement analysis: Harness interview summary/checkpoints and AL accepted task package. Architecture: ArchitectureDesigner.propose with actual proposal/coverage/alternatives/rationale/decision requirements. Decisions: DecisionWorkflow and InterviewEngine recorded verdict/version/Registry provenance; an agent's approved assertion cannot substitute. Plans: AL planning report/admission adapters preserve existing schemas and bindings. Assignment: AL writer_handoff generation plus Harness assignment_handoff._selected authenticated pagination. Results: execution_artifacts and AL artifact CLI explicit contract submission. Verification: actual evidence ledger/review/planning proof consumers. Failure: operational issue interface and execution_contract failure validation. Escalation: existing typed business blocker interface, keeping operational issues distinct. Delivery: actual delivery projection/completion bindings, authored work/proof/limitations/approval state.

Both `al/codex/skills/al/scripts` and Claude counterparts must remain equivalent.

## Closed common envelope

Add a small AL-owned validator/adapter module and schema plus Harness adapter; no new JSON Schema runtime dependency. Version exactly integer1, ten discriminated family kinds. Envelope:

- context: actual AL run/session, Harness task/execution where applicable, subtask, graph revision, producer, current epoch/generation where applicable; absence has explicit applicability/reason.
- source_specification: immutable package/document reference and effective amendment/version binding.
- required_inputs: typed exact references, purpose, required/current semantics.
- expected_outputs: named kind/scope, never promises of success.
- acceptance_criteria: actual registry IDs and source binding.
- evidence_refs: immutable evidence/report refs, empty only with reason.
- constraints: references or bounded explicit constraints.
- status: family-specific closed values.
- failure: null or closed classification, immutable diagnostic reference, bounded repair guidance.
- payload: family-specific fields or reference to existing authoritative typed payload.

Use bounded JSON parsing and existing artifact reference formats. Standalone execution identity is the actual AL run; additionally populate Harness execution when bound. Pre-execution requirement/design records explicitly say not_started, never fabricate an execution.

## Enforce actual exchange boundaries

Writer handoff generation publishes a typed assignment receipt alongside existing generated prompt/body/managed evidence block. Derive trusted context, required inputs, expected outputs, criteria and ownership from actual current state. Validate referenced bytes before saving receipt. Harness assignment_handoff._selected validates receipt in addition to existing writer/phase/graph/plan/presentation checks; include receipt identity in paginated resource binding, rejecting superseded assignments. Never infer a complete approved plan from Markdown headings.

Add explicit typed submission to existing AL artifact and Harness submit_output surfaces; do not guess kind from filename. Validate syntax/family/context/authenticated producer before publication. Generic supporting artifacts remain non-contract storage. require_authored_report and require_dependency_outputs enforce declared output contracts for newly typed assignments while retaining exact byte/provenance/current candidate/producer checks. Authored work may remain consumable without verified completion where existing policy allows it.

Adapters for all ten families check references/context, then invoke specialized planning/review/decision/completion/evidence validators. Keep original schemas authoritative. Existing integration handoff `{from,to,text}` gains an explicit typed-reference form for new exchanges; legacy text remains historical semantic information. Neither form admits a writer by itself.

New assignments declare contract requirements. Old receipts without declarations keep existing behavior and explicit legacy coverage; no synthesized historical contracts/success or silent pin upgrade. Unknown required versions fail with supported-version repair guidance; viewing old artifacts succeeds.

Errors are bounded objects: code, contract_kind, schema_version, field_pointer, expected, repair_action, state_changed:false. Fixed descriptions for wrong producer, stale spec, missing approval, unresolved/tampered artifacts and unsupported versions; never echo sensitive rejected values.

## Invariants

Valid metadata grants no approval/capability/completion/verification. Preserve producer identity, epoch, graph/specification/dependency/evidence/approval checks. Required references resolve exact immutable current bytes; names/headings/claimed status are insufficient. Validation failure leaves indexes/readiness/admission unchanged. Historical bytes/pins unchanged; no retroactive typed-success. Errors are actionable without secret echoes. Preserve current authored versus verified/completed distinctions.

## Ownership and checks

Engineer: both AL distributions' new agent-contract/family adapters/schema, writer_handoff, artifact_cli, execution_tasks; Harness agent_contracts, assignment_handoff, execution_artifacts, execution_authority and minimal operations/MCP wiring; specialized producers only for explicit adapter calls. Builder follows stable interfaces with ten-family/legacy/public CLI/Harness integration fixtures, parity/manifests/docs and release verification. No early parallel work: assignment/submission contracts and packaging are shared.

No database migration, subscription, provider activation or service. CI source is ci.yml locked sync/Ruff lint+format/nonbrowser pytest/build Linux+macOS; AL release workflow requires actual managed prerequisites/profile, not count-only local substitution. No explicit timeout-minutes in inspected jobs. Existing pins stay immutable; qualification/parity work never installs or upgrades other runs automatically.

Acceptance:

- R4-01 all ten families have validated records consumed at real orchestration boundaries.
- R4-02 required common fields present or legitimately inapplicable for actual lifecycle.
- R4-03 actual missing approvals/decisions/inputs prevent affected downstream execution.
- R4-04 invalid/stale contracts produce sanitized actionable errors without accepted-state mutation.
- R4-05 metadata cannot forge approval, verification, completion or producer identity.
- R4-06 exact current immutable references; altered/missing bytes refused.
- R4-07 history readable, pins unchanged, no fabricated conversion/success.
- R4-08 current authored dependency consumption preserved where allowed.
- R4-09 both platforms and Harness public integration/compatibility verified.
- R4-10 only declared exchange boundaries structured, internal reasoning/supporting material unrestricted.

Tests must exercise public handoff -> authenticated paginated assignment -> typed output -> dependency consumption; ten-family actual adapter matrix; stale graph/spec/wrong producer/missing or tampered refs with no downstream start/readiness mutation; allowed authored-unverified dependencies; legacy and unsupported versions; specialized underlying validators refusing forged payloads inside valid envelopes; secret-canary errors; platform parity. Refresh actual source hashes before classification/admission. Register exact recipes/scopes before writer handoff. Proposed focused files include tests/test_agent_contracts.py and test_artifact_pagination.py plus both platforms' agent-contract, writer-handoff and artifact-consumer suites. Broad CI and release obligations stay separate; this plan claims no executed tests.
