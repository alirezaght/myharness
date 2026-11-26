# Context reuse and execution recovery

Harness and standalone AL classify the remaining implementation risk after
clarification and source inspection. Local established-pattern changes use SMALL;
ordinary features use STANDARD. COMPLEX requires a specific difficult new design
problem and a useful role for pseudocode. Explicit developer choices remain
authoritative. Repository size, security keywords and test volume do not determine
complexity.

Repository investigation follows manifest/package and explicit-path ownership,
literal relative imports and applicable nested AGENTS.md. Keyword matches remain
inspection leads. Unknown ownership produces a scope question. Secret contents
are never scanned; unrelated environment files do not produce task-specific
config questions. Standalone AL exposes the same bounded read-only investigation
with `routing-audit --request "APPROVED REQUEST"`; Harness exposes
`harness:repository-audit`. Main divergence uses local refs without fetching;
remote freshness stays unverified.

Use `harness classify TASK --assessment risk.json --repo REPOSITORY` to record
current source evidence after material clarification. The closed risk input and
examples are in [routing-risk.md](../al/codex/skills/al/reference/routing-risk.md).
The installed host instructions also include both input formats directly, and
`harness classify --assessment-schema` prints the closed risk format (JSON Schema,
notes and an example) from the installed package.

## Reviewed context and complete proposals

`harness context import TASK --file context.json` imports supplied sources and
proposed answers/design/tests with provenance. It creates pending decisions,
preserves catalogue keys to avoid duplicate questions, and retains typed
ticket/criterion/invariant identities. Repeating the same import is idempotent;
conflicting answers or invalid source quotes roll back the entire import.
`harness context import --schema` prints the closed format below (JSON Schema,
notes and an example) from the installed package.

The closed input has `schema_version: 1`, `sources`, `answers`, `design`, `tests`
and `requirements`. Sources contain `id`, `kind` (ticket/audit/user/spec/planning),
`content`, its raw hexadecimal `sha256`, and `provenance`. Each imported item names
a `source` ID and an exact source `quote`. Answers add `id`, `topic`, `question`,
`answer`, `catalogue_key`. Design items add `id`, `section`, `statement`,
`delegated`. Tests add `id`, `category`, `statement`, `covers`, `mock`, `mock_reason`;
covers use `Q:<answer-id>` and `E:<design-id>`. Requirements retain optional supplied
ticket identity/content and criterion/invariant rows with original IDs, text and
source provenance. Acceptance carries those identities into new execution
handoffs. Legacy specs fall back to AC-01 only when no explicit AC IDs exist.

```sh
harness review-package export TASK --output review.json
harness review-package accept TASK --sha256 sha256:HASH --source 'Exact user approval provenance'
```

The export contains all proposed decisions, design, test contents, constraints and
prior decisions. Draft content is visibly UNAPPROVED. Batch acceptance requires
the exact current hash and explicit user approval; it records individual
decisions and exact test-plan approval in one transaction. Stale content, flagged
test boundaries or failed steps roll back all decisions. Final completeness
review and specification approval remain mandatory separate steps.

## Readiness and ignored dependency hardlinks

```sh
harness execution readiness TASK --host codex --repo REPO
harness dependencies inspect TASK --repo REPO --scopes node_modules --output layout.json
harness dependencies reconcile TASK --repo REPO --file layout.json --sha256 sha256:HASH
```

Readiness works before approval and checks the native executable/version,
installed AL protocol, transitive mandatory skill inputs and dependency layout.
It performs no provider login/request or product acceptance.

Native executables are admitted by tested release line, not exact patch: Claude
`>=2.1.310,<2.2.0`; Codex `>=0.164.0,<0.166.0` or `>=0.170.0,<0.171.0`
(`SUPPORTED_NATIVE_RANGES` in `harness/native_tool_policy.py`). Auto-updated
patches keep their line's restrictions, which are still verified when the profile
is built (Codex 0.170 runs an offline `features list` probe). A version outside
these ranges is BLOCKED with the supported ranges named. To proceed anyway, pass
`--accept-unverified-host-version` to `harness execution readiness` or
`harness execution start`; start records the override (exact version and binary
digest) as `host-version-override.json` beside the execution handoff and as an
`execution.host_version_override` workspace event. A later binary change blocks
again.

The dependency scan discovers every `node_modules` and `.venv` directory and
observes up to 2,000,000 entries (bounding observation time only; set
`HARNESS_DEPENDENCY_SCAN_LIMIT` to raise it). Tracked/ignored classification
uses one Git call per inspection, not one per file. Every BLOCKED
`dependency-layout` item names its exact next commands in `next` (and in
`reason`): for ignored hardlinks, `harness dependencies inspect` for the affected
scopes, `harness dependencies reconcile` with the hash `inspect` printed, then
`harness execution readiness` again, which re-observes the layout and passes once
the copies are private. When the scan budget is exhausted, it names the readiness
and start commands with a doubled `HARNESS_DEPENDENCY_SCAN_LIMIT`; more than
10,000 hardlinks in one plan names the scope to inspect and reconcile first.

