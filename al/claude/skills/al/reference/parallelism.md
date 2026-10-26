# AL parallelism — the decision procedure

## The rule

> **Parallelize only independent work with non-overlapping write ownership AND
> stable interface boundaries. When dependency, interface stability, repository
> state, or correctness is uncertain, execute sequentially.**

AL never prioritises speed over repository integrity. Sequential is the default
and the fallback; parallel is an exception AL has to earn. Do not force
parallelism, and never parallelize just because two tasks have different labels.

**Non-overlapping files are NOT sufficient.** Parallel implementation is allowed
only when ALL SIX hold:

1. the tasks have no direct dependency,
2. they have no producer/consumer relationship,
3. neither depends on an interface another agent is changing,
4. their expected writes do not overlap,
5. they are not likely to modify tightly coupled regions indirectly,
6. their commands cannot mutate shared state in conflicting ways.

The canonical unsafe case: AL-Engineer changes the `TokenService` interface while
AL-Builder writes the controller that consumes `TokenService`. Different files,
still unsafe — the Builder is coding against an interface that does not exist
yet. Run it sequentially.

## Parallel readers, cautious writers

Parallel READ-ONLY investigation is encouraged for large tasks (§A, §D).
Parallel WRITERS are the exception and need all six conditions above.

Good parallel work — Engineer on the core algorithm in one stable component,
Builder on independent fixtures, test data, mock utilities, unrelated test
scaffolding.

Bad parallel work, all of it sequential instead:

| Engineer | Builder | Why it fails |
|---|---|---|
| changes a service contract | implements consumers of that contract | unstable interface |
| runs dependency generation | runs tests against the regenerated files | shared generated state |
| changes the database schema | writes integration code against the unfinished schema | unstable schema |
| edits a module | reformats or "tidies" the same directory | write overlap |

When in doubt, run sequentially.

## Shared working-tree side effects

Two agents can collide without sharing a single source file. Before allowing a
parallel phase, look at the **commands** each side will run, not only the files:

package installation · lockfile changes · code generation · repository-wide
formatting or `--fix` · database migrations · dependency updates · generated
files · build output · test snapshot updates · any git operation.

Any of those in either side's plan ⇒ sequential. `"${STATE[@]}" ownership` checks this
mechanically from `--engineer-commands` / `--builder-commands`, and its exit 5 is
a decision, not advice. Pass the commands each agent is actually expected to run
(the plan's `SHARED_STATE_COMMANDS` plus the verification each side will scope to
itself); a command AL does not declare is a command the guard cannot catch.

Repository base and candidate gates execute in disposable checkouts, not the
working candidate. Their materializer rejects unsafe symlinks and `.git` paths
before a gate can launch; a rejected checkout is infrastructure failure, not a
passing or failing execution of the gate command. See `gates.md` for the
containment and remote-response rules.

## Overlapping a fix with a running check

Not a parallel writer phase, and narrower: while a verification command runs, a
small fix may be written only when `"${STATE[@]}" overlap --area <running check>
--fix-files <files> --fix-commands <commands>` exits 0 — the fix lies outside the
running check's whole scope (code, fixtures, contracts, dependency manifests,
runtime config), runs no shared-state command, and the phase is not a barrier.
Ownership, artifacts and fixtures must be demonstrably independent; filenames
alone never are. Exit 5 ⇒ wait for the check. Even an allowed overlap is
re-checked: the ledger marks the run TORN if its scope moved while it ran.

## What Claude Code actually gives us (verified against 2.1.266)

- **Concurrent subagents: yes, natively.** Several `Agent` tool calls emitted in
  ONE assistant message run at the same time; each returns its own result. AL
  needs no scheduler, no polling and no custom monitor — the CLI already shows
  running subagents in the transcript, `/tasks` and the status line.
- **Continuing an agent: yes.** `SendMessage` to a spawned agent's id/name
  resumes it with its context intact — this is how the Builder's finalization
  reuses the instance that did the early work.
- **Stopping an agent: yes** (`interrupt_agent`), but AL does not use it on a writer:
  killing an agent mid-edit can leave a half-applied change with nobody able to
  describe it. AL waits instead.
- **Protection against two agents writing one file: NONE.** The only guard is
  per-agent staleness — "File has been modified since read, either by the user
  or by a linter. Read it again before attempting to write it." That check is
  scoped to a single agent's own read timestamps and is cleared by re-reading,
  so the retry can still overwrite a sibling's concurrent edit. There is no
  cross-agent lock, no ownership registry and no conflict detection.
  **Therefore write ownership must be settled before the agents start.**
