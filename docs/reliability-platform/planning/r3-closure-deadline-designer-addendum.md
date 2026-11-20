## R3 addendum: qualified deadline enforcement and closure

**Explicit policy time limits require a qualified stoppable boundary. Owned-process-group absence alone does not qualify arbitrary commands or native execution trees.** Preserve ordinary best-effort timeouts when no explicit policy time cap applies; do not silently present them as strict enforcement.

### Supported boundary and pre-effect refusal

Current source establishes:

- macOS `OperationIsolation` permits `process-exec process-fork`; its filesystem restrictions do not prevent descendants creating another process group/session.
- Native `prepare_isolation()` likewise does not establish whole-tree termination.
- Linux operation argv includes `--unshare-pid` and `--die-with-parent`. These are relevant mechanisms, but qualification must bind the actual launcher/namespace lifecycle and demonstrate descendant disposal; flag presence alone is insufficient.
- `runtime_support()` currently checks policy-module membership. This establishes hook availability, not deadline or closure capability.

Add a source-bound internal capability check before consuming a start permit or launching effects:

```text
if applicable policy requires operation_deadline_ms
   or remaining execution_elapsed_ms:
  require qualified profile for this handler's actual execution boundary
  require trusted clock continuity
  require deadline propagated to every owned launch/check path
  otherwise refuse UNSUPPORTED_ENFORCEMENT_CAPABILITY or CLOCK_UNCERTAIN
```

For current macOS arbitrary commands and native execution, explicit strict time enforcement is unsupported unless a separately verified mechanism establishes stoppability beyond the initial process group. Refuse **before command/provider execution**, not after observing an escaped descendant.

A closed trusted handler can qualify independently when its implementation has bounded work, no untracked asynchronous work, and effective deadline checks. Do not infer that qualification from a “read-only” command classification.

The time boundary is local governed execution. It never promises reversal of completed effects or cancellation of remote provider liability. Requirements that include those stronger effects need their own qualified capability or refusal.

### Closure facts versus accounting terminality

Keep these facts distinct:

```text
parent_reaped
owned_group_absent
qualified_execution_boundary_closed
remote_liability = none | settled | unresolved | unavailable
```

Independent group-absence evidence strengthens the first two facts. It cannot manufacture either of the latter facts.

In current `ProcessHooks.finish()` / `Attempt.finish_processes()`:

```text
owned_group_closed alone:
  record that exact observation
  retain discrete charges
  do not imply whole operation closure where escape/external liability is possible

settle accounting lifecycle:
  require all closure obligations declared by the admitted handler/profile
  require no unresolved execution or liability obligation
  never refund already-consumed mutation/retry units
```

Thus the current predicate “every `policy_processes.closure` equals `owned_group_closed`” is insufficient as a universal settlement predicate. Preserve unresolved state when the qualified boundary cannot be established. Do not assign measured external usage from reserved units.

### Current-supervisor settlement authority

Separate admission from closure publication:

```text
admit/start:
  retain current principal, assignment, writer, dependency,
  specification, policy and capability checks

record closure:
  require current authenticated supervisor ownership
  require exact execution/epoch/generation
  require exact durable attempt and process-instance binding
  permit only closure/accounting transitions for that attempt
  require appropriate evidence
  grant no execution permit and no new writer authority
```

An original writer becoming inactive must not prevent its current supervisor from recording closure. Therefore closure publication must not reuse `Attempt.authority()` unchanged where it calls `operation.admitted(...)`.

Conversely, “current epoch” alone is not supervisor authentication. Bind the actual owning supervisor/invocation and trusted receipt provenance. Retired callbacks remain refused. Settlement must not bypass specification checks to launch work, restore capabilities or speculate refunds.

### Retirement and crash reconciliation

`require_closed()` may remain the final barrier only if `settled` already means all required closure obligations were satisfied.

```text
before epoch activation / closed-context publication:
  require no potentially executing predecessor attempt
  require qualified closure evidence for each started execution boundary
  retain unresolved liabilities and charges
  refuse transition if its required closure cannot be proved
```

Do not let `record_closed_context()` inherit a stronger meaning from its parent/process/thread checks.

After a crash:

- Missing PID, dead supervisor, absent initial process group or stale heartbeat is insufficient.
- Reconcile only from durable, exact process-instance and qualified boundary evidence.
- A launcher crash before PID publication remains ambiguous.
- Never replay an uncertain started attempt.
- Clock continuity remains independently required. `CLOCK_ID` equality supports same-boot continuity; it does not repair an untrusted reconstructed origin or absent timing evidence.

### Required cases

- A command creates a new session/group: initial group disappears, but whole-operation closure remains unresolved.
- Explicit time policy refuses unsupported macOS arbitrary/native execution before effects.
- Uncapped legacy timeout behavior remains available without strict-enforcement claims.
- Qualified Linux namespace disposal covers escaped groups and launcher failure; otherwise refusal.
- Inactive original writer, live current supervisor: exact closure can record without admission authority.
- Foreign supervisor or stale epoch cannot settle.
- Group absence cannot clear unknown provider liability or refund charges.
- Crash without durable launch identity remains unresolved.
- `require_closed()` rejects superficially “group closed” attempts with outstanding obligations.
- Clock discontinuity refuses new time-budgeted work while authenticated cleanup remains usable.

OPEN_QUESTIONS
- []