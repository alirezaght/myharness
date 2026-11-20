PSEUDOCODE

## Qualified method and ownership (CORE)
purpose: Implement `sealed-al-retry-v1` as the sole additional closed-control qualification.
signatures: New private `RetryControlContext`, typed Git helper client/broker and completion receipt; preserve `cmd_retry`, `apply_checkpoint`, gate refresh and candidate validation.
inputs / outputs: Authenticated selected runtime plus exact retry request → governed retry and separately proven execution closure.
state changes: Existing retry state/artifacts, durable helper lifecycle records and final control receipt.
control flow:
  1. Permit only `retry ROLE --reason TEXT`; retain existing role/reason validation.
  2. Bind exact managed launcher, interpreter, selected pin/manifest, imported AL/installer closure and Git executable identity.
  3. Qualify preparation outside SQLite transactions. Admission rechecks prepared immutable identities in its short transaction.
  4. Enable the profile only through an inherited authenticated private channel. Environment variables alone cannot enable or authorize it.
  5. Keep ordinary execution unchanged when the private context is absent.
failure / error behaviour: Unsupported executable/configuration/runtime refuses before retry effects. No automatic pin upgrade.
integration points: Existing Engineer ownership plus narrow additions in:
  - `al/installer/runtime_run.py`
  - `al/installer/runtime_pin_boot.py`
  - `al/installer/validator_origin.py`
  - one private installer helper-transport module.

## Exact callgraph interception (CORE)
purpose: Cover Git execution before and during the retry—not merely after grant consumption.
signatures: New typed client methods corresponding to existing source operations; no generic `run(argv)` remote endpoint.
control flow:
  1. Establish the authenticated context before protected launcher `subject()` invokes `repository_identity()`.
  2. Preserve that context across the selected interpreter’s existing `exec` and pin bootstrap. Validate it again against the protected transaction.
  3. Route these actual call sites through the private helper only when qualified:
       `validator_origin.repository_identity`;
       `al_store._resolve_repo_root`;
       `al_scope.git_bytes` and indexed-blob batch;
       `al_git_cache._object_binding` and `blobs`;
       `al_gitlinks.git` and `long_tree`;
       `al_repository_facts.git_records`.
  4. Preserve existing parsing, object-hash verification, scope fingerprinting, gate currency and refresh behavior.
  5. Add a private-context spawn guard so an overlooked direct `subprocess`/shell launch refuses rather than escaping helper tracking. This is a completeness guard over trusted sealed code, not an OS sandbox claim.
edge cases: Interpreter provenance observation uses kernel interfaces in `execution_process.observe`; inspect any conditional bootstrap probes and either explicitly qualify their fixed executable protocol or refuse them. Do not introduce a blanket Python subprocess exemption.

## Closed Git forms and configuration (CORE)
purpose: Keep repository-controlled material from selecting executable behavior.
signatures: Proposed typed methods:
`repository_identity`, `resolve_revision`, `merge_base`, `list_index`, `list_tree`, `list_untracked`, `status`, `diff_paths`, `read_blobs`.
inputs / outputs: Validated repository/reference/path/OID operands → bounded bytes or verified blob frames.
control flow:
  1. Broker constructs argv from method-specific templates corresponding to the inspected calls:
       `rev-parse` identity/object-format/current-revision forms;
       `merge-base` with validated revisions;
       `ls-files` index/untracked NUL forms;
       `ls-tree` bounded recursive/NUL inventory forms;
       `status --porcelain --ignore-submodules=all`;
       `diff` path-list forms used by currency/candidate logic;
       `cat-file --batch` with validated hexadecimal object IDs.
  2. Freeze the exact finite flag alternatives found at those call sites in the method version. Reject any additional option rather than forwarding it.
  3. Use an authenticated absolute Git executable; never PATH resolution. Recheck its identity before each helper launch.
  4. Build a fresh environment. Remove inherited Git configuration injection, executable/helper selection, tracing, pager, askpass, loader and Python startup variables.
  5. Disable system/global configuration, interactive prompting and paging. Force hooks/fsmonitor off, external diff/textconv off, automatic maintenance/GC off, and lazy fetching off only where the qualified executable supports those semantics.
  6. Inspect repository/common-directory/worktree configuration through a bounded nonexecuting reader. Refuse unsupported includes, filters, promisor/partial-clone behavior, helper selection or other features capable of external execution. Do not silently change repository semantics.
  7. Bind configuration/metadata identities and revalidate before use. Missing objects fail locally; never fetch to repair them.