- **Native isolation (`isolation: "worktree"`): available, re-checked, and
  deliberately NOT used by AL.** The Agent tool accepts it and gives the agent
  its own git worktree, "auto-cleaned if unchanged"; `EnterWorktree` does the
  same for a session. Neither provides a merge-back, and the worktree base is
  governed by `worktree.baseRef`, whose default (`fresh`) branches from
  `origin/<default-branch>`. For AL that is strictly worse:
  1. Nothing lands in the user's tree, so AL would have to invent merge
     machinery to reunite the Engineer's core with the Builder's tests — exactly
     the complexity we are told to avoid, and unreliable on conflicts.
  2. The worktree is checked out from a fresh base, so the user's pre-existing
     uncommitted changes are absent: the agents would build against a base the
     user does not have, and AL could not tell AL's changes from the user's.
  3. AL-Architect reviews `git diff` in the real tree; worktree changes are
     invisible there.
  Shared tree + disjoint write ownership + declared commands is the correct
  mechanism here. Do not build a custom merge system to change that.

## Mechanical guards (a non-zero exit is a decision, not advice)

| Command | Exit | Meaning |
|---|---|---|
| `"${STATE[@]}" ownership --engineer L --builder L [--engineer-commands L --builder-commands L]` | 5 | owned paths overlap, or a declared command mutates shared tree state ⇒ run this phase SEQUENTIALLY |
| `"${STATE[@]}" snapshot pre-review --require pre-verify` | 6 | the tree moved ⇒ writes still landing; wait, re-verify, do NOT review |
| `"${STATE[@]}" retry <agent> --reason ...` | 7 | retry budget spent ⇒ STOP with blockers |
| `"${STATE[@]}" correction --route ...` | 3 | six correction iterations used ⇒ STOP with blockers |
| `"${STATE[@]}" model-unavailable <model>` | 4 | no approved model left ⇒ STOP with blockers |
| `"${STATE[@]}" spec-gate SPEC_BLOCKED` | 8 | the specification is not implementable ⇒ STOP, report the questions |
| `"${STATE[@]}" correction --route ...` | 9 | 0 BLOCKING findings ⇒ no correction loop; approve with notes |

The ownership check treats a directory as every file beneath it, so
`src/auth` ⟷ `src/auth/token.test.ts` is a conflict. It only ever refuses
parallelism; it never authorises it. AL's own judgement still has to clear the
task on dependency and coupling.

## A. Read-only investigation fan-out (Phase 1)

This fan-out happens *inside* planning, before the classification exists, so
judge it from the specification: worth it when the change is genuinely
unexplored — it spans two or more packages/services, or touches an area AL has
not seen this session, or the spec names behaviour whose current implementation
must be located first. **Skip it** for a single known file, a copy/i18n change,
a rename, a one-line guard, or anything where dispatching workers costs more
than reading the file. Anything that will plainly classify SMALL does not need
scouts.

2–5 workers, one narrow brief each, drawn from: current implementation of the
feature · existing tests for the area · integration points and callers ·
patterns and conventions to follow · relevant configuration/flags/env ·
persistence and migration behaviour · error and failure handling · similar
implementations elsewhere in the repository.

Non-negotiable: `al-scout` only (no Edit/Write/Bash tool at all), briefs must be
independent, and the reports are leads that go to AL-Architect. AL does not
synthesize them, does not plan from them, and does not skip the Architect.

## B. Implementation split (Phase 3)

Parallel writers are for COMPLEX tasks. On a SMALL path there is one implementer
and nothing to split; on a STANDARD path the Builder normally runs after the
Engineer, at the finalization barrier.

Run AL-Engineer and AL-Builder concurrently only when EVERY line holds:

1. The plan says `PARALLEL_SAFE: YES`, and the six conditions of the core rule
   hold on AL's own reading too.
2. The early set is non-empty. With a Designer it is the **intersection** of the
   plan's `BUILDER_EARLY_WORK` and the Designer's `EARLY_SUPPORT_SAFE` — if
   either omits an item, it waits. Without a Designer (SMALL/STANDARD) there is
   no second opinion on early safety, so AL runs the phase sequentially unless
   the early set is unambiguously standalone.
3. `SHARED_FILES` is empty and `"${STATE[@]}" ownership` exits 0 — including the
   `--engineer-commands` / `--builder-commands` shared-state check.
4. The early work is worth a parallel round (more than a couple of trivial files).
5. Nothing in the early set needs a symbol, signature or behaviour the Engineer
   has not written yet, and the plan's `INTERFACE_STABILITY` names no interface
   the early set consumes.
6. Neither side will run a shared-state command (install, codegen, migration,
   formatter/--fix, snapshot update, git, shared build).

Safe early Builder work: test fixtures · mocks of existing interfaces · static
test data · test scaffolding and acceptance-test skeletons · independent
regression setup · straightforward DTOs · schemas · isolated configuration ·
independent adapters · boilerplate · non-overlapping supporting files.