Reconciliation requires the exact current plan hash and settled execution
attempts. It replaces only ignored hardlinks with private byte-identical copies,
preserves file mode and source/Git state, records original device/inode/link/hash
provenance and restores originals on failure. Tracked files, symlinks,
nonignored files and stale plans are refused. The source guard remains enforced.

## Existing bridge and report recovery

`harness execution upgrade-bridge TASK EXECUTION --reason 'Tool-maintenance reason'`
validates current approval, settled attempts/writers/checks and the protected AL
origin. It selects a separately verified versioned bridge with an immutable
receipt and ledger event. Failed publication restores the previous selection.
Old snapshots, reports, AL pin/run, native session and proof history stay intact.
Resume is separate and explicit. Identical bridge candidates return Current.

Native agents enumerate `harness:skill/index`, then read `harness:skill` and
`harness:skill/reference/...` with bounded offset/limit cursors and content hashes.
Repository confinement stays enforced. Global installation does not upgrade the
commands or policy of old immutable AL pins.

Execution status includes `reports`: verified named-output metadata (`owner`,
`name`, `artifact_id`, `sha256`, `size`, `role`, `resource`). Status never rewrites
the artifact index or exposes report content/reasoning. Retrieve report content
through its hash-bound artifact resource.

## Verified standalone planning reuse

`alctl planning-proof --repository REPO --platform HOST --session SESSION --run RUN`
independently validates the exact standalone planning occurrence, original pin,
Architect packet/report/obligations and current source. Changed/unsupported
policy, source drift, foreign identities, incomplete planning and uncertain
operations are refused.

`harness execution adopt-planning TASK EXECUTION --file request.json --sha256
sha256:HASH --source 'Exact user approval provenance'` requires a closed request
with `schema_version: 1`, `source`, `target`, `mapping`, and a substantive `reason`.
Source contains explicit `repository`, `platform`, `session_id`, `run_id`, raw
hexadecimal `task_sha256`, and `report_sha256`. Target contains `execution_id` and
`handoff_hash`. Mapping contains `criteria` and `invariants` objects mapping every
target ID to an existing source ID. Repository and HEAD must match the approved
target candidate. A fresh protected proof is required even on replay.

The immutable receipt is exposed through `harness:planning-adoptions`. The current
Harness Architect must inspect it, revalidate current requirements/source and
author current planning proof. Old approval, admission, evidence, task state and
native identity are never transferred. Neither workflow is restarted.

Malformed AL integration inputs return a structured `input_schema` diagnostic
with operation, missing/unexpected fields and recovery reference without saving
reporting state. The pinned `reference/schemas/blocker.json` documents blocker
input.

## Supervised native completion

Claude profiles include a Stop hook that reads `harness:pending-work` through the
existing attested supervisor MCP channel. It asks the supervisor to continue while
owned commands run, setup is queued, or terminal command results remain uncollected.
This resource contains handles and lifecycle status, never command output. Child
roles cannot inspect other agents' pending work. Only an owning principal's final
`poll_process` page acknowledges a command result; cancellation still requires
collecting its terminal outcome. Stopping a turn never constitutes AL completion.

Provider errors and user interruption retain their normal exit and cleanup paths.
The hook does not grant execution authority, bypass an AL gate, retry a provider
failure, or prevent an operator from cancelling the execution. Claude's own
consecutive-stop-continuation cap remains enabled; normal polling tool calls reset
that counter. An unresponsive or incapable native agent can still terminate and
must be reported as interrupted rather than completed.


## Sealed-control source inventory maintenance

Sealed retry and writer-finish profiles authorize exact reviewed AL executable
bytes. Their build-owned indexes are checked in root CI and by the AL fast/release
consistency guard. Source correspondence is a prerequisite, not native qualification.
Runtime, installer and ordinary build paths never regenerate these indexes.

Run the offline, read-only check from the repository:

```sh
python3 scripts/control_source_inventory.py
```

A mismatch names changed, missing or uninspected executable sources. Review each
actual changed/new source and its security consequences before proposing new hashes.
After that review, explicitly render a deterministic candidate to a **new directory
outside the repository**, whose parent already exists:

```sh
python3 scripts/control_source_inventory.py --candidate-output /private/tmp/control-source-candidate
```

Rendering leaves live indexes unchanged and grants no source approval or runtime
qualification. Independently review candidate-to-source correspondence and the full
source delta before promoting reviewed maps through a separate reviewed change.
Then run the inventory tests and required AL fast checks, followed by actual native
sealed-control positive flows and intended negative refusals, retaining exact source,
interpreter, SQLite, Git, process-ownership and cleanup evidence. A hash refresh alone
must never be used to turn a qualification refusal into permission.

The provisional `.github/workflows/macos-control-qualification.yml` qualifies its
exact Apple Git-155 candidate. Local Git-157 observations are separate evidence and
do not establish that workflow candidate or release support.
