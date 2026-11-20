PSEUDOCODE

## `harness/policy/resource_bindings.py`, `resources.py` (CORE)
purpose: Separate immutable presentation from coordinated consumption of mutable dependency outputs.

signatures:
- Preserve `derive(operation, principal, state, *, task_id, resources, mutating, boundary)`.
- Add internal dependency-selection helper returning authenticated source claims and receipt bindings.
- Extend `prepare(...)` to distinguish these internally derived operation modes.

inputs / outputs: Actual task graph, current authored/completed producer receipts, server-selected repository and artifact roots. No caller-supplied safety classification.

state changes: Strict consumers reserve claims on their existing `Attempt`; presentation never enrolls a resource domain.

control flow:
1. Assignment presentation and typed output publication retain `verify_outputs=False`, immutable-reference validation and physical artifact claims. Do not recursively attach source dependencies.
2. For actual mutable dependency consumption, recursively select the same receipt variant used by `require_dependency_outputs`: completed task → completion binding; otherwise current authored output.
3. For each dependency with source outputs, derive conservative scope from its declared files plus source-output paths. Bind execution/task/revision, producer invocation/writer references and the complete governing receipt identity.
4. Mark these private claim dictionaries with an internal discriminator, e.g. `purpose="mutable_dependency"`. This is persisted server-derived intent, not authority supplied by a contract.
5. A nonempty mutable-dependency set forces coordinated claim accounting on the same Attempt even when no policy document matches and no sticky domain exists.
6. Preserve existing boundary qualification. An arbitrary process does not become qualified because it consumes a dependency; refuse its unsupported coordinated execution before launch.
7. Select all consumers before admission: patch, mutating command, worker-owned registered check, and dependency setup where their actual handlers require mutable outputs. Do not discover additional scope after reservation.
8. Separate metadata validation from byte-consuming validation in `task_environment.task_unit`; its current call from `resources.prepare` must not read dependency bytes before reservation.
9. Ordinary diagnostic reads and immutable presentation remain distinct from a request for stable dependency consumption.

failure / error behaviour: Missing receipt, ambiguous resource scope or unsupported boundary refuses affected strict consumption. Unknown scope overlaps; it does not establish independence.

integration points: `OperationController.admitted`, `require_dependency_outputs`, `Attempt.current`, registered-check preparation and setup continuation.

## `harness/policy/claims.py`, `domains.py`, `admission.py` (CORE)
purpose: Serialize transient strict consumption against both existing legacy liabilities and future launches, without sticky enrollment.

signatures:
- Add `require_predecessors_clear(conn, dependency_claims, *, own_attempt_id)` using the supplied transaction connection.
- Extend claim conflict checking with an internal unqualified-launch mode.
- Extend `Attempt.admit`, `current`, `start`, `start_launcher` and claim revalidation.

control flow:
1. Prepare canonical roots, immutable runtime-capability observations and receipt metadata outside the admission transaction.
2. In the existing `Attempt.authority()` transaction—current epoch followed by `BEGIN IMMEDIATE`—perform:
   ```
   validate current authority, policy and exact receipt bindings
   validate supported coordination coverage
   check existing enforced claims
   if mutable_dependency claims:
       check relevant legacy admission records
       check pending/unknown native launch presence
       check unmatched process observations
   insert this Attempt
   reserve all its claims
   reserve budgets/consume existing capabilities
   commit
   ```
3. Legacy predecessor checking examines every overlapping `policy_legacy_admissions.resources` entry, including entries marked `read_dependency`. An unqualified read-only command is not a qualified closed read. Unknown entries conflict conservatively.
4. Do not remove or reinterpret legacy records during this check. Existing fenced no-start or independently qualified settlement remains their only applicable resolution.
5. Reuse the conservative unmatched-native/process inventory currently present in `domains.enroll`, extended to include pending launch possibility, not merely `child_pid IS NOT NULL`.
6. Enforced read/read sharing remains valid between qualified coordinated consumers. It must not permit an unqualified legacy launcher to overlap a strict mutable-dependency claim.
7. Legacy admission, `start_launcher` and `start` perform that boundary-aware conflict check in their existing transactions. Thus:
   - Legacy admission first: its durable presence prevents strict reservation.
   - Strict reservation first: conflicting legacy admission/launch refuses.
   - Enrollment first: existing enrollment rules apply.
   - Strict reservation first: overlapping enrollment encounters its enforced claim and refuses; no implicit enrollment occurs.
8. At start, recompute and compare the governing dependency receipts and derived claims, recheck predecessor safety and exact stored claim ownership, then commit the existing start/launcher CAS.
9. Claims survive initial source hashing, repeated validation, dispatch return and asynchronous consumption. Existing `owner_finished`/process settlement closes them only after the consuming effect is qualified closed.
10. Validation failure before any launcher/effect permit uses existing `release_unstarted`. After a permit or uncertain effect, retain liability; no independent “typed validation finished” settlement.

