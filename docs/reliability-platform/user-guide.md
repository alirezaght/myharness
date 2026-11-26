# Reliability views in the dashboard

The dashboard presents two read-only views for a selected execution: **Operation incidents** and **Execution timeline**. They show persisted facts in the selected task, execution, and runtime epoch. They do not decide root cause, verify acceptance, or grant approval.

## Open the dashboard and select a scope

From the repository, start the local dashboard with:

```sh
uv run python -m harness.dashboard [<task-id>] [--db PATH] [--specs PATH] [--project PATH] [--no-browser]
```

Omit the task ID to open the task list. Select a task to open its execution view. Supplying a task ID opens that task directly. The dashboard binds only to `127.0.0.1` on a free port; it prints the URL and normally opens a browser. `--no-browser` prints the URL without opening a browser. `/harness` is the repository shortcut that opens the dashboard for the active task.

In the execution view, open **Execution identifiers** and note the **Execution** and **Runtime epoch** values. The task ID, execution ID, and epoch are separate scope values: use the task ID shown for that task together with the IDs from the selected execution. The views never combine records from another task or epoch. A stale or mismatched scope returns an unavailable/not-found result rather than silently switching executions.

## Operation incidents

The incident section lists persisted denied or failed operation outcomes for the selected scope. Use its category selector and literal text search to narrow the list; **Older incidents** follows the API cursor. Select a row to inspect its safe metadata. An **Outcome not recorded** entry is a still-pending observation, shown separately from confirmed incidents. It does not mean that the operation failed.

Incident records are observations, not root-cause assessments. The current R1 coverage is operation dispatch. Early transport and epoch rejections, assignment waits, historical backfill, and asynchronous completion classification are not covered. The section has no retry, resolution, approval, or activation controls.

## Incident Intelligence investigations and operator decisions

The **Incident Intelligence** section adds scoped history and attributed
interpretation to the existing execution view. Use literal search, category,
source-kind and cursor controls to find persisted incidents. Select an incident
to inspect its immutable observed fact and source provenance separately from
the investigation history. Task scope can include historical incidents from
different executions; execution scope is bound to the displayed execution and
runtime epoch. Choices in evidence pickers are actual incidents in the active
scope, and short incident identifiers appear first so similar entries remain
distinguishable.

The primary editor lets an operator add a hypothesis, confidence, rationale,
supporting or contradicting evidence, and status. It appends an attributed
investigation revision; it does not rewrite the observed fact. Optional
controls, component references, corrections and corrective-action suggestions
are behind **Controls, components, corrections, and suggestions**. Each
optional item must be explicitly selected for addition, so a status-only edit
does not add unrelated actions or control claims. Resolved or dismissed status
and non-unknown hypotheses need the evidence required by the API. A component
marked verified means only that the referenced Knowledge source/version is
present in the selected historical snapshot; it does not verify current code
or prove a cause.

Use **Suggest related symptoms** to create a bounded suggestion, then inspect
its members and source/version differences before accepting, rejecting,
splitting or superseding it. Similar symptoms do not establish a common cause.
These decisions append group history and do not alter incident facts.

Legacy incidents that are visible but unindexed can be projected explicitly
from the full task, execution and epoch scope. Projection is bounded and
reports coverage as of a checkpoint. For a published output, choose an actual
listed report/result and record an operator verdict with a rationale and
explanation. The list contains metadata; submission independently checks the
immutable output. A verdict is historical operator attribution, not approval,
verification, deployment permission or an execution-state change. The local
operator label does not authenticate a named user.

After a stale revision or scope conflict, reload the incident or group before
editing again. The client preserves drafts and selected evidence through list
refreshes, but a task-only scope does not expose execution-bound projection or
output-verdict actions. See the [R5a API contract](r5a-api-contract.md) for
request bounds, cursor fields, accepted actions and remaining limits.

## Execution timeline

The timeline correlates recorded execution facts in descending time order. It can include:

- R1 operation start and return/denial/failure facts, including links to a persisted incident when present;
- owned-process launch, start, launch failure, cancellation request, timeout, exit, or cleanup-uncertainty observations;
- references to semantic agent/task/check/decision/output/run reports that passed the existing ingestion validation; and
- references to authorized Knowledge bundles when the existing operation actually returned one.

Runtime observations and semantic reports are different evidence. A semantic report says what an accepted report recorded; it is not an independently measured runtime event or proof of acceptance. A process exit code does not prove that checks were published, descendants were cleaned up, or work was accepted. The timeline exposes safe references and metadata, not operation bodies, process output, argv, environment, or semantic payloads.

