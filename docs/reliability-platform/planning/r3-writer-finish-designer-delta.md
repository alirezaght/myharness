PSEUDOCODE

## `harness/policy/control_qualification.py`, `resources.py`, `admission.py`, `processes.py` (CORE)
purpose: Qualify exactly `sealed-al-writer-finish-v1`, separately from retry.

signatures: Extend existing `prepare(operation, args)`, `Qualification.binding`, `Qualification.qualifies(process)` with an internally selected method/profile; no caller-selected capability.

inputs / outputs: Exact arguments:
`["writer", "finish", "--id", writer_id, "--outcome", outcome]`,
where outcome is `success`, `failed`, or `abandoned`.

state changes: Create only this invocation’s attempt, helper records and completion receipt. Never settle preceding writer/process/resource obligations.

control flow:
1. Require existing supervisor authority. Preserve `OperationController.al_command` restrictions; this qualification grants native workers no additional commands.
2. Bind writer ID to the actual current run’s writer record, principal authority, execution epoch, selected pin and exact arguments. AL rechecks `ACTIVE` under its state lock; stale preflight cannot authorize a changed writer.
3. Prepare immutable interpreter, launcher, pin/import closure, Git identity and configuration qualification outside admission transactions and AL locks, using the existing retry qualification mechanism.
4. Select the dedicated boundary only after qualification succeeds. Update existing explicit boundary checks in admission/process settlement to recognize this exact profile, never a caller-provided cleanup flag.
5. Treat contribution reads as observed provenance, not stable dependency consumption. Permit the specifically qualified release/control operation despite the producer’s unresolved product liabilities; its permitted writes remain protected AL state/artifacts. Do not grant arbitrary overlapping product mutation or dependency access.
6. Preserve exhausted-budget cleanup availability through this fixed release method’s existing authorized cleanup allowance; do not extend that allowance to other AL commands or arbitrary subprocesses.
7. At start, revalidate authority, method binding and prepared identities using existing fencing. Record launch intent before launch.
8. Close this invocation only with its matching qualified completion receipt and exact main-process exit. Earlier liabilities remain unchanged even when AL records `FINISHED`.

failure / error behaviour: Unsupported pin/configuration/helper scope refuses the qualified command. No legacy launcher fallback in an enrolled domain. A failed finish never implies permission settlement, producer closure or dependency readiness.

integration points: Existing `ControlChannel`, `ProcessHooks`, foreground/background process management; no `RetryChannel` grant or retry-attempt claim for writer finish.

## Both AL script trees: `al_writers.py`, `al_task_attribution.py`, `al_scope.py`, `al_gitlinks.py` (CORE)
purpose: Preserve the actual writer-finish semantics within a separately authenticated closed callgraph.

signatures: Preserve `cmd_writer(a, sid)`, `_cmd_writer(a, sid)`, `finish_contribution(st, writer, before_ref)` and `observe(st, *, retain_content=False, expected=None)`.

control flow:
1. Existing `al_state._dispatch` calls `require_dispatch()`, acquires `locked(sid)`, then calls `cmd_writer`.
2. `_cmd_writer` loads guarded state, requires the named writer and `state == ACTIVE`, sets:
   - success → `FINISHED`;
   - failed/abandoned → `UNRESOLVED`.
3. Preserve `finish_contribution` completely:
   - validate admitted contribution artifact, writer ID and ownership;
   - `_capture` validates historical retained content and conflict-stage blobs;
   - `observe(retain_content=True)` repeats source observation, retains exact content, then reobserves;
   - publish contribution artifact and append its reference;
   - `al_store.save(sid, st)` performs existing guarded publication.
4. Route every Git call through existing authenticated helpers. Actual finish path requires index/tree/blob/untracked forms through `S.records`, plus existing bootstrap/identity forms.
5. Preserve all instability, missing-content and artifact-validation refusals. Do not substitute an empty contribution or skip attribution to make release succeed.
6. `al_gitlinks.observe` can inspect populated child repositories. Existing `Client.git(root=child)` rejects a root different from the bound repository: retain that refusal for this minimal profile. Do not widen child-repository authority or silently omit observations.
7. Progress reporting remains synchronous fixed-label stderr output; it is not a completion signal.

data flow: Observed contribution artifacts can contain provenance despite outstanding producer effects. Their existence does not establish stable inputs, qualified producer closure, verification or acceptance.

failure / error behaviour: Artifact snapshots may exist before state publication; they do not establish saved finish. Save failure leaves no inferred business success. Later readers must still apply unresolved-liability fences.

## `al/installer/runtime_retry_control.py`, `harness/policy/control_channel.py`, source-closure manifests (CORE)
purpose: Reuse the sealed transport without conflating its two qualified methods.

signatures: Extend existing `activate(...)`, `Client.request(...)`, `ControlChannel.request(...)` and qualification binding with a finite authenticated profile discriminator.

control flow:
1. Replace retry-only literal selection with exactly two audited alternatives: existing retry and exact writer finish. Reject extra flags, abbreviated options, reordered/duplicated operands and other writer actions.
2. Bind profile and exact argv at both launcher and pin activation. A retry channel cannot activate finish or vice versa.
3. Retain interpreter exec permits, closed imports, spawn audit refusal, repository-bound fixed Git grammar, sanitized environment/configuration and helper bounds.
4. Helpers remain independently owned and durably linked to this attempt; no controller-lock acquisition from the broker.
5. Completion remains distinct from business save:
   - protected command and launcher postchecks finish;
   - broker verifies every helper closed and prepares method-bound receipt;
   - supervisor observes matching main exit;
   - only then settle this invocation.
6. Interruption, unknown helper state or missing final receipt retains this invocation’s liability. Main exit, AL `FINISHED`, or a contribution artifact alone cannot close it.
7. Update qualified source hashes for both platform trees and installer closure through existing packaging. Never modify an existing pin.

edge cases: Foreground response loss and background polling use identical settlement rules. Repeating finish after a successful save still encounters the existing non-ACTIVE refusal; it must not replay contribution publication.

## Permission-release fallback (CORE)
purpose: Keep revocation possible without launching an unqualified command.

control flow:
1. If qualification fails, retain the existing authorized in-process permission-revocation route identified by the implementation owner.
2. That route revokes only current principal authority using existing fencing.
3. Report AL writer finish as deferred/unsupported; do not mark AL state `FINISHED`, manufacture contribution evidence, reconcile processes or clear resource claims.
4. The inspected operations/assignments files do not expose a named standalone revoke function; do not fabricate one or reinterpret `release_for_replacement` as unconditional revocation.

TESTS_NEEDED
- Both-platform protected writer-finish tests: success/failed/abandoned preserve exact state and contribution semantics; unknown/non-ACTIVE writer and extra arguments refuse.
- Qualification tests: separate retry/finish activation and receipt bindings; old pins, unsafe Git configuration, unwrapped spawn and child-repository scope refuse without fallback.
- Attribution tests: actual changed content, conflict-stage blobs, unstable observation and corrupt historical artifacts preserve existing behavior.
- Policy tests: exhausted budgets still permit qualified release; earlier unresolved producer claims continue blocking overlapping mutations and dependencies after successful finish.
- Lifecycle tests: foreground/background save success with response loss; interruption before save, after artifact retention and after save; missing receipt and unknown helper retain liability.
- Revocation tests: unsupported finish still allows existing in-process permission revocation without altering AL provenance or prior liabilities.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Tests and source-closure packaging wait for the method-binding and settlement changes.

OPEN_QUESTIONS
- []