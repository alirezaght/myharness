# AL handoff templates

Every prompt starts with the block below, then the mode-specific section.
Replace `<...>` with the verbatim artifact. Required-output headings are the
contract AL validates on return.

**Direct source artifacts, never a summary chain.** No agent may receive only
the previous agent's summary. Every prompt carries the ORIGINAL specification
verbatim, and every stage that needs the plan, the pseudocode, the real diff or
the verification output gets the artifact itself. An Architect-written
`SPEC_SUMMARY` is a reading aid that travels *with* the specification — it never
replaces it. Reviewer findings never become the specification either: they sit
below it in the authority order and are quoted as findings, not requirements.

```
AL WORKFLOW HANDOFF — you are <agent display name>. Read your agent instructions first.
Repository root: <path>. Follow root and nested AGENTS.md/CLAUDE.md.

ORIGINAL USER SPECIFICATION (immutable, highest authority):
<<<SPEC
<verbatim $ARGUMENTS>
SPEC>>>

TASK CLASSIFICATION: <SMALL | STANDARD | COMPLEX> · PATH: <the route AL is running>
(omit this line in MODE: PLAN — the Architect decides it there)

PRE-EXISTING WORKING-TREE CHANGES (user's, not AL's — do not touch or revert):
<preexisting_changes list, or "none">

EVIDENCE (writers only — omit for read-only agents):
Run every check you report through the AL ledger, never bare:
  <the run's $STATE launcher prefix> evidence run --area <a> [--tier focused --scope <paths> -- <argv>]
Planned checks: <`$STATE evidence status` table, or "none yet">
Report each check as `E<n> · <area> → PASS | FAIL | EMPTY | INFRA` (the ledger's
status and counts, verbatim) or `<check> → NOT RUN — <why>`. Never retype a count,
never cite a log the ledger did not write, never present a focused run as the
broad suite. A VALID PASS is reused automatically — do not re-run it by hand.
```

The ID in an EVIDENCE block is the immutable AL run ID the supervisor chose for
`$STATE start` and passes to the launcher on every call. Native harness or
conversation IDs are attribution only and must never be passed as `--session`.

Claude's native `start` inspects an existing run only to validate its pointer,
schema, attribution and repository identity; it never resumes that run. A new
implicit start may use a different working directory in the same repository and
publishes a distinct AL ID after saving the new state. Failed publication
restores the previous pointer and preserves the previous run. Explicit commands
against each run remain bound to that run's own working directory.

## Concurrency block (add to EVERY agent that may write while a sibling runs)

Add verbatim to `al-engineer` and `al-builder` whenever the phase was launched
in parallel. Omit it entirely for sequential phases — a lone writer must not be
told a peer exists.

```
PARALLEL EXECUTION — a sibling agent is writing to this SAME working tree right now.
YOU OWN (write only these paths):
- <path>
FORBIDDEN THIS PHASE (owned by <peer display name>; read them if you need to, never write them):
- <path>
UNSTABLE INTERFACES (being changed by <peer display name> right now — do not code against them):
- <interface/signature/schema/event, or "none">
Rules:
- Do not create, edit, move or delete anything outside YOU OWN. New files must sit
  inside a path you own; if you need one elsewhere, report it, do not create it.
- No repository-wide or directory-wide formatter, linter --fix, codemod, or
  `pnpm -w` write command: it would rewrite your peer's files mid-edit.
- Run NO command that mutates shared working-tree state: package install,
  lockfile change, code generation, migration, snapshot update, shared build, or
  any git operation. Report the need instead.
- Re-read a file immediately before editing it. Never `git add`, `git stash`,
  `git checkout`, `git commit`, or revert anything.
- Run only verification scoped to what YOU touched. Do not judge a failure that
  points at your peer's files — report it.
- If the work genuinely requires a FORBIDDEN path, STOP that part of the work and
  report it as `OWNERSHIP_CONFLICT: <path> — <why it is unavoidable>`. AL will
  sequence it. Never edit it "just this once".
```

## al-architect · MODE: PLAN
Add: `MODE: PLAN`, and — when AL ran a read-only investigation fan-out —
`INVESTIGATION_REPORTS (AL-Scout, read-only leads — verify before you rely on
them; they carry no authority): <verbatim reports>` (else the line
`INVESTIGATION_REPORTS: none — AL ran no scouts`).
State: `Answer the specification validity gate FIRST. Then classify the task and
decide whether pseudocode is genuinely needed (COMPLEX always uses the Designer). If you answer DESIGNER_REQUIRED:
NO, your IMPLEMENTATION_PLAN is the implementation guidance and must be concrete
enough to build from.`
Required output: `SPEC_STATUS: SPEC_READY|SPEC_BLOCKED`.
- On `SPEC_BLOCKED`: `BLOCKING_QUESTIONS` (each with `WHY_MATERIAL` and `RECOMMENDED`),
  `WHAT_IS_CLEAR` — and nothing else.