Use **Event kind** and **Authenticated principal ID** to filter. The list initially requests 25 records. **Older timeline entries** follows the paired descending cursor. The client retains a bounded investigation window of at most 100 loaded records as older pages are traversed; when there are newer records, it tells you the window was retained. **Latest timeline entries** resets the window to the newest page. Refreshes keep the selected execution scope and selected process detail when those records remain in the window. Select **View process evidence** on a process row for its scoped lifecycle detail; this is a read-only view.

An execution trace is created at supported new execution reservation. Older executions without a trace remain explicitly marked **Historical correlation unavailable**. A GET does not backfill or create a trace. Coverage version 1 reports support for operation dispatch, owned-process lifecycle, validated semantic links, and authorized retrieval references. Early transport rejection, historical backfill, durable process recovery, and native-child observation are not covered. Historical comparison is currently unqualified.

Durations are reported only when measured with the runtime's monotonic clock. Tokens, cost, and native child-process measurements are unavailable; unavailable values are not zero. A missing field can also mean that the particular observation did not provide it. The dashboard does not infer those values from elapsed wall time, output size, or related events.

Neither section grants approval authority. Use the normal task workflow and its verification/review steps for decisions and acceptance.

## Runtime policy and shared-budget view

The execution view also shows the active policy versions, same-database budget pools, policy decisions, capabilities and (when requested) one attempt's safe metadata. Read these facts for the exact task, execution and runtime epoch. A decision or capability history page contains at most 25 rows; decision history uses `before`, and capability history uses `cap_after`. Capability coverage is explicitly `partial` while another page may remain and becomes `complete` only when the query establishes that no later page exists. Follow each cursor independently. The view does not return policy documents, resource claims, authorization delivery text or tool arguments.

Pool rows expose the latest shared pool maximum and its shared `consumed` and `reserved` counters. This maximum is not a role-specific effective allowance. Each operation also obeys the most restrictive ceiling among its selected immutable policy versions, including explicitly inherited parent versions. A later version may raise the shared row's maximum for operations selecting that version, while a child still pinned to an older parent remains constrained by that parent's ceiling. Effective limits do not rewrite or reset the shared counters. `mutating_operations` and `retry_attempts` are discrete admitted invocations; they do not count changed files, writes, child processes or syscalls. Other usage is `null` when the system cannot measure it. In particular, generation tokens, cost and native-child usage are unavailable, not zero. A capability is bound to its exact execution, epoch, generation, principal or role, operation, resources, policy digest and approved execution binding. Issued and revoked capabilities retain references to their protected authorization decision and action delivery.

Policy accounting is transactional only inside the selected project's existing SQLite database. An explicitly named, execution-tree or workflow-instance pool is shared only within the owner identity recognized in that database; a global policy scope does not create a machine-wide counter. Scope rules intersect with existing Harness authority and parent restrictions. Explicit deny rules win. Network, database, production and native-delegation restrictions are refused where the current runtime cannot enforce them; a policy document is not evidence that those controls exist.

Policy resource-domain enrollment is sticky. Once enrolled, conflicting work is checked against retained read/write/unknown claims even when a budget, role, policy version or capability differs or disappears. Dependency resource claims are established before reading dependency bytes and remain until the owning attempt is proven closed. Unknown or unresolved producers/resources block conflicting work. Live `harness:repository-audit` traverses repository manifests and source and has no qualified coordinated read boundary, so under an applicable policy or enrolled resource domain it is refused before Git/source traversal; it does not bypass claims. Untouched legacy audit may remain uncoordinated only when no policy applies and the repository domain has not been enrolled. Other metadata-only operational reads remain available where their handler permits them. These rules do not turn independent safe diagnostics into mutations or allow status-only cleanup to consume work budget; `uncoordinated_legacy` is not a coverage claim.

Mutation and retry reservations are charged at their irreversible start boundary and are not refunded for failure, no-op, cancellation or a normal refusal after the guarded effect. A reservation may be released only while still reserved with no start permit, or through the explicit fenced `release_unstarted` path that wins its start compare-and-set. Uncertain work remains unresolved and retains claims. Polling, cancellation, result collection and essential reconciliation remain subject to their existing owner and epoch checks; they are not blocked by exhausted work budgets.