data flow: Repository files remain data. Gate discovery may describe checks/hooks but cannot execute them through this profile.
failure / error behaviour: Unknown Git version/feature behavior is unsupported until qualified. Existing semantic checks remain mandatory.
integration points: Git documents system/global configuration overrides and lazy-fetch controls; executable-version qualification is essential because fsmonitor interpretation differs across versions. [Git invocation documentation](https://git-scm.com/docs/git), [Git configuration documentation](https://git-scm.com/docs/git-config).

## Harness-owned bounded helper execution (CORE)
purpose: Keep helper ownership outside the potentially killed AL parent.
signatures: New private broker methods `request(method, operands)`, `cancel_helpers()`, `completion_snapshot()`.
state changes: Durable helper-intent, exact launched-process identity and terminal cleanup records linked to the policy attempt.
control flow:
  1. Broker runs independently of `OperationController.lock` and the AL state lock.
  2. Authenticate channel nonce, sequence, method and prebound repository/runtime. Reject extra fields, descriptors and arbitrary cwd/argv.
  3. Persist helper launch intent before spawning.
  4. Spawn the fixed executable through existing owned-process machinery with exact identity receipt, closed environment and bounded input/output.
  5. Bound total helpers, total bytes, object bytes and per-helper wait. These operational bounds do not establish general strict-time enforcement.
  6. For blob batches, broker owns pipes and validates frame length/OID/type; return bounded chunks or bounded verified results. Do not expose a reusable process handle to AL.
  7. On AL disconnect/parent termination, stop accepting requests and clean up every owned helper. Unknown cleanup retains liability.
  8. Never hold SQLite transactions while waiting for processes or transferring payloads.
edge cases: If the Harness supervisor crashes between spawn and identity publication, retain ambiguous launch intent. No PID-disappearance reconciliation.

## Retry grant, save and completion (CORE)
purpose: Preserve three independent facts.
control flow:
  1. AL retains its existing state lock and all preconditions.
  2. Existing `RetryChannel.claim()` performs the one-use accounting transition.
  3. `apply_checkpoint()` still performs gate refresh/current-candidate checks through qualified helpers.
  4. Existing `al_store.save()` records retry history plus exact attempt/grant references.
  5. After AL command returns, complete protected launcher postchecks before requesting a final qualified-control completion receipt.
  6. Broker closes request admission and verifies every helper terminal/closed; supervisor independently observes main-process exit.
  7. Persist a normal-completion receipt bound to:
       method version;
       policy attempt;
       runtime/interpreter/Git identities;
       helper inventory and closure receipts;
       AL result classification;
       exact saved retry reference, when present.
  8. Close resource claims only when this execution-boundary predicate holds.
failure / error behaviour:
  - Grant consumed, no save: charged failure/uncertainty, never automatic replay.
  - Saved retry, lost response: reconcile exact history; do not repeat.
  - Refreshed artifact, no save: retained partial effect, not retry success.
  - Main exit without helper completion: unresolved.
  - Qualified normal refusal can close execution claims without claiming business success.
  - Supervisor interruption or unknown helper closure retains liabilities.
integration points: Foreground and background paths use the same broker and completion predicate. No qualification extends to arbitrary commands, registered checks or native generation.

TESTS_NEEDED
- Both platforms: actual protected retry, grant consumption, checkpoint recovery, gate refresh and saved history.
- Foreign pin/import/interpreter/Git identity and PATH-shadowed Git rejection.
- Config/environment injection, hooks, filters, fsmonitor, partial-clone/missing-object and maintenance canaries: no unapproved executable/network effect.
- Full callgraph coverage, including bootstrap Git, inventory streams and blob batches; unwrapped spawn fails closed.
- Helper output/object/count limits, timeout, malformed batch and AL parent kill.
- Crash before spawn, after spawn/before identity publication, after grant, before save and after save.
- Foreground/background normal completion and response-loss reconciliation.
- Main exit alone cannot close claims; exact completed helper inventory can.
- Legacy private-context-absent behavior unchanged.
- No generic Git RPC, global install, credential access or permission widening.

EARLY_SUPPORT_SAFE
- none

DEFERRED_SUPPORT
- Qualification tests depend on finalized method grammar, executable receipt and completion schema.
- Snapshot/package parity checks depend on the complete new closure.

OPEN_QUESTIONS
- []