- On `SPEC_READY`: `TASK_CLASSIFICATION`, `CLASSIFICATION_REASON`, `IMPLEMENTER`,
  `IMPLEMENTER_REASON`, `DESIGNER_REQUIRED`, `DESIGNER_REASON`, `SPEC_SUMMARY`,
  `ASSUMPTIONS`, `INVARIANTS`, `IMPLEMENTATION_PLAN` (with `CORE_SCOPE`,
  `SUPPORT_SCOPE`, `INTEGRATION_POINTS`, `EDGE_CASES`, `RISKS`),
  `WRITE_OWNERSHIP` (with `CORE_FILES`, `SUPPORT_FILES`, `SHARED_FILES`),
  `PARALLEL_ASSESSMENT` (with `BUILDER_EARLY_WORK`, `BUILDER_DEFERRED_WORK`,
  `INTERFACE_STABILITY`, `SHARED_STATE_COMMANDS`, `PARALLEL_SAFE`, `REASON`),
  `TEST_PLAN` (with `VERIFICATION_COMMANDS`), `EVIDENCE_PLAN`,
  `RUNTIME_PREREQUISITES`, `ACCEPTANCE_CRITERIA`.

## al-architect · PLANNING REPORT
After `classify` and `gates plan`, continue the same Architect (SendMessage) with the
output of `$STATE planning packet`: the packet and its `report_template`.
State: `Return the report_template as JSON with every FILL: text replaced and the
example states, transitions, outcomes, failures and scenarios rewritten to describe
this task's observable behavior. Keep every identity, binding and routing field
exactly as given. Set invocation_id to your actual invocation.`
Required output: the completed `architect-planning-report-v1` JSON and nothing else.
The supervisor runs `planning report --file` and `planning admit --file`; a report
still containing `FILL:` is refused.
## al-architect · MODE: RESOLVE_CONFLICT
Add: `MODE: RESOLVE_CONFLICT`, `APPROVED PLAN: <plan>`, `INVARIANTS`,
`ACCEPTANCE_CRITERIA`, and either `PLAN_CONFLICT REPORTED BY AL-DESIGNER:
<verbatim>` or — for a pre-review defect `$STATE rework` sent here (exit 12) —
`DEFECT NEEDING A DECISION: <verbatim CORE_FLAW_REPORTS item or failing check,
with its ledger id and log path>` plus `WHY NOT FAST PATH: <rework's reasons>`.
State: `The conflict may mean your plan was wrong. Say so if it does.`
Required: `PLAN_STATUS: REVISED|UNCHANGED`, `RESOLUTION`.

## al-designer (only when DESIGNER_REQUIRED: YES)
Add: `APPROVED PLAN: <plan>`, `INVARIANTS`, `ACCEPTANCE_CRITERIA`,
`WRITE_OWNERSHIP + PARALLEL_ASSESSMENT (from the plan): <verbatim>`,
`WHY YOU WERE INVOKED: <the Architect's DESIGNER_REASON, verbatim>`,
and for a rerun after a conflict: `CONFLICT RESOLUTION: <architect output>`.
State: `Inspect the real code before writing pseudocode. Cover the parts that
genuinely need it — do not restate the plan. If the plan is incompatible with the
repository, return a single PLAN_CONFLICT line and nothing else.`
Required: `PSEUDOCODE` (+ `TESTS_NEEDED`, `EARLY_SUPPORT_SAFE`,
`DEFERRED_SUPPORT`, `OPEN_QUESTIONS`) **or** a single `PLAN_CONFLICT: <reason>` line.

## al-engineer (implementation)
Add: `APPROVED PLAN`, `INVARIANTS`, `ACCEPTANCE_CRITERIA`, and either
`PSEUDOCODE: <verbatim>` or
`PSEUDOCODE: none — no Designer ran for a <SMALL|STANDARD> task; the APPROVED
PLAN is your implementation guidance`, plus
`YOUR SCOPE: CORE blocks + anything the core needs to function`, and the
**concurrency block** when the phase runs in parallel.
State: `Write the focused tests you need to develop or validate difficult logic;
leave broad coverage, fixtures and wiring to AL-Builder and list them. Never
weaken an existing test.`
Required: `IMPLEMENTATION_SUMMARY`, `CHANGED_FILES`, `DEVIATIONS_FROM_PSEUDOCODE`
(or from the plan when no pseudocode existed), `VERIFICATION_RUN`,
`LEFT_FOR_BUILDER`, `OWNERSHIP_CONFLICTS` (parallel phases only; `none` if
clean), `CONCERNS`.

