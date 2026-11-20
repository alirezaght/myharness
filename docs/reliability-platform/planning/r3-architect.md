SPEC_STATUS: SPEC_READY

OUTPUT_ARTIFACT: R3-POLICY-BUDGET-DESIGNER-BRIEF

TASK_CLASSIFICATION: COMPLEX  
IMPLEMENTER: both, sequentially  
DESIGNER_REQUIRED: YES

The new difficulty is atomic admission across overlapping budgets, delegated authority, one-use approval capabilities and execution retirement. Pseudocode must establish the linearization points and crash behavior before implementation. Existing difficult code alone is not the reason for this classification.

No new user decision is needed for these conservative semantics. No global Knowledge gate applies.

### Stable integration seams

- `harness/execution_authority.py:18–70`: current epoch, sealed approval, execution binding and terminal-state restrictions.
- `harness/execution_files.py:25–59`, `:216`: confined paths, descriptor-based access and patch authorization.
- `harness/command_safety.py:49–120`: structured command normalization/classification. Caller-provided `effects` and `target` are descriptions, not authorization evidence.
- `harness/execution_processes.py:17–89`: timeout validation, owner/task/revision-bound process creation and completion hooks; current records are in memory.
- `harness/execution_epoch_lock.py`: short shared claims excluding authority transitions.
- `harness/native_tool_policy.py`: host tool/role restrictions.
- Existing AL retry admission in `al_retry.py:41–70`: matching failure, remaining attempt allowance, justification and reconciliation requirements.

Use the stable R1/R2 interfaces once finalized; do not freeze their current implementation in this design.

### Deliverable

An actual governed operation:

1. Derives its resources and authority from trusted state.
2. Evaluates all applicable policies.
3. Atomically consumes required capability uses and reserves budgets.
4. Executes through existing containment.
5. Records a truthful settlement or unresolved outcome.
6. Shows the policy decision and budget state in the existing dashboard.

Begin with admitted mutating-operation counts, retry-attempt counts and deadlines. Add token/cost enforcement only for providers with qualified bounds and accounting.

### Scopes and precedence

Supported policy scopes:

`global → project → workflow → role → execution`

These are intersecting constraints, not “most specific wins.”

- Every applicable explicit deny wins.
- Allowed tools/resources are the intersection of applicable restrictions and existing authority.
- Limits use all applicable pool constraints; satisfying a narrow limit does not bypass a broader one.
- Required approvals accumulate.
- A child inherits its parent’s restrictions and root budget pools; it may receive narrower limits.
- Missing optional policy configuration preserves existing approved behavior.
- Missing authorization for an authorization-sensitive action denies it.
- Unknown policy versions, malformed rules or unresolved required resource identity fail closed.

A globally configured policy is not automatically a machine-wide aggregate budget. Every limit must declare its accounting domain explicitly: `execution_tree`, `workflow_instance`, or a named shared pool. Project/execution admission must never claim machine-wide enforcement from a per-repository SQLite counter.

Designer must specify one authoritative transactional location for every shared pool. Do not coordinate supposedly atomic counters by independently updating multiple repository databases. Initially unsupported accounting domains must be rejected when configured, not silently downgraded. A later shared-pool implementation can use a small local authority ledger through the existing persistence architecture; it is not a memory platform or a distributed service.

### Trusted operation derivation

Introduce a closed internal `OperationIntent`; agents cannot submit a pre-authorized instance.

Derive:

- Execution, epoch, generation, specification and approval from the current execution envelope.
- Principal/role and delegation ancestry from the authenticated registry.
- Task/revision/owned scope from the admitted assignment.
- File targets from actual parsed patch paths and descriptor-confined resolution.
- Command identity from structured argv and existing classification.
- Network/database/production targets from approved execution profiles or registered capabilities, never a caller’s `effects` claim.
- Mutating classification from the admitted handler and execution profile. A command admitted with write capability consumes the mutation unit even if it happens not to write.

