## R3 correction: unresolved resource and producer fence

The inspected `resources.prepare()` returns early when policies are absent or inapplicable. `release_for_replacement()` and `require_dependency_outputs()` do not inspect durable policy liabilities. Therefore a budget-only guard cannot close this gap.

### Durable bindings

Add a small resource-claim relation associated with each governed attempt:

```text
attempt_id
authoritative_repository_ref
resource_ref
access = read_dependency | write | external_effect | unknown
producer_execution_id / producer_task_id / producer_revision
producer_principal_ref / producer_writer_ref, when actually established
state = reserved | active | unresolved | closed
```

Derive these fields from admitted assignments, writer records, actual output receipts, parsed patch targets and the effective isolation profile. Resource references resolve through trusted internal metadata; store no raw argv, reasons or secret-derived hashes.

Use the actual writable profile scope for arbitrary commands. `control:al`, `control:environment` and similar operation labels are **not sufficient conflict scopes**. Resolve their actual filesystem/control/external resources; unresolved scope becomes `unknown`.

Persist task/producer provenance at admission. Do not recompute historical ownership from a later assignment.

### Overlap rules

```text
same authoritative resource namespace:
  write vs write/read_dependency => conflict when paths overlap
  read_dependency vs read_dependency => compatible
  external effects => use qualified exact target semantics
  unknown => conflict throughout its unresolved namespace

path overlap:
  "." or exact match or ancestor/descendant
```

A known filesystem scope does not prove absence of other effects. If the handler cannot bound an effect’s namespace, treat it as overlapping the entire supported ledger domain.

Query all relevant executions in the **same authoritative database**, not merely the current execution/role/tree. Canonical aliases must resolve to the same resource identity or be refused. Do not claim coordination with independent databases or uninstrumented external actors.

### Atomic admission and start

Add a connection-taking helper, proposed:

```python
require_resource_clear(conn, claims, *, own_attempt_id=None)
reserve_resource_claims(conn, attempt_id, claims)
```

Execute within the existing short epoch-fenced `BEGIN IMMEDIATE` transaction:

```text
validate current authority/assignment/revision
derive or revalidate exact resource and dependency claims

check all conflicting reserved/active/unresolved claims
also treat unclosed process boundaries or unresolved liabilities as open,
  regardless of an accidentally terminal accounting label

insert own durable claims
commit
```

This check-and-insert is the serialization point. An observation-only check followed by a later reservation is insufficient.

At irreversible start, revalidate authority and the claims within the same transaction as the start CAS. Claims remain held across the external effect until qualified closure. Exclude only the exact current attempt and authenticated nested operations already covered by that attempt—not all work from the same principal, task or execution.

Do not acquire another SQLite connection’s transaction from `admitted()` while an admission transaction is already active. Pass the current connection into the connection-taking helper.

### Scope independent of budgets

The resource fence must run even when:

- no optional policy is configured;
- the new role has no matching policy;
- a policy is replaced or removed;
- a successor principal replaces the original writer;
- another execution in this database targets the same resources.

Separate resource-safety tracking from optional budget charging. Preserve legacy budget behavior; absence of a cap must not mean absence of unresolved-effect tracking.

For old immutable bridges lacking this fence, do not claim coordinated admission. Where such an active producer can affect the relevant resource scope, require its qualified closure or refuse conflicting admission. Do not rewrite its pin.

### Admission paths

Within owned files:

- `OperationController.admitted()`: check affected resources and dependency liabilities after existing authority checks. This is an early refusal; the final atomic claim/start gate remains mandatory.
- `require_dependency_outputs(...)`: retain existing authored-output/verified-completion logic, adding unresolved producer/resource checks.
- `OperationController.al_command()`: derive affected scope for writer/task admission and check-consuming commands; reserve the corresponding claim before delegating to AL.
- Actual patch/process/check/delivery start: enforce the final durable claim gate.

For dependency consumption, bind both the actual producer identities and output/resource scope from the authenticated receipt. Acquire read-dependency claims for the consumer operation’s relevant lifetime, so a conflicting effect cannot begin between validation and use.

A producer’s pending effect on a different, provably independent scope need not block unrelated work. Unknown producer scope cannot establish independence. Keep authored-but-unverified dependency readiness valid when no affected liability exists.

### Release, completion and reconciliation

Permission release and effect settlement remain separate:

```text
writer finish / principal revocation / reassignment:
  may update existing permission/lifecycle state
  never close resource claims or erase liabilities

qualified current-supervisor closure:
  verify exact attempt/process/resource binding
  close claims atomically with the corresponding closure transition

uncertain closure:
  claims remain unresolved
```

No additional `execution_assignments.py` release guard is required. Release may proceed; conflicting new authority/effects must still fail their own admission gates.

Authenticated cancellation, collection and diagnosis remain available. Diagnostic reads must not be represented as stable dependency evidence while the relevant resource is unresolved.

### Required cases

- Writer finishes and principal is replaced while its process remains unresolved: overlapping patch/process/dependency admission fails.
- Permission release succeeds without clearing claims.
- No policy, unmatched role and policy replacement cannot bypass the fence.
- Two concurrent overlapping admissions: exactly one reserves; disjoint work proceeds.
- Same principal starts a second overlapping operation: no blanket self-exemption.
- Cross-execution overlap in the same database is fenced.
- Dependency validation/start race is closed by read-dependency reservation.
- Authored-unverified dependency remains usable when no affected liability exists.
- Unknown scope blocks conservatively; cleanup/diagnosis still works.
- Accounting marked settled with an unclosed process still blocks.
- Only qualified exact-attempt closure releases claims; PID disappearance does not.
- Unsupported old producers cannot be treated as instrumented merely because the database has new tables.

OPEN_QUESTIONS
- []