## al-engineer · REWORK (pre-review defect — fast path or after the Architect)
Prefer `SendMessage` to the Engineer instance that wrote the core. Add:
`REWORK <n> of 2 (this batch)`, `DEFECTS (confirmed; fix all of them in this one
pass): <verbatim CORE_FLAW_REPORTS items / failing checks, each with ledger id and
the failing lines of its log>`, `ARCHITECT RESOLUTION: <verbatim, or "none — fast
path: cause demonstrated, local, interfaces and invariants unchanged">`, and the
current `git diff` of the affected files.
State: `Fix exactly these defects. No redesign, no interface change — if the fix
turns out to need one, stop and say so under CONCERNS. If no repro is recorded
yet (Architect route, unproven cause), record the one the resolution names
before touching code and see it go red. Prove each fix by re-running that repro
command unchanged — it must PASS — plus any focused ledger check it needs.`
Required: the implementation contract.

## al-builder · RESUME (after rework)
Continue the Builder instance. Add: the Engineer's rework summary and diff, and
`AFFECTED SUPPORT WORK: <only the tests/wiring/fixtures the fix touches>`.
State: `Resume only the affected work. Everything else you finished stands.`
Required: the FINALIZATION contract.

## al-builder · MODE: SOLO (SMALL path, Builder is the only implementer)
Add: `MODE: SOLO`, `APPROVED PLAN`, `INVARIANTS`, `ACCEPTANCE_CRITERIA`,
`VERIFICATION_COMMANDS: <from plan>`.
State: `No Engineer ran and there is no core diff: the whole task is yours. The
approved plan is your implementation guidance. Cleanup stays inside this task.
If the work turns out to be correctness-sensitive beyond routine implementation,
report it under CONCERNS instead of pushing through.`
Required: the `MODE: FINALIZATION` contract, with
`EARLY_WORK_RECONCILED: none — no early phase`.

## al-builder · MODE: EARLY (only when running parallel to the Engineer)
Add: `MODE: EARLY`, `APPROVED PLAN`, `INVARIANTS`, `ACCEPTANCE_CRITERIA`,
`PSEUDOCODE`, `EARLY_SUPPORT_SAFE (your whole scope this phase): <verbatim
designer list>`, `DEFERRED_SUPPORT (NOT yours yet — the Engineer's core is still
being written): <verbatim>`, and the **concurrency block**.
Also state: `The core does NOT exist yet. Do not import, call, assert against or
type against symbols the Engineer has not written. Do not run the plan's
VERIFICATION_COMMANDS and do not report a PASS/FAIL verdict for the change —
failures now are expected and meaningless. Build only what stands on its own.`
Required: `EARLY_BUILD_SUMMARY`, `CHANGED_FILES`, `NOT_YET_POSSIBLE`,
`OWNERSHIP_CONFLICTS`, `CONCERNS`.

## al-builder · MODE: FINALIZATION (the barrier)
Runs after any early phase, and on any path where support work is left over.
Add: `MODE: FINALIZATION`, `APPROVED PLAN`, `INVARIANTS`, `ACCEPTANCE_CRITERIA`,
`PSEUDOCODE` (verbatim, or `none — no Designer ran`),
`AL-ENGINEER SUMMARY: <verbatim>`,
`FINAL CORE DIFF: <git diff of the Engineer-owned paths>`,
`CURRENT DIFF STAT: <git diff --stat>`,
`YOUR EARLY WORK (already on disk, from MODE: EARLY): <verbatim EARLY_BUILD_SUMMARY,
or "none — you did not run early">`,
`STILL OWED: <DEFERRED_SUPPORT + NOT_YET_POSSIBLE + LEFT_FOR_BUILDER items>`,
`VERIFICATION_COMMANDS: <from plan>`.
State: `The Engineer is finished and every other agent has stopped. The whole tree
is yours now — no ownership restrictions apply. Reconcile your early work against
the real core, then finish the remaining work and run verification. Cleanup stays
inside this task; report anything you suspect in the core instead of redesigning it.`
Required: `BUILD_SUMMARY`, `CHANGED_FILES`, `VERIFICATION` (ledger ids), `PENDING`, `PRE_EXISTING_FAILURES`,
`CORE_FLAW_REPORTS`, `EARLY_WORK_RECONCILED`, `CONCERNS`.

## al-scout · MODE: INVESTIGATE (Phase 1, read-only fan-out)
Add: `MODE: INVESTIGATE`, `BRIEF: <one narrow question>`.
State: `Read-only. Siblings are running concurrently in this tree; you must not
write anything. Answer only this brief.`
Required: `SCOUT_BRIEF`, `FINDINGS`, `RELEVANT_FILES`, `PATTERNS_AND_CONVENTIONS`,
`GAPS`, `CONFIDENCE`.