Retain existing path checks at actual use. Policy evaluation cannot replace descriptor-based alias protection or OS containment.

Sensitive command material must not be copied into the policy ledger. Exact approval for sensitive material requires a trusted immutable resource reference whose underlying store already protects it; do not persist hashes of arbitrary secrets merely to bind an approval.

### Closed contracts

**PolicyVersion**

```text
schema_version, policy_id, version, scope_kind, scope_ref,
parent_policy_refs, rules, limits, approval_ref, content_digest,
state, supersedes_ref
```

**OperationIntent**

```text
schema_version, intent_id, execution_id, epoch_id,
authority_generation, principal_ref, task_ref, task_revision,
operation_kind, resource_refs, required_effects,
spec_binding_ref, effective_policy_digest,
capability_refs, requested_limits
```

`required_effects` is server-derived.

**PolicyDecision**

```text
schema_version, decision_id, intent_id, verdict,
closed_reason_codes, matched_rule_refs, policy_digest,
reservation_refs, capability_use_refs, created_at
```

**BudgetPool**

```text
pool_id, dimension, accounting_domain, owner_ref,
limit, unit, policy_version_ref, consumed, reserved,
revision, state
```

**Reservation**

```text
reservation_id, intent_id, pool_id, units,
state, admitted_at, started_at, settled_at,
settlement_ref, observed_units, measurement_status
```

**ApprovalCapability**

```text
capability_id, developer_decision_ref, approval_ref,
execution_id, epoch_id, authority_generation,
principal_or_role_ref, operation_kind, resource_refs,
policy_digest, spec_binding_ref,
maximum_uses, maximum_units, expires_at,
revocation_generation, state
```

No free-form rule execution, arbitrary Python expressions or agent-generated allow decisions.

### Budget units

- `mutating_operations`: **one admitted mutating tool/command invocation**, not filesystem writes, files changed or subprocess syscalls. Multi-file patches count as one invocation unless a separately declared dimension governs targets.
- `retry_attempts`: one admitted retry of an identified failed attempt; inherited AL retry restrictions still apply.
- `execution_elapsed_ms`: elapsed wall-time allowance for the execution tree, with restart-safe accounting semantics.
- `operation_deadline_ms`: upper bound for a particular owned operation, constrained by remaining execution allowance.
- `generation_tokens`: only qualified provider-defined accounting units.
- `monetary_cost`: integer currency subunits under a frozen pricing version; no floating-point money.

Concurrent child time is not silently summed into wall time. If aggregate process-time accounting is later needed, declare it as another dimension.

An admitted mutating invocation consumes its count when it crosses the agreed start boundary. It is not refunded because the operation failed or modified nothing. Proven-not-started attempts may release reservations under the specified protocol.

### Unsupported token/cost enforcement

- Unspecified legacy token/cost limits introduce no invented accounting or new denial.
- Explicit token/cost enforcement requires a qualified conservative pre-admission bound, enforced provider limit where necessary, and adequate settlement observations.
- If these are unavailable, deny with `UNSUPPORTED_ENFORCEMENT_CAPABILITY` before provider execution.
- Public aggregate usage cannot stand in for qualified child accounting.
- Byte counts are not token counts.
- Unknown usage is not zero.
- Provider completion or a timeout does not prove a charge was absent.
- No transcript observer or authentication changes are permitted to “fix” missing measurements.

### Coupled invariants for Designer

