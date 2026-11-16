# Approved recovery increment — AL implementation design

The developer requested a dashboard button to reset planning when a task cannot
continue and explicitly answered **Both** to approval of the knowledge-platform
architecture and recovery of AR-2301 through a fresh audited execution.
This records the actual AL-Architect's source-backed recovery design. The original
mission and architecture remain in their separate immutable proposal artifacts.

## User behavior

Add **Start a new planning run** to the existing task controls. First show a
read-only preview. The exact confirmation, **Start new planning**, keeps the same
task and approval, preserves available history, starts a new AL session/run and
epoch, and carries no previous PASS, review, writer or agent authority. Repository
files are unchanged by this action. Explain incomplete history and source loss.
An interrupted transition offers **Finish recovery** for the same request.
Continue task and Retry checks retain their existing meanings.

Distinguish an intact runtime's ordinary replan from actual missing-state
recovery. Unreadable, permission-denied, corrupt or binding-mismatched state is not
silently classified as missing. A missing checkout cannot become implementation
ready just because the user restarts planning.

## Existing mechanisms to preserve

`executions.approval_id` is unique. Reuse the same execution with a new epoch,
not another approval. Existing ordinary successor preparation requires intact
predecessor state; do not weaken that path. Existing Continue task settlement also
reads predecessor state, so it cannot implement this recovery button.

Reuse the protected issuer's initialization channel, genuine AL start, fencing,
compare-and-swap epoch selection and worker reservation. Add an explicit closed
missing-state mode. Do not accept replacement state JSON from the operator,
substitute old PLANNING snapshots, edit pins, or expose an arbitrary runtime path.

Relevant seams: `harness/execution_controls.py`, `execution_settlement.py`,
`execution_runtime_recovery.py`, `execution_runtime_activation.py`,
`execution_runtime_fences.py`, `execution_epochs.py`, `execution_hosts.py`,
`execution_initialization.py`, `execution_initialization_channel.py`,
`execution_worker.py`; `al/installer/runtime_pin_transaction.py`,
`runtime_pin_initialization.py`, `runtime_pin_boot.py`, `runtime_successor*.py`,
`runtime_start_isolation.py`, `activity_profiles.py`; mirrored `al_store.py` and
`al_new_origin.py`; existing dashboard and CLI action handlers.

## Recovery contract and inputs

Public operations provide preview, start, inspect and finish/recover through the
same controller used by the dashboard. Designer specifies the exact closed
schemas and CLI syntax before implementation.

The preview binds task/execution/approval, current epoch/generation, protected
predecessor transaction, state condition, original task package and accepted
amendment manifest, pending material decisions, known failures, candidate/source
identity, source-restoration limits, settlement coverage, durable layout policy,
history completeness and a canonical hash. Revalidate every binding at start;
an old preview or browser epoch authorizes nothing changed in the meantime.

Recovery authority validates current approval, sealed bytes, approved tests,
protected issuer transaction and selector independently of absent mutable state.
Build a typed recovery-lineage artifact from the original hash-bound package,
verified accepted deliveries, registry bytes/hashes and their acknowledgment
events. Earlier lineage records establish only their actual historical claims.
Do not interpret a database `Resolved` label as amendment authority. Verify
delivery identity/hash, ordering, base revision and affected scope. Preserve
rejected/pending decisions and label unavailable history. Incomplete accepted
requirements allow investigation with explicit unresolved obligations, not an
unqualified implementation grant or silent fallback.

Independent settlement validates all capable attempts and owned operations,
matching native teardown receipts, worker and epoch locks, delivery/browser
operations and protected process census. Missing PID or an Exited label alone is
insufficient. Reuse validated closed-context receipts; never mark unavailable
writer/check history completed. Unknown ownership remains a capability problem.

## Protected transition

The new explicit mode follows:

`PREVIEWED → PREPARING → PREPARED → FENCING → DB_SELECTED → ACTIVE`

1. Revalidate preview, acceptance, candidate, settlement and expected epoch.
   Persist request identity; reserve exactly one successor session/run and
   issuer-derived durable directory.