`execution_elapsed_ms` uses the captured wall origin and boot/clock identity, adds a same-supervisor monotonic bound, includes downtime on the same boot, and refuses when wall time moves backward or the origin/current boot identity cannot be validated. `operation_deadline_ms` is not a general hard timeout. For ordinary repository `read_file`, the handler checks the existing monotonic bound before descriptor access, after parent traversal and target open, before and after each bounded hash read or `pread` (including EOF), after source-version validation and descriptor cleanup, and after response decoding before returning the page. For `apply_patch`, it checks before subsequent directory creation, deletion, temporary-file writes/flush/fsync and target replacement after potentially blocking work, then before reporting success. Temporary-file and descriptor cleanup remains unconditional; if the target mutation already completed when a later check detects expiry, it is not rolled back and the invocation remains charged. These cooperative checks cannot interrupt an OS syscall already in progress or promise a hard scheduling/latency bound. With no active policy attempt, the deadline helper is a no-op and legacy reads keep their existing behavior. Special `harness:` and `artifact:` resource handlers are separate paths and are not covered by ordinary-file read timing checks. Tokens, cost, provider usage and general native/process deadlines are not qualified by these fields.

### Operator actions and authorization

The local dashboard provides `POST /api/policy/install`, `/api/policy/capability`, `/api/policy/revoke` and `/api/policy/workflow`. These routes consume an existing, exact protected developer decision and its acknowledged delivery in current AL state. The accepted delivery is canonical JSON with `schema_version: 1`, `kind: "runtime-policy-authorization"`, the exact action, exact execution binding and a closed action-specific `document`. Its accepted decision, immutable registry provenance, active execution binding, delivery acknowledgement hash and decision state are checked again before installation, issuance, revocation or workflow registration. A caller-supplied `approved` flag or policy prose grants nothing.

The dashboard listens on loopback and checks `Host`, same-origin `Origin`/`Sec-Fetch-Site`, JSON content type and a single declared `Content-Length` before reading an action body. Policy action bodies are limited to 4096 bytes and must contain exactly `task`, `execution`, `epoch`, `generation`, `decision` and `revision`. These local HTTP checks do not authenticate a named human; authority comes from the already recorded protected developer decision and its AL acknowledgement. Refusals use fixed safe errors. `409` means the action was refused or stale, `413` means the body exceeded its limit, and `503` means policy storage was unavailable.

Revocation blocks later admission and records its own action and decision references. It does not undo a start that already won the admission/start race, remove a persisted effect, refund a use, or close process/resource liabilities. A success or failure receipt closes only the current attempt when its supported closure evidence is complete. Missing receipts, interrupted helper work, absent PIDs, accounting labels and unqualified group observations do not establish closure. Retiring an execution remains fenced while an enrolled attempt or process boundary is unresolved.

Resource classification follows the implemented handlers. Recognized `harness:` metadata endpoints such as tooling and capabilities return status/presence data; preparation uses filesystem presence checks and does not read manifest bytes. Unknown `harness:` names that actually fall through to ordinary file reads are path-validated and classified as file resources. Descriptor-backed `diagnostic` requests of kind `source`, `log` or `config` claim their requested file before reading and retain the claim through response construction. These diagnostic routes are not cleanup exemptions and do not gain a strict deadline qualification. Live repository audit uses a conservative repository-wide dependency claim for preflight and refuses when coordinated policy/enrollment applies. This documents current handler behavior, not an exhaustive guarantee for arbitrary helper routes.

### Qualified sealed AL control methods

Only `sealed-al-retry-v1` and `sealed-al-writer-finish-v1` currently have a qualified execution boundary. Qualification is supported on macOS for the exact inspected immutable pin, complete packaged source closure, launcher, isolated interpreter, repository identity/configuration and fixed Git executable. The accepted Git version output is `git version 2.54.0 (Apple Git-157)`, `git version 2.50.1 (Apple Git-155)`, `git version 2.50.1`, or `git version 2.54.0`; the executable is the resolved `/Library/Developer/CommandLineTools/usr/bin/git`. The repository must use a validated ordinary `.git` directory/configuration; linked worktrees, shallow repositories, alternates, grafts, replacements, promisor packs and unsupported gitlinks refuse. The trusted path starts Python with `-I -S -B`, checks pinned source and runtime identities, uses bounded finite read-only Git helpers, authenticates the private helper channel and prepares separately tested main and helper sandbox profiles. Source-fixed probes must receive `EPERM` or `EACCES` for `os.fork`, `os.forkpty`, `os.posix_spawn`, and `subprocess.Popen` with vfork enabled/disabled, close-fds enabled/disabled, and new-session enabled; any allowed or ambiguous case refuses before the protected method runs.