1. **Atomic all-or-nothing admission:** all pool reservations, capability-use reservations and the allow decision commit together, or none do.
2. **No oversubscription:** simultaneous siblings cannot reserve beyond any shared ancestor pool.
3. **No authority inflation:** delegation, retries, restart and policy supersession cannot create fresh parent capacity.
4. **Single use:** competing operations cannot spend the same capability use.
5. **Exact binding:** capability authorization is valid only for its operation/resources/spec/policy/execution generation.
6. **Revocation linearization:** revocation prevents admissions after its committed boundary. It cannot claim to undo a side effect already started.
7. **Crash honesty:** absence of a result leaves consumption unresolved; no speculative refund or automatic replay.
8. **Epoch isolation:** retired controllers cannot admit new work or settle successor records. Late settlement requires an explicitly designed trusted reconciliation path, not a stale worker exception.
9. **Preserved AL authority:** policy permission alone never satisfies specification, task, dependency, writer or review gates.
10. **No accidental denial of cleanup:** authenticated polling, cancellation, result collection and essential reconciliation have narrowly defined control-plane allowances; exhausted work budgets must not make owned work impossible to stop.
11. **Bounded audit:** denied requests cannot cause unbounded persisted payloads or leak untrusted identity/resource text.
12. **Truthful enforcement:** configured dimensions lacking real enforcement support fail closed rather than degrading to advisory mode.

### Required protocol pseudocode

Designer must supply executable-shape pseudocode for:

- Effective-policy selection and validation.
- Admission transaction and lock ordering with epoch transitions.
- Reservation state machine: `reserved → started → settled`, plus `released_proven_not_started` and `unresolved`.
- Capability issuance, use reservation, expiry and revocation.
- Parent/child pool inheritance and delegation admission.
- Policy replacement while operations are in flight.
- Process launch before/after crash windows.
- Settlement racing cancellation, epoch retirement and revocation.
- Safe handling of unavailable terminal accounting.
- Restart reconciliation using current trusted ownership/closure evidence.
- Deadline calculation and termination/result-collection behavior.

Do not simply hold a SQLite transaction or epoch lock throughout an external command. Identify the exact point after which capacity cannot safely be returned without proof.

### Deadlines and persistent process linkage

Extend the existing owned process path with a durable attempt/reservation reference before launch. Retain the process manager’s owner/task/revision checks.

- Effective operation timeout is the minimum of requested timeout, policy maximum and remaining execution allowance.
- Persist start/deadline references; use monotonic time while the supervising process remains alive.
- Specify restart handling and clock anomalies explicitly. Do not reconstruct elapsed time from untrusted agent timestamps.
- Termination must use existing process ownership and settlement mechanisms.
- Timeout signals are attempts to stop; they are not positive closure receipts.
- Process output collection must remain possible after work-budget exhaustion.
- Unknown processes/reservations remain visible and block unsafe refund/reuse.

### Component ownership

Engineer:

- New focused `harness/policy/` modules for contracts, composition, resource derivation, capabilities, reservations and reconciliation.
- Next available numbered migration for immutable policy versions, decisions, pools, reservations and capability uses.
- Narrow integrations in execution authority/dispatch, command classification, file authorization, process manager and delegation admission.
- AL retry integration where both existing retry limits and shared budgets must apply.
- Race/crash/security/integration tests.

Builder, after contracts stabilize:

- Existing dashboard policy decision/budget/capability views.
- Closed configuration validation and documentation.
- Fixtures, browser checks and source-backed verification evidence.

Shared interfaces and authoritative state require sequential writers. No separate policy service is proposed.

### Acceptance scenarios

- Two concurrent mutating attempts competing for the last unit: exactly one admitted.
- Child and parent compete for the same inherited capacity without exceeding it.
- Forged `effects`, target, role, epoch and capability resource fail.
- Capability replay, expiry and revocation races preserve exact use limits.
- Crash before launch releases only when independently proven not started.
- Crash after possible effect remains unresolved and consumes conservative capacity.
- Cancellation and result collection remain usable after budget exhaustion.
- Unknown token/cost capability rejects only explicitly configured enforcement.
- An ordinary legacy run without those caps preserves behavior.
- Actual subprocess paths cannot escape declared filesystem/network constraints.
- UI shows measured, reserved, consumed and unknown quantities distinctly.
- Existing AL approvals, retry caps, donor/observer refusals and clean-review restrictions remain unchanged.

The first R3 delivery must exercise this complete admission-to-settlement-to-dashboard path. Additional supported budget dimensions can follow under the same reviewed protocol, rather than shipping a policy registry with no enforced consumer.
