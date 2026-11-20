# R3 centralized policy and shared budgets

Actual AL-Architect brief: R3-POLICY-BUDGET-DESIGNER-BRIEF. COMPLEX, Engineer then Builder sequentially, Designer required. The new difficulty is atomic admission across overlapping budget pools, delegated authority, one-use approval capabilities and execution retirement. All original mission phases remain authorized; Knowledge acceptance is not a blanket gate.

Build a real governed operation: trusted intent -> compose applicable policies -> atomically consume capabilities/reserve budgets -> existing contained execution -> truthful settlement -> existing dashboard policy/budget view. No separate service or memory store.

Existing seams: execution_authority.py:18-70 current epoch/spec/approval; execution_files.py:25-59,216 confined descriptors; command_safety.py:49-120 structured normalization (caller effects/target are not authority); execution_processes.py:17-89 owner/task/revision process hooks; execution_epoch_lock.py shared claims; native_tool_policy host restrictions; AL al_retry.py existing failure/reason/reconciliation/retry caps. R1/R2 observation APIs are finalized by their own preceding writers; don't freeze moving internals or alter their authority.

Policy scopes global/project/workflow/role/execution intersect. Explicit deny wins; allowed resources/tools intersect existing authority; required approvals accumulate; all applicable pool constraints apply. A child inherits root restrictions and pools and may only narrow. Absent optional configuration preserves legacy behavior; absent required authorization denies. Unknown policy versions/malformed rules/unresolved required resource identity fail closed.

Every limit declares accounting_domain execution_tree/workflow_instance/named shared pool. Global policy scope does not magically create machine-wide counters from a per-project DB. Designer must choose one transactional authority ledger for each shared pool; never claim atomicity across independently updated SQLite databases. Reject unsupported configured domains explicitly. A small local ledger through existing persistence can support real shared pools; no distributed service.

Internal OperationIntent cannot be agent-submitted as preauthorized. Derive execution/epoch/generation/spec/approval from trusted envelope; principal/role/ancestry from authenticated registry; task/revision/owned paths from admitted assignment; patch targets from parsed confined descriptors; commands from structured argv classification; network/DB/production targets from approved profiles/capabilities, never caller effects. Mutating classification comes from admitted handler/profile; write-capable command consumes one mutation operation even if it writes nothing. Keep path checks at use and OS containment. No sensitive commands/raw secrets or their hashes in policy ledger; exact sensitive approvals reference protected immutable resources.

Closed versioned contracts:
- PolicyVersion: schema_version, policy_id, version, scope_kind, scope_ref, parent_policy_refs, rules, limits, approval_ref, content_digest, state, supersedes_ref.
- OperationIntent: schema_version, intent_id, execution_id, epoch_id, authority_generation, principal_ref, task_ref, task_revision, operation_kind, resource_refs, server-derived required_effects, spec_binding_ref, effective_policy_digest, capability_refs, requested_limits.
- PolicyDecision: schema_version, decision_id, intent_id, verdict, closed_reason_codes, matched_rule_refs, policy_digest, reservation_refs, capability_use_refs, created_at.
- BudgetPool: pool_id, dimension, accounting_domain, owner_ref, limit, unit, policy_version_ref, consumed, reserved, revision, state.
- Reservation: reservation_id, intent_id, pool_id, units, state, admitted_at, started_at, settled_at, settlement_ref, observed_units, measurement_status.
- ApprovalCapability: capability_id, developer_decision_ref, approval_ref, execution_id, epoch_id, authority_generation, principal_or_role_ref, operation_kind, resource_refs, policy_digest, spec_binding_ref, maximum_uses, maximum_units, expires_at, revocation_generation, state.
No free-form executable rules/Python expressions or agent-generated allow decisions.

Dimensions:
- mutating_operations: one admitted mutating tool/command invocation, not files changed, filesystem writes or subprocess syscall count; a multifile patch is one unless an explicit other dimension governs targets.
- retry_attempts: one admitted retry of identified failed attempt, retaining AL restrictions.
- execution_elapsed_ms: execution-tree wall allowance, not sum of concurrent child process time.
- operation_deadline_ms: min requested, policy max and remaining execution allowance.
- generation_tokens: only qualified provider-defined units.
- monetary_cost: integer currency subunits under frozen pricing version, no float money.
Mutation count consumed at agreed start boundary, no refund because failure or no write. Proven-not-started can release only under defined proof.

Unspecified token/cost caps keep usage unavailable with no invented accounting or new denial. Explicit enforcement requires qualified conservative pre-admission bound, enforced provider limit where needed and adequate settlement. Otherwise reject UNSUPPORTED_ENFORCEMENT_CAPABILITY before provider execution. Public aggregate usage is not child accounting; bytes not tokens; unknown not zero; exit/timeout not proof no charge. Preserve observer refusals/authentication.

Designer invariants:
1 all-or-nothing transaction for every pool reservation, capability use and allow decision;
2 no sibling/parent pool oversubscription;
3 delegation/retries/restart/policy replacement do not reset capacity;
4 capability use cannot be double spent;
5 exact resources/operation/spec/policy/epoch binding;
6 committed revocation blocks later admission, never claims reversal of started effects;
7 uncertain execution keeps unresolved consumption, no speculative refund/replay;
8 retired workers cannot admit/settle successor state; no historical-writer exception;
9 policy permission never substitutes AL spec/task/dependency/writer/review gates;
10 budget exhaustion must not block authenticated owned cancel/poll/result collection/essential reconciliation;
11 bounded closed audit of denies, no untrusted payloads/resource text;
12 unsupported required enforcement rejects instead of advisory downgrade.

Provide executable-shape pseudocode for composition/validation; lock ordering and admission transaction; reserved->started->settled/released_proven_not_started/unresolved; capability issuance/use/expiry/revocation; inherited pools/delegation; replacement in flight; launch crash windows; cancel/settlement/retirement races; unavailable accounting; restart using trusted closure proof; deadline and result collection. Never hold DB or epoch lock across external command. Name the exact irreversible-start boundary.

Process linkage: persist durable attempt/reservation reference before launch; maintain owner/task/revision checks. Record trusted deadline/start, monotonic while supervisor alive, explicit restart and clock-anomaly semantics. Termination uses existing ownership and settlement; a signal is not closure. Unknown reservations/processes stay visible and cannot fund unsafe refund/reuse.

Engineer owns harness/policy focused contracts/composition/resource/capability/reservation/reconciliation, next migration, narrow authority/dispatch/file/command/process/delegation and ALretry integrations, race/crash/security tests. Builder follows frozen contracts with existing dashboard/config/docs/browser verification. Sequential writers only.

Acceptance: two concurrent last-unit attempts exactly one allow; parent-child same pool no overflow; forged effects/target/role/epoch/resource deny; use/expiry/revocation races; proven-not-started release only; possible effect unresolved consumes conservative capacity; cancel/poll collect after exhaustion; unsupported explicit token/cost only denies configured cases; uncapped legacy compatible; real subprocess path containment preserved; UI separates measured/reserved/consumed/unknown; existing AL/observer/review boundaries unchanged. Deliver admission->settlement->dashboard as working vertical slice, not disconnected policy registry.