data flow: Claim discriminator and receipt bindings live in the existing private claim JSON. Public coverage labels cannot alter them.

edge cases:
- Same Attempt may hold both destination write claims and dependency read claims; exclude only its exact ID.
- Policy removal, unmatched roles, replacement principals and other executions sharing the authoritative DB still encounter claims.
- Unsupported relevant immutable bridges refuse coordinated consumption. Require source-bound protocol support; module-name presence alone is insufficient.
- No guarantee extends to other databases or uninstrumented external actors.

## `harness/execution_authority.py`, `execution_operations.py`, `task_environment.py` (CORE)
purpose: Require reservation before dependency bytes and preserve it through use.

signatures:
- Extend `require_dependency_outputs(..., verify_outputs=True)` with an internal consumption context carrying the existing Attempt and caller connection.
- Preserve `OperationController.admitted(..., verify_outputs=True)`.
- Add a metadata-only mode to `task_unit(...)`.

control flow:
1. `verify_outputs=False` performs existing graph, receipt, lifecycle, writer, evidence, revision and contract-shape checks only. It must not resolve dependency artifacts, inspect source descriptors or hash bytes.
2. For `verify_outputs=True`, require the exact current Attempt to cover all derived mutable-dependency claims before the first source read. Verify persisted reservation and receipt binding using the supplied connection.
3. Reject missing or insufficient current Attempt coverage; do not create nested admission or recursively call `prepare`.
4. Outside policy transactions, run existing typed-result/report validation and descriptor-backed source reads. Retain ordinary-file, no-follow, identity-before/after, digest and mode checks.
5. Reuse exact validated immutable buffers in report/contract consumers. A source hash stored inside an immutable receipt remains an observation, not a substitute for current-source validation.
6. Before the consuming effect, existing Attempt start revalidates authority and receipt identity. A changed receipt invalidates the earlier hash result.
7. Polling, cancellation and background authority checks outside an owning Attempt use metadata-only validation. They must not manufacture a short-lived strict read claim or release the process owner’s claims.
8. Where a continuation actually consumes mutable bytes, restore the exact stored owning Attempt context and verify its claims; queued setup retains that Attempt until completion.
9. `apply_patch` authorization, registered-check launch and setup preparation must use the scopes reserved by their dispatch Attempt. Newly discovered dependency scope refuses rather than silently broadening admission.

lock order: Prepare identities → short epoch/DB admission transaction → release transaction → artifact/source reads → short start transaction → effect. Artifact-index locks never contain policy admission, authority acquisition or nested database transactions.

## `harness/execution_worker.py`, `policy/reconciliation.py`, native no-start integration (CORE)
purpose: Close the native launch race confirmed by Architect.

signatures: Extend the existing worker `Launching` transition and `native_admission(conn, execution)` with strict-claim/presence checks.

control flow:
1. Treat pending native launch intent as unknown scope unless trustworthy existing authority proves a narrower scope. No self/execution exemption for an outer native process.
2. In the short current-epoch transaction that changes the exact worker attempt to `Launching`:
   ```
   verify worker token, epoch and allowed prior state
   reject conflicting active/reserved/unresolved strict claims
   validate required supported coordination capability
   CAS exact attempt to Launching
   commit
   ```
3. Execute `Popen` afterward, never inside the transaction. The committed launch possibility prevents strict consumers from entering the intervening gap.
4. Strict admission inventories `Reserved`/`Launching` and unresolved historical launch possibilities conservatively. A future reservation may wait/refuse at launch; it cannot cross an existing strict claim.
5. Native policy checks before spawn remain in place. A failed spawn is resolved only through the exact fenced no-start path.
6. Prepare existing no-start receipt evidence outside the DB transaction, then validate its exact token/epoch binding and fence that attempt from future launch in the transaction. A status string, null PID, parent exit or process-group disappearance is insufficient.
7. Qualified native closure, if available, uses its existing independent proof. This increment does not qualify arbitrary native processes.
8. Relevant unsupported old bridges retain immutable pins and refuse coordinated operation. Do not claim their launch paths honor the new protocol.

failure / error behaviour: Crash after launch permission but before PID publication leaves unknown presence. Immutable assignment/output presentation can remain available while strict mutable consumption refuses.

## Qualified producer: `control_qualification.py`, `control_channel.py`, `execution_operations.py`, installer control transport, both `al_writer_handoff.py` copies (CORE)
purpose: Prevent newly supported handoff generation itself from introducing an unqualified predecessor.

signatures:
- Add exactly `sealed-al-writer-handoff-v1`.
- Extend existing `prepare(operation, args, state)` and authenticated activation/receipt discrimination.
- Add a fixed handoff-body buffer operation to the existing private channel.

inputs: Exact grammar:
```
writer handoff --id WRITER --body-file artifact:ID:SHA [--area AREA]*
```
Require full options, bound writer, valid registered areas, no duplicates or extra flags. Absent areas means the current sorted evidence-plan areas, bound during preparation.