Retry qualification applies only to the existing AL retry command with its original role/checkpoint/reason and grant safeguards. Writer-finish qualification applies only to the exact currently active writer ID and `success`, `failed` or `abandoned` outcome. A granted capability, an AL state save and a qualified execution-closure receipt are separate facts. A normal refusal may be closed and failed while remaining charged. Missing or interrupted closure proof stays fenced. Writer finish closes only that finish invocation; it does not settle earlier producer work, verify output or authorize acceptance. Old pins are immutable and are never upgraded in place. Unsupported Git configurations, linked worktrees, gitlinks, launcher/pin closures and platforms refuse. These methods do not qualify arbitrary commands, native provider process trees, general strict deadlines, Linux/container execution, or live provider behavior.

## Typed AL and Harness exchanges

Newly generated handoffs and explicit typed outputs carry a closed version-1 companion for one of ten families: requirement analysis, architecture proposal, design decision, implementation plan, task assignment, implementation result, verification result, failure report, escalation request or final delivery. The generated assignment binds the actual writer/task/graph/specification context, immutable inputs and expected output kind. To publish a typed implementation result through standalone AL, submit an explicit contract file with `al artifact output --name result.md --file result.md --contract-file contract.json --writer WRITER_ID`; Harness `submit_output` accepts the corresponding optional `contract` object. Supporting files without a declaration remain free-form legacy artifacts.

The companion describes its source record and exact immutable references. It does not approve a decision, prove a test passed, verify a task, complete a writer, or authorize publication. Existing producer identity, current epoch/revision, dependency graph, protected decision, specialized planning/review/evidence and publication checks still apply. If a required contract is invalid or stale, the affected publication/consumption is refused with a bounded field pointer and repair action; rejected values are not echoed. Normal AL release and attempt-accounting rules still apply.

Implementation-plan companions retain a `contract_observation` snapshot of the exact authored plan. Current consumers accept its captured graph revision only as a historical observation bounded by the current graph; they still validate the immutable report, current specification lineage and current authorization.

A blocker’s original companion and later authorized decision/revalidation transitions are retained in bounded escalation history. For a decision, the Harness bridge returns a version-1 SHA-256 receipt for the exact Registry-validated inbox bytes. AL checks the receipt against one bounded inbox read and uses those same bytes for parsing and snapshotting. See the [R4 API contract](r4-api-contract.md) for the precise receipt fields and limits.

An assignment page can authenticate and return exact immutable prompt/receipt bytes while separately reporting that current mutable source was not checked. This distinction preserves useful presentation for older handoffs that may have an unknown legacy process record. When a downstream operation actually reads mutable dependency source, the existing policy protocol checks the producer receipt and legacy/native predecessors, reserves matching source claims before hashing, retains claims through the consumer effect and revalidates before start. An unresolved predecessor refuses that strict source use; typed status does not settle it. `immutable_snapshot_validated`, `mutable_source_not_checked`, `coordinated_source_consumption` and `legacy_uncoordinated` are coverage facts, not authority or completion statuses.

Current R4 does not qualify every writer-start, task-authored/task-agent operation or outer native process. Missing PID, status labels, a generated prompt or an authored output do not establish that an earlier process stopped. Historical untyped artifacts remain readable under their existing behavior, and an authored but unverified dependency remains usable where the existing authority and source-safety checks allow it. See [the R4 API contract](r4-api-contract.md) for the family map and precise boundaries.

### Automatic dependency setup

When an AL registration discovers local dependency units, `auto_prepare()` performs bounded metadata/path checks and queues concrete setup requests. A `Queued` result is pending work; it is not evidence that lockfiles were read, packages were installed, or tests ran. The outer AL command does not own or execute these setup effects.

The controller processes setup after the ambient Attempt has ended. It revalidates an explicitly retained setup owner or sends a fresh request through the normal `environment_operation` dispatch. The admitted attempt must match the same execution, principal, task and revision before the setup handler reads dependency manifests. Polling or cancellation does not clear unresolved old setup/process liability or demonstrate that an installer stopped. The archived E51 causal red and E59/E60 functional passes are recorded with current currency and later test corrections in [implementation-progress.md](implementation-progress.md); the native and arbitrary installer boundaries remain unqualified.

`WRITER_ID` is the current active writer. `contract.json` must bind the actual current assignment, its acceptance criteria and the exact content of `result.md`.

The standalone command checks the contract against the active assignment and its actual acceptance criteria before publishing or returning an idempotent result. Operational issue revisions preserve the authenticated historical companion and bind a new companion to the current authorized revision. Interrupted-check reconciliation preserves the original terminal observation and records separate versioned reconciliation evidence; a changed status is not itself proof that the check passed. See the [R4 API contract](r4-api-contract.md) for these boundaries and the preserved failure history.