2. Invoke genuine contained protected AL initialization. New state is PLANNING,
   proof pending, no current writers/evidence/review or inherited agent identities.
   Save the immutable preparation receipt; preparation grants no product authority.
3. Recheck settlement and fence the old subject through protected publication
   locks and its retained transaction. Retain original metadata as history. A
   later reappearance of old state must not revive its capability.
4. Harness independently validates the issuer fence, atomically archives the old
   epoch and selects the new session/run/handoff/generation. Preserve historical
   status, result, cursor and native identity. Reset only the new epoch's runtime
   cursor and native identity; do not resume the old conversation.
5. Publish and validate the active selector and durable transition. Launch one
   fresh native root through an idempotent owned dispatch only after issuer and
   Harness identities agree. Normal AL planning/admission still govern writers.

Retry the same request and exact preview. Different concurrent requests cannot
race the selected epoch. Once fencing starts, recover forward, never reactivate
the old subject. Handle the receipt-before-SQLite-acknowledgment crash window as
well as fencing/DB-selection/activation/start acknowledgment windows. Do not
blindly initialize another successor after a partial transition.

## Durable storage

Use a new versioned protected transaction layout for Harness-bound runs while
retaining v1/v2 readers. The issuer derives a private execution/epoch runtime
outside the checkout and temporary directory hierarchy from verified Harness
authority metadata. Bind it in the transaction, initialization, handoff, selector
and activity registry. No `AL_RUNTIME_DIR`, arbitrary caller path, broad registry
write permission or silent relocation of an old pin.

Update source lookup, runtime validation, both platform stores/origins, startup
containment, successor artifact handling, activity discovery and installer active
run protection together. Newly started Harness runs use durable layout; intact
legacy runs migrate only through a supported successor.

AR's database and original spec paths are also temporary. Their identities cannot
be silently changed; retain durable recovery inputs now and treat any later
identity migration separately. Durable AL state does not constitute a backup of
the source checkout or all private local state.

## Source restoration and real AR use

Source restoration is a separate previewed action/prerequisite. Git can restore
known missing regular baseline blobs without overwriting existing files. Exclude
protected/secret and special/aliased entries. Record exactly what was restored
and what remains unknown; baseline restore does not recover deleted uncommitted
edits. Recover independently retained applied patches only with their actual
provenance, and never assert completeness that cannot be established.

For AR-2301: revalidate recovered approval and source inventory; complete supported
source restoration; verify native/owned-process closure; confirm exact planning
preview; create the fresh durable epoch on the same approval/task; have a genuine
new Architect assess remaining approved work. Prior PASS or completion does not
transfer.

## Ownership, ordering and verification

AL-Engineer owns protected recovery/layout/lineage/settlement/fresh-start backend
and focused transaction/security tests. AL-Builder follows with dashboard/CLI,
browser fixtures, documentation and release declarations. Shared packet/schema
and activity interfaces are sequenced. No early parallel writers.

Keep schema changes additive and schema-only; reuse existing transition tables
where their contracts suffice. Source inspection and issuer calls occur outside
SQLite write transactions. Preserve the supported macOS prepared-start boundary
unless equivalent Linux isolation is implemented and verified. Operational
environment gaps remain explicit; no isolation fallback is permitted.

Required tests include actual protected missing-state startup; no inherited
authority; accepted/rejected/pending/corrupt amendment inputs; live/missing/forged
settlement evidence; interruption at every durable boundary; double clicks and
stale previews/epochs; old callbacks or restored old state after fencing; durable
runtime survival and unsafe-path rejection; source-loss diagnostics without
source edits; and an actual fresh native Architect demonstration. Chrome checks
desktop/narrow layout, overflow, alignment, focus, refresh and retry progress.

The existing expanded-depth checklist also reopened skipped questions despite
accepted source-bound proposals. Public commands reconciled this run against its
actual approval. Include a narrow regression fix so future risk reassessment
retains accepted resolutions without repeated user questions.
