## R3 AL retry integration addendum

Both `al_retry.py` copies currently match. `cmd_retry(a, sid)` runs under `al_store.locked(sid)` through `al_state._dispatch()`. Its recovery effect is `R.apply_checkpoint(...)`, followed by retry-history/count changes and one `al_store.save()`.

Use a **dedicated one-use retry channel**, serviced independently of `OperationController.lock`. Do not extend `CheckChannel` into a general accounting endpoint or give subprocesses writable database access.

### Actual and new interfaces

Preserve:

```python
OperationController.al_command(controller, principal, request, state)
al_retry.cmd_retry(a, sid)
al_recovery.apply_checkpoint(st, data, checkpoint_id)
al_store.locked(sid)
```

Add in both AL distributions:

```python
retry_preconditions(st, a) -> RetryPreflight
require_retry_grant(st, a, preflight) -> RetryGrant
```

Add in Harness:

```python
RetryChannel(frozen_authority, expected_retry, policy_attempt)
RetryChannel.claim(request) -> RetryGrant
RetryChannel.close()
```

`RetryChannel` follows the bounded socketpair/framing pattern of `InitializationChannel`, with one fixed `claim` operation. It accepts no argv, paths, SQL, resource selectors or alternative accounting amounts.

### Parent preparation

```text
OperationController.al_command:
  retain existing command whitelist and principal restrictions
  retry remains orchestrator-only through the existing child restriction

  parse exact retry role/reason from structured AL arguments
  reject caller-supplied retry-channel flags/environment/descriptors

  verify selected AL pin advertises the required retry hook
  if configured enforcement requires the hook and pin lacks it:
    refuse UNSUPPORTED_ENFORCEMENT_CAPABILITY before launch

  derive expected failure from the already trusted AL source
  bind:
    execution/epoch/generation and immutable spec/approval
    AL run/session and selected runtime pin
    authenticated requesting principal and its role
    target retry role
    failure occurrence/checkpoint identity
    current per-role retry count
    exact requested retry operation/reason
    effective policy digest and applicable pools/capabilities

  retain sensitive exact-comparison material only in private process memory
  persist opaque failure/checkpoint references, not reason hashes or text,
    in the policy ledger

  atomically admit/reserve the retry attempt under the R3 protocol
  create private RetryChannel with this frozen server-owned context
  pass its descriptor only to this managed AL invocation
  launch using existing contained foreground/background path
```

The requesting principal and the target retry role are distinct fields. Do not infer a child invocation ID: existing failure records identify a role/checkpoint, not necessarily an independently observed native attempt. Record that provenance limitation.

The retry reservation and any mutation reservation belong to the same operation admission. The retry start boundary described below is distinct from observing the launcher subprocess start; do not consume the retry twice.

### AL state-lock validation and one-use claim

Extract the existing retry checks without relaxing their order or semantics:

- Valid retry role and implementation-failure classification.
- Existing once-per-role limit.
- Matching pending failure and allowed stopped status.
- Nonempty reason and `retry_safe=yes`.
- No outstanding writer/check obligations.
- Exact peer-reconciliation requirements and artifact verification.
- Checkpoint membership and verified checkpoint bytes.

Preserve the existing exhausted-role behavior that records `STOPPED_WITH_BLOCKERS`; a preflight helper must not silently remove that behavior.

```text
cmd_retry(a, sid), already holding al_store.locked(sid):
  st = load(sid)
  preflight = retry_preconditions(st, a)

  if Harness-bound policy requires retry enforcement:
    grant = require_retry_grant(st, a, preflight)
    require exact current run/spec/failure/checkpoint/role/count binding

  changed = R.apply_checkpoint(st, verified_checkpoint, checkpoint_id)
  append existing retry_history record plus opaque policy attempt/grant refs
  increment existing retries[target_role]
  clear failure
  save(sid, st)
```

`require_retry_grant` uses the inherited channel. Immediately mark its descriptor non-inheritable; close it after the exchange. No grant path or reusable bearer file is returned to the agent.

### Independent broker claim

The broker runs in a separate service thread, like existing `CheckChannel`, but its callback must never acquire:

- `OperationController.lock`;
- the AL state lock;
- `ProcessManager.lock`.

It uses frozen server-owned context and a fresh database connection.

```text
claim(request):
  validate closed frame, channel nonce and exact expected retry payload
  compare current preflight to parent-held expected failure/operation
  reject drift without starting retry

  acquire short shared epoch claim
  BEGIN IMMEDIATE
    revalidate explicit epoch/generation, current spec/approval,
      principal release state, policy and capability expiry/revocation
    require this policy attempt is reserved and not previously claimed
    CAS reserved -> started
    move retry and applicable discrete units reserved -> consumed
    persist one-use claim identity
  COMMIT
  release epoch claim
  send exact bound grant
```

Use existing current-authority validation without calling back through `dispatch()` or acquiring its lock. No SQLite transaction remains open while AL continues or while the parent waits for completion.

The successful claim commit is the **irreversible retry accounting boundary**. A second claim on the channel fails; an ambiguous response is not resent as a new executable grant. The child must not retry a failed grant exchange.

### Crash and drift behavior

- Drift before claim: AL refuses; guarded unstarted reservation release remains possible.
- Claim committed but response lost: charge remains; no automatic replay.
- Claim received but checkpoint recovery later refuses: charge remains, with no claim that AL retried successfully.
- Crash before `al_store.save()`: charge remains; retry effect is unresolved until exact state/process reconciliation.
- Save succeeds but response/observation fails: inspect existing retry history and grant reference; do not rerun.
- Parent/channel death: child receives no grant and cannot proceed.
- Epoch retirement after a committed claim does not reverse an already-started retry. Existing retirement/closure barriers must prevent unsafe successor activation.

A grant does not bypass any later `apply_checkpoint()` checks. AL’s ordinary state transaction remains authoritative for whether recovery actually committed.

### Protected runtime and channel restrictions

Keep the new protocol in the verified AL/Harness runtime closures. Reject configured enforcement on unsupported pins; no upgrades, state-scraping fallback or argument trick bypass.

Do not reuse the initialization channel’s authorization or broaden `CheckChannel` actions. The new channel’s fixed callback can only consume its prebound retry reservation. AL/check subprocesses retain their existing database write prohibition.

### Required tests

- Both-platform parity and all existing retry refusal/once-per-role cases.
- Real parent holding `OperationController.lock` while child claims under AL’s state lock: no deadlock.
- Foreign role/principal/spec/epoch/failure/checkpoint/count and changed retry reason refuse.
- Two claims consume once; duplicate/lost response never authorizes replay.
- Revocation before claim denies; committed claim remains charged after later failure.
- Crashes before claim, after commit, before AL save and after AL save.
- Checkpoint drift after grant preserves conservative charge.
- Receipt references reconcile saved history without rerunning recovery.
- Channel cannot select commands, paths, pools or amounts; child cannot write Harness DB.
- Unsupported old pins refuse required enforcement; uncapped legacy behavior remains supported.

OPEN_QUESTIONS
- []
