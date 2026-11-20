## R3 integration addendum against current R2 source

### Correlation and authoritative identities

`ProcessContext` now contains:

```python
database: Path
execution_id: str
epoch_id: str
authority_generation: int
observation_id: str
principal_id: str
```

`OperationController.dispatch()` creates the parent observation, performs existing authority checks, then wraps `_dispatch()` in:

```python
dispatch_scope(self, observation, principal["thread_id"])
```

Reuse this immutable context for correlation. Derive policy authority independently through the existing authenticated principal, assignment and execution guards.

`ProcessObserver` allocates distinct `span_id` and `attempt_id`; neither is the policy accounting attempt. Persist an explicit policy-attempt→R2-span/attempt relation, validating execution, epoch, generation, principal and parent observation. One governed operation may correlate with multiple subprocess spans; it must not accidentally consume one mutation unit per span.

Do not infer a durable process identity from R2: its spans currently contain no PID/start-identity receipt, and `timeline_contracts.COVERAGE["durable_process_recovery"]` remains false. R3 still needs its own durable execution linkage and conservative accounting recovery.

### Background process launch

Current signature:

```python
ProcessManager.start(
    owner, profile, argv, cwd, *,
    task, revision, timeout=30, environment=None, max_timeout=300,
    pass_fds=(), completion=None, report=None,
    lifecycle=None, correlation=None,
)
```

Current ordering reserves capacity under `self.lock`, releases it, begins the observer, calls `operation_popen`, installs the record, then notifies `process.started`.

Insert R3 as follows:

```text
derive/admit policy intent through existing handler authority
reserve process capacity under ProcessManager.lock; release lock
begin existing R2 observer exactly once
bind its IDs to the durable policy attempt
preallocate process handle and durable launch reference
validate remaining launch prerequisites
policy start CAS + consumption commit under short epoch/SQLite claims
release those claims
operation_popen(...)
record actual owned-process linkage
install process record under ProcessManager.lock
publish existing R2 process.started notification outside lock
start drain thread
```

`pending_process(owner, task, revision, *, context=None)` already calls `observer.begin()`. Passing that returned observer as `lifecycle` would cause `ProcessManager.start()` to call `begin()` again. Preserve exactly-once initialization when adding the hook.

Observer initialization failure before policy start may permit the original guarded unstarted-release path. Failure after policy start never refunds discrete mutation/retry charges.

R2 `notify()` catches publication errors and sets `available=False`. **Do not put accounting authorization or settlement inside this best-effort callback.** Policy failures must retain their own durable unresolved state.

### Foreground AL commands

The synchronous `OperationController.al_command()` path already uses:

```python
observer = pending_process(owner, task, revision)
capture_started = time.monotonic()
proc = operation_popen(...)
observer.notify("process.started")
bounded_capture(
    proc, timeout=timeout, report=report,
    lifecycle=observer, monotonic_start=capture_started,
)
```

Place the same durable policy start boundary immediately before `operation_popen`, after observer initialization and final authority checks. Foreground execution must receive the same policy attempt/deadline/settlement treatment as `ProcessManager.start`; returning through `GateReport.publish()` or `report.failure()` must not skip accounting settlement.

Current `bounded_capture` signature is:

```python
bounded_capture(
    proc, *, timeout=60, max_bytes=16 * 1024 * 1024,
    report=None, lifecycle=None, monotonic_start=None,
)
```

Its `monotonic_start` controls measured duration, **not the deadline**. Both it and `_drain()` currently calculate a fresh `time.monotonic() + timeout`. Add an internal absolute policy deadline to both paths; preserve existing timeout limits while enforcing the minimum remaining allowance without rounding upward.

### Settlement and cleanup

R2 terminal snapshots contain `returncode`, `duration_ms`, `stop_reason` and `cleanup_status`. Reuse these observed facts as evidence, without enlarging their meaning.

```text
perform existing owned cleanup
capture immutable local outcome snapshot
attempt policy settlement with exact policy-attempt/epoch/generation
publish existing R2 notifications independently
preserve unresolved accounting if authoritative settlement cannot commit
```

Run accounting and observer callbacks outside `ProcessManager.lock`. Current cancel, revalidate and close paths already release that lock before notifications or cancellation work; preserve this ordering.

`cleanup_status="observed"` currently records successful kill/wait calls. It is not proof of no prior effects, no provider charge or complete arbitrary descendant closure. Likewise, `process.launch_failed`/`launch_state="not_started"` is an observation classification, not sufficient evidence for a policy refund.

`record_closed_context()` checks the native parent and registered operation processes/drain threads. It does not enumerate policy attempts or foreground launch uncertainty. R3 must add exact attempt closure linkage and extend runtime recovery `_settled()` accordingly. Unknown attempts remain charged/unresolved; no replay or historical-writer exception.

Owned cancel/poll/result collection remain available under exhausted budgets while preserving authentication, ownership and disclosure guards.

### Transactions and fencing

R2 `_publication(context)` opens a separate connection, then acquires shared `epoch_action_lock` and `transaction(conn)`, validating explicit epoch/generation.

Do not call `_publication`, `observer.begin()` or `notify()` while holding a policy SQLite transaction: separate connections would contend with the transaction already held. Keep R2 publication sequential and outside policy transactions.

For policy audit links that must commit atomically with accounting, use a connection-taking helper inside the existing policy transaction, following `timeline_store.link(conn, execution, ...)`. Extend the closed timeline vocabulary/schema deliberately; do not misuse an existing process or semantic event kind.

`execution_projection.ingest_event()` already commits accepted event, projection, semantic link and cursor together. Its rejection diagnostics separately revalidate epoch/generation. Preserve both fences. Semantic retry/task reports provide correlation, not authorization to charge, refund or settle a policy attempt.

### Immutable runtime closure

`execution_bootstrap.authority_snapshot()` explicitly includes R2 modules through `_RELIABILITY_MODULES`. Add the complete R3 policy-module closure explicitly; nested packages are not automatically included.

Existing bridge snapshots remain immutable. Qualify policy enforcement only for supporting selected profiles and use existing supported transitions. Missing hooks in old bridges cannot be repaired by database configuration or handwritten pin changes.

### Focused verification obligations

- Exactly one observer initialization; explicit policy/R2 identity binding.
- Observer publication failure after launch cannot trigger replay or lose accounting.
- Foreground/background deadline and settlement parity.
- No database callbacks under the process lock or nested cross-connection publication.
- Stale settlement and diagnostic callbacks remain fenced.
- R2 `not_started`, exit and cleanup labels cannot authorize refunds.
- Foreground uncertainty blocks unsafe retirement.
- Frozen runtime closure includes all policy dependencies.

OPEN_QUESTIONS
- []