Work that waits for the Engineer: tests coupled to exact implementation details ·
tests needing the final exported API · wiring onto new production interfaces ·
cleanup of code the Engineer is editing · integration tests needing completed
core behaviour · type fixes caused by the Engineer's changes · anything in a file
the Engineer owns.

## C. Correction split (Phase 7)

Only BLOCKING findings enter this phase at all. Route by `TYPE`
(CORE/BOTH → Engineer, TESTS_BOILERPLATE → Builder). Then parallelize only when
ALL hold:

1. Every routed finding says `PARALLEL_SAFE: YES`.
2. No finding in one batch appears in another finding's `DEPENDENCIES`.
3. No `TYPE: BOTH` finding is in play (it needs both agents in one region).
4. The union of `EXPECTED_FILES` per batch is disjoint and `"${STATE[@]}" ownership`
   exits 0, commands included.
5. Neither batch needs the other's output — no "test the new API" against an API
   this same iteration introduces.

`TYPE` alone never implies parallel safety: a CORE finding that changes a public
API and a TESTS_BOILERPLATE finding that tests that API are dependent, and run
Engineer → Builder → review.

Otherwise: Engineer first, then Builder with the Engineer's correction summary
and diff. Different classifications alone are NOT a reason to parallelize;
dependency and file ownership come first.

**One reviewer rejection plus its one batch of routed corrections is ONE
correction iteration**, whether the batch ran in parallel or sequentially.
`"${STATE[@]}" correction` is called exactly once per rejection.

## D. Read-only review fan-out (Phase 6)

Optional, and only for a large final review — roughly ≥8 changed files, or ≥400
changed lines, or a diff spanning two or more packages/services. A SMALL task
never qualifies. Up to three
`al-scout` workers in `MODE: ANALYZE`: one on tests, one on security and
concurrency/state, one on integration and regressions. They inspect only, they
may not emit `STATUS:`, and their observations reach AL-Architect as leads to
confirm or dismiss. AL-Architect alone produces `STATUS: APPROVED` or
`STATUS: CHANGES_REQUIRED`. For a small diff, skip them.

## Never parallel

0. The specification validity gate and the task classification.
1. Specification → AL-Architect plan.
2. AL-Architect plan → AL-Designer.
3. `PLAN_CONFLICT` resolution (no implementation agent may start while one is open).
4. Builder finalization relative to the Engineer — it is the barrier.
5. Verification → final review.
6. The final review itself.
7. A new review before every requested correction is complete.

AL-Architect must never review while any agent can still write.

## Failure inside a parallel phase

1. Do **not** treat the phase as succeeded because the sibling succeeded, and
   never record a failed agent as completed.
2. Let the sibling finish. Do not `interrupt_agent` a writer: a killed agent can leave
   a half-applied edit nobody can describe. Only stop the sibling if the user
   asks, or if it is a read-only scout (nothing to corrupt).
3. Record the failure with
   `"${STATE[@]}" agent-failed <agent> --reason "<why>" --peer <peer>
   --peer-status <completed|running|failed|none|unknown> --retry-safe <yes|no|undetermined>`.
   This captures the failure checkpoint and closes the phase occurrence without
   marking it complete. The run is now AGENT_FAILED; `waiting`, `interrupt`,
   `transfer`, and another checkpoint require RUNNING and cannot be layered onto
   that failed state. Preserve the failure until guarded retry or `stopped`.
4. Before retry, reconcile every outstanding writer and check with its own
   reconciliation command and inspected artifact. For a parallel peer, also run
   `"${STATE[@]}" agent-peer-reconcile <peer> --reason "<observed state and inspected changes>"
   --artifact <proof-file>`. A peer-status label alone is not proof. Generic
   `resume` cannot clear a pending failure; use guarded `retry` or `stopped`.
5. Retry at most once, via `"${STATE[@]}" retry <agent> --reason "<why>"` (exit 7 = no
   budget left), and only when the failure is transient, `retry-safe=yes`, the
   requested role matches, the checkpoint still identifies the same task and
   candidate, all operations are reconciled, and peer proof is valid. Retry
   creates a new writer lifetime; never reopen a finished or reconciled writer.
6. When retrying is unsafe, or the retry also fails, re-run the phase
   **sequentially** if the tree is clean enough to continue; otherwise
   `"${STATE[@]}" stopped` and finish with `STATUS: STOPPED_WITH_BLOCKERS`, naming what
   landed and what did not.

## Package-script migration classification

The parsed package-script guard treats exact `migrate`, `migration`, and
`migrations` scripts and their colon-delimited families as shared-state
mutations. It classifies the parsed script position, so an option value, search
pattern, or argument to an unrelated script that merely contains one of these
names remains data. Only the exact `migration:verify` script is the documented
check-only exception. `--check` and `--dry-run` arguments do not waive migration
classification, and other `*:verify` or `*:check` scripts do not inherit the
exception.