control flow:
1. Preserve supervisor authority. Select this supported method before legacy classification; its qualified transient claim accounting does not enroll domains or grant native workers new rights.
2. Bind current run/writer/phase/graph/task revision, selected areas and plan digest, original indexed body reference, source specification and dependency-receipt metadata.
3. Use actual server-selected issuing-state/artifact roots for write claims and execution-artifact roots for immutable input claims. Do not use a synthetic namespace to defeat genuine conflicting enforced claims.
4. Independently qualify the exact pin/import closure, launcher, interpreter, Git identity/configuration and current tested main/helper containment profiles. Existing retry/finish labels supply no qualification.
5. After this same Attempt reserves claims, obtain the body through `ArtifactStore.buffer`. Release its index lock before further policy work.
6. Keep that exact buffer for child consumption. For this qualified method only, preserve the original artifact token in argv instead of the current resolve-to-path/reopen sequence.
7. The authenticated broker serves only its bound buffer, optionally in bounded ordered chunks within existing framing limits. It accepts no path, arbitrary artifact ID or replacement bytes.
8. In `cmd_handoff`, the qualified context supplies those bytes; legacy standalone behavior remains unchanged. Preserve UTF-8, nonempty-body and managed-block checks.
9. Preserve the actual callgraph:
   ```
   protected launcher/pin activation
   al_state._dispatch → locked(sid)
   al_store.load and all origin/recovery/editorial/publication/identity guards
   cmd_handoff writer/phase/area validation
   evidence_block (renders commands; executes none)
   prompt snapshot
   dependency_records metadata snapshots
   al_agent_exchange.produce
   al_store.save and protected postchecks
   method-bound completion receipt
   exact observed main exit
   ```
10. Use existing finite Git helpers for bootstrap/identity/guard calls. No Git grammar expansion, registered check execution, arbitrary child launch or recovery-file option.
11. Bind final receipt to this method, argv/input reference and qualified runtime. Close only this invocation after all helpers and main complete normally under the existing closure protocol.
12. Preserve previous legacy liabilities. A new qualified handoff cannot retroactively repair an old generation or writer-start command.
13. Old pins lacking the method remain readable through immutable presentation. They cannot claim this qualification or be silently upgraded.

failure / error behaviour: Save, transport completion and process closure remain separate facts. Response loss does not authorize replay; missing completion or unknown helper retains liability.

## Immutable presentation adapters (CORE)
purpose: Expose truthful guarantees without widening authority.

control flow:
1. Cache resolved immutable buffers within one validation/presentation operation. Validate and render the same buffer; current `_selected_reserved` must not validate a prompt through the resolver and then reopen it as though the second read were already authenticated.
2. Keep authenticated root/index selection and bounded ordinary-file checks in `agent_exchange.resolver`, `ArtifactStore.buffer`, assignment presentation and typed-result consumers.
3. Return coverage facts independently:
   - `immutable_snapshot_validated`: exact returned bytes authenticated.
   - `mutable_source_not_checked`: no current dependency-source verification performed.
   - `coordinated_source_consumption`: applicable claims and predecessor checks cover actual use.
   - `legacy_uncoordinated`: historical/unqualified execution coverage.
4. Labels grant no authority and never replace receipt, principal, epoch or claim validation.

TESTS_NEEDED
- `tests/test_agent_contracts.py`, assignment/artifact tests: real generated assignment remains readable despite an existing unqualified legacy generation row; no source reads, enrollment or row deletion. Exact-buffer replacement/tampering/wrong-root/stale-receipt failures preserve accepted outputs. **R4-04/06/07/09**.
- `tests/test_policy_resources.py`: legacy-first versus strict-first races, including unqualified read/read; active/unknown predecessors; enrollment races; independent scopes; policy removal, role changes and replacement principals. **R4-03/05/08**.
- Dependency reconciliation/operation tests: claims exist before first source hash and remain through mutation/process use; receipt change before start refuses; metadata mode performs zero dependency-byte reads; polling/cancellation preserves original claims. **R4-03/08**.
- Worker tests: strict reservation versus `Launching` commit in both orders; crash before PID publication; exact no-start fencing; outer-native presence and unsupported bridges refuse without PID-based closure. **R4-03/05/09**.
- Both-platform public handoff tests plus sealed-control tests: actual generation → assignment → typed output; separately demonstrate strict-consumer success under supported predecessor conditions and refusal under genuine unresolved legacy/native presence. No mocked closure.
- Handoff qualification tests: exact argv/body/areas/context; buffer replacement; unsafe config; unsupported pin; forbidden subprocess; helper interruption; save-before-response-loss; foreground/background receipt binding. **R4-05/06/09**.
- Lock-order tests: no nested admission transaction and no artifact-lock→policy-lock acquisition.
- Existing authored-unverified dependency tests remain valid when resource-safety conditions are satisfied. **R4-08**.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Public-path tests, parity/source manifests and documentation wait for the held claim and qualification interfaces. Engineer retains current ownership; Builder follows stabilization.

OPEN_QUESTIONS
- []