## al-scout · MODE: ANALYZE (large-diff review fan-out)
Add: `MODE: ANALYZE`, `ANGLE: <one review angle>`, `ACCEPTANCE_CRITERIA`,
`APPROVED PLAN`, `GIT DIFF: <full diff>`, `VERIFICATION RESULTS`.
State: `Read-only. The tree is frozen — do not write. You may NOT approve or
reject: AL-Architect alone issues STATUS. Report only your angle.`
Required: `SCOUT_ANGLE`, `OBSERVATIONS`, `NOTHING_FOUND`, `COVERAGE`.

## al-architect · MODE: REVIEW (two context-isolated invocations)

The runtime owns stage separation. For fresh review, pass only the frozen fresh
packet from `$STATE review-packet --stage fresh`; it excludes plan, pseudocode,
and implementer summaries. Save a structured report with `$STATE review-fresh
--file <report.json>` before creating the comparison invocation. Start comparison
with `review-compare-start --plan <approved-plan-file>` and add
`--pseudocode <approved-pseudocode-file>` when the route required a Designer.
The compare packet contains typed input references and content. Its report lists
the packet hash and every required plan/pseudocode hash in `inspected_artifacts`;
supplemental artifacts do not substitute for required inputs. The packet is
available only afterward and must be reviewed in a separate invocation with a
distinct ID. Never reuse a conversation or prompt history that exposed the plan
during fresh review.

Both reports include review/stage IDs, unique invocation ID, role/model, task,
candidate and packet hashes, inspected artifact hashes, every criterion with
status/rationale/location/evidence, findings with stable IDs and sources, prior
resolutions with explicit status, and a derived status. Fresh reports carry no
historical findings or resolutions; comparison alone resolves prior blockers,
and only an evidenced `fixed` resolution clears one. Comparison additionally supplies plan defects
and rollout/UI assessments. Link only real artifact locations and valid evidence.
The report is a JSON file passed to the corresponding runtime command; prose
counts and summary-only verdicts are rejected. See `review.md` for the schema and
assessment requirements.

Every packet carries an immutable stage attempt with the approved Architect
model slot and policy digest. The report's required `packet_hash` and `model`
must bind to that attempt; optional echoed `attempt_id`, `effort`, and
`policy_digest` must also match. If the issued model becomes unavailable before
its report is accepted, record unavailability and explicitly replace only that
unsaved packet with `review-packet --stage <fresh|compare>
--replace-unavailable-attempt`. The prior packet is archived and abandoned. Do
not replace a saved report or change a report's model to the run's latest slot.
An accepted fresh attempt remains fixed if the comparison stage later uses a
recorded approved fallback.

Gate manifests declare environment dependencies only through duplicate-free
`env_names` (names only; omitted means empty, and `env` is rejected). Include
selected/anticipated paths, units, and current source hashes. Independent gate
inventory retains earlier paths and discovers applicable repository inputs, so
a replacement manifest cannot erase a known obligation. N/A is appropriate
only when the repository is source-free for that category; unresolved semantics
remain blocking.

The comparison checks the full selected MR from its pinned merge base, all
corrections, earlier unresolved findings, and explicit fix/dispute/defer
rationales. `APPROVED` or `CHANGES_REQUIRED` with zero blockers may advance to
the bounded notes pass or final verification. Any blocker requires correction.

## al-engineer / al-builder (correction)
Add: `CORRECTION ITERATION: <c> of 6`, `REVIEWER FINDINGS FOR YOU (exact, all
SEVERITY: BLOCKING, TYPE <CORE|BOTH> or <TESTS_BOILERPLATE|BOTH>): <verbatim
findings>`, `APPROVED PLAN`, `INVARIANTS`, `ACCEPTANCE_CRITERIA`, `PSEUDOCODE`
(or `none`), the **concurrency block** when the corrections run in parallel, and
for a sequential Builder the Engineer's correction summary plus its diff.
Instruct: fix exactly these findings, nothing else; the ORIGINAL specification
still outranks any finding; dispute a wrong finding under CONCERNS with evidence
instead of applying it. NON_BLOCKING findings are NOT routed and must not be
worked on.
Required: same headings as the agent's implementation/finalization contract (the
Builder answers on the FINALIZATION contract; parallel runs add
`OWNERSHIP_CONFLICTS`). A Builder correcting in parallel with the Engineer runs
only verification scoped to its own findings — AL re-runs the full
VERIFICATION_COMMANDS at the barrier.
