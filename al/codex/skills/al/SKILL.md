---
name: al
description: "AL — Multi-Agent Development Orchestrator. Gates the specification, classifies the task SMALL/STANDARD/COMPLEX, then runs only the stages that task needs: AL-Architect (plan) → AL-Designer (pseudocode, COMPLEX only) → AL-Engineer and/or AL-Builder (parallel only when provably independent) → Builder finalization → verification → AL-Architect fresh review with a bounded correction loop. Use for any implementation task that warrants AL's quality controls; invoke with `$al <task>`."
---

# AL — Engineering collaboration

AL helps clarify the specification, implement the change, test it and independently
review the code. Keep the path proportionate to the remaining design risk.
**Only a material specification ambiguity or consequential engineering choice that
needs the developer pauses affected implementation.** Missing tools, incomplete
coverage, provider failures and internal workflow trouble are operational repair
work. Continue independently defined, authorized work and report pending
verification honestly.

Real execution identity, current epoch/task authority, ownership, secrets, safe
paths and containment remain enforced. A physically unavailable operation gets a
technical diagnostic and repair action; never turn it into a product approval
question or let a native agent grant itself privileges.

If the developer authorizes repairing AL/Harness outside AL, honor that
instruction. Use ordinary scoped implementation, tests and review; do not force
this workflow, ledger or approval rounds around its own repair. Preserve the
history without claiming AL approval. This grants no extra filesystem, native
execution or secret access.

## 1. Understand the task

Preserve the original request verbatim in an immutable task package, together
with accepted requirements, acceptance provenance, constraints and stable
criterion/invariant IDs. A bare “yes” refers to the actual proposal; never invent
its contents. Preserve accepted amendments separately from the original package.

Inspect the relevant code, repository instructions and locally known main
changes. Ask concise questions only about material behavior/scope that the code
and existing conversation do not settle. Do not repeat an approved interview or
ask the developer how to repair AL's tooling. For migrations or Pub/Sub work,
inspect relevant CI timeouts and per-environment rollout/pilot flags; record
missing inputs as investigation and verification work unless a real developer
engineering choice remains.

Read [routing](reference/routing.md) and
[implementation continuity](reference/implementation-continuity.md). Load other
references at the point they are needed; do not read every manual before writing
code. Under Harness, use `harness:skill/index` and bounded
`harness:skill/reference/...` resources instead of assuming host filesystem paths.

## 2. Choose the lightest useful path

Initial complexity is provisional. Reassess after clarification and inspection;
preserve explicit developer choices and the source-backed risk assessment in
[routing-risk](reference/routing-risk.md). Classify the actual change, rather than
the complexity of its surrounding system.

- **SMALL:** established local pattern, clear acceptance. One implementer,
  focused tests and independent Architect review. No Designer, fan-out or Builder
  round just for ceremony. The supervisor can establish the clear spec and brief
  implementation guidance from the interview and code inspection; do not require
  a separate pre-implementation planning round for an already clear local fix.
- **STANDARD:** an ordinary feature. A brief useful Architect plan, Engineer,
  focused tests and independent review. Builder only for real remaining support
  work. No pseudocode when the code and plan already make the implementation clear.
- **COMPLEX:** only with a named hard part — a specific new algorithm, invariant
  or protocol (`classify COMPLEX --hard-part algorithm|invariant|protocol:<name>`).
  Breadth, the number of services or files, and defect counts are never reasons.
  Architect and Designer address that hard part, followed by Engineer and useful
  Builder support. Explain how pseudocode reduces actual risk.

Reuse agent context. Do not escalate because of file count, repository size,
security keywords, missing tools or an incomplete discovery scan. Resolve routine
engineering details internally. Do not add agents or approval rounds “to be safe.”

## 3. Start through the protected launcher

Before starting a run or admitting any writer, check **role-host readiness** in
[the workflow](reference/workflow.md#role-host-readiness-before-writer-admission).
The current host must advertise the named AL role-launch capability needed by
this route. If it is absent, restricted by fork/depth, or UNKNOWN, return the
structured [host handback](reference/handoffs.md#role-host-handback) to the main
capable supervisor. Keep required planning/review pending; do not substitute a
persona, generic agent or model, or build the task while silently omitting them.
This is operational dispatch recovery, not a developer specification question.

Use one explicit session and repository for every command:

```sh
STATE=("$HOME/.local/bin/alctl" run --repository "<repo-root>" --platform codex --session "<fresh-id>" --)
"${STATE[@]}" start --package-file <package.json> --review-mode task --target <revision>
```

Whole-MR review names its target, normally the remote branch such as `origin/main`.
Without `--target`, whole-MR defaults to the upstream of the branch the checkout
was created from when Git records it. `start` warns loudly, and does not refuse,
when the target is a local branch behind its upstream; fix it before review with
`retarget --target <upstream>`, which keeps the spec gate, classification and
evidence and drops only gate plans, review scope and admitted planning. Do not invoke
`al_state.py` directly, set `AL_RUNTIME_DIR`, edit a pin/state file, reset counters
or transplant an old run into a new runtime. `clear` removes a pointer, not history.
A new installation does not upgrade an existing pin. Later commands of a run are served
by a resident per-repository service with identical results, falling back to in-process
execution whenever it is unavailable (`alctl service status|stop`; `AL_SERVICE=0` disables it;
see [performance](reference/performance.md#resident-service)). Use supported recovery for
an existing Harness execution, preserving its approval and requirement lineage.

Record SPEC_READY and the justified classification. Defined scoped implementation
may proceed while planning proof, coverage or tooling is incomplete. Collect
required final proof alongside the work; do not ask the developer to waive it.
`planning admit` records incomplete relevant coverage as verification obligations
instead of refusing. When the Architect revises its own admitted obligations before review,
`planning revise --reason TEXT` archives the admission and reopens planning for the
ordinary packet, report and admit route ([workflow](reference/workflow.md#revise-admitted-planning-obligations)). The first ordinary `writer start` records the PLANNING (or
PSEUDOCODE) to IMPLEMENTATION edge itself; a Designer route records `phase PSEUDOCODE`
first. Refusals on this path name the next supported command. A verification-only
run whose implementation already exists in the worktree walks the same edges without
a writer: `phase IMPLEMENTATION` (after `phase PSEUDOCODE` on a Designer route),
`phase FINALIZATION` when COMPLEX, then `phase VERIFICATION` and `review-start`
(`verify` records these edges itself when no Designer or finalization work is pending).
Final proof, review and approval enforce only coverage gaps that mean AL cannot see or
trust what the change touches, and only where the change reaches (its paths, their units, the
literal dependency closure and the links they consume): a torn capture, a link, gitlink or
opaque boundary, or unreadable governing rules. A check AL cannot establish (no executable
binding, an unmodelable manifest or package script, an unclassified directory, an unsupported
layout, CI inventory or provider, or a package setup AL's own policy cannot qualify) is
unverified: listed in `gates status` (`coverage_notes`, `unverified`) and the agent brief,
never blocking (#157). Gaps outside the change's reach, including units selected only through
dependency uncertainty, are verification notes too. A coverage refusal names each blocking
gap, its repair and the `gates discover --extend current` / `gates plan` commands. An
`ATTRIBUTION_AMBIGUOUS` from `recovery next` names the paths, writers and causes and the
`task reconcile-attribution` steps; staging or committing between writers is no overlap.
Use [the detailed command workflow](reference/workflow.md) only for the operation
being performed, and [gates](reference/gates.md) for task-relevant discovery and
check planning. Preserve actual changed paths and root/shared obligations.
`gates discover` analyses without holding the run lock, so writers and amendments
continue on the admitted plan; add paths with `gates discover --extend current
--paths-file <json>`, and use `--bounded` on large monorepos (see gates). Pass
`--output <file>` to `gates discover` or `gates plan` to write the manifest JSON to a
file and keep stdout to a one-line summary (a run started on AL 0.1.25 or earlier keeps its pinned
runtime, which has no `--output`). Repository analysis is shared per repository
across worktrees and tasks as content-addressed facts, so a repeated discovery or plan
analyses only changed blobs (`AL_ANALYSIS_CACHE=0` disables it; see gates). A CI step whose failure never fails the
pipeline (`allow_failure`, `continue-on-error`, `cmd || true`, a shell function that
swallows the failure) becomes an informational gate, and a candidate failure
identical to its base failure is reported as pre-existing: only new failures block.
A CI job AL cannot run locally (unknown pipeline context, runner image, variables,
services, deploy tools) stays an UNRESOLVED gate the pipeline verifies: it is listed
under `unverified` in `gates status` and the review packet and never blocks review.
Resolve it by planning the gate as its offered verbatim `ci_recipe`, or record the
pipeline's own result with `gates ci-evidence --id <gate> --status <status> --url
<pipeline-or-job-url> [--pipeline-id N --job-id N --commit SHA]` (a reviewer-visible
note, never AL proof).

## 4. Implement with the right agents

Use the fixed role/model/effort slots from `show --models` and `show --spawn ROLE`:
Architect Astra/MEDIUM, Designer Astra/LOW, Engineer Sol/MEDIUM, Builder Luna/MEDIUM, Scout Luna/LOW.
Only genuine model unavailability enables the documented fallback; never silently
downgrade or invent availability. See the detailed workflow for that failure path.
A role that cannot run is a technical capability problem, not a specification
question; continue other authorized work.

Start the scoped writer before dispatch. Generate its handoff through
`writer handoff --id ID --body-file BODY` after registering the relevant evidence
areas, and deliver the generated prompt intact (it carries the repository
`brief` and earlier agents' handoff records). Agents are **warm by default**:
pass `--agent-id` (or run `agent record`) and send corrections, follow-ups and
delta re-reviews to that same instance with the printed resume call; a fresh agent
is the exception, recorded with `--fresh-reason`, for independence. Agents report
`progress`, raise `ask --blocking|--non-blocking` questions mid-run, and end with a
`handoff-record`. The runtime generates identity/evidence metadata; do not make agents
reconstruct hashes or ask the developer to approve bookkeeping. Include the
original specification and actual plan/pseudocode artifacts (by hash plus the
relevant original excerpt or delta when long), rather than a chain of summaries. [Handoff templates](reference/handoffs.md) provide each role's
contract when needed.

Writers preserve other changes and own explicit paths. Parallelize only genuinely
independent work with stable interfaces and disjoint writes; otherwise sequence
it. Read [parallelism](reference/parallelism.md) only when considering concurrent
writers. Read-only agents do not start background tasks, proxies or services, and
must not leave child processes behind.

For Harness, report actual agents/tasks using
[execution integration](reference/execution-integration.md). A planned role is
not a running invocation. Terminal agents need fresh observed invocations.
Current released authored output can unblock dependent implementation without
pretending its producer is verified Completed. Operational issues and actual
specification/developer decisions use separate typed interfaces.

## 5. Test, repair and review

For every test you author, change, consolidate, or review, apply the mandatory
[testing standards](reference/evidence.md#testing-standards). They govern ordinary
feature tests as well as explicit suite cleanup. Read the relevant production behavior
before changing tests, and prove observable outcomes and realistic risks.

Use the intent commands by default ([workflow](reference/workflow.md#intent-commands-the-default-path)):
`writer finish --id ID --outcome success --evidence auto` finishes a writer and runs
the planned evidence its changed paths reach; `verify` runs the missing or stale
evidence and the base and candidate gates, then enters VERIFICATION and starts review,
or refuses naming the blocker and the fine-grained command that resolves it;
`next --do` runs `recovery next`'s suggestion only when it is a safe deterministic
operation; `approved` records the FINAL_VERIFICATION edge. Keep the fine-grained
commands for exceptions and recovery.

Run relevant checks as soon as they are useful, using the generated evidence
commands. Install locked dependencies in isolated checkouts before package-backed
hooks; missing setup is infrastructure failure, while an executed code failure is
FAIL. Preserve failures and audit replacements. Unknown, empty, missing, stale or
unavailable results never count as passes.

Code defects receive focused repair. Tooling/provider/coverage gaps receive
operational diagnostics and repair, with implementation continuing where defined.
Internal reassessment, retry budgets and report-format problems must not become
new developer approval questions or prevent otherwise authorized edits. Preserve
their history and repair the protocol without fabricating credits or evidence.

Obtain independent Architect code review and correct actual in-scope defects.
After a correction `review-start` runs a smaller delta review when safe and says
why when it runs a full one. Load [review](reference/review.md) at that boundary for versioned report schemas,
required context separation and current final-proof checks. Keep code-review
progress separate from pending verification and business acceptance. Do not claim
APPROVED or verified completion until the actual runtime's required evidence,
coverage and review checks pass. Never hide missing proof to make the workflow
look finished.

## 6. Report the useful result

Give concise progress: what was learned or implemented, the remaining technical
uncertainty, and the next action. Report implementation status separately from
verification and developer decisions. Do not paste agent transcripts or hidden
reasoning. A host exit, progress estimate or authored report is not completion.

At the end, explain the behavior changed, relevant test results and material
remaining obligations. Only an actual successful Architect/runtime approval is
AL APPROVED. If checks remain pending, say so while preserving the implementation
already completed. Publishing, committing and installation follow the developer's
actual authorization. Read [distribution](reference/distribution.md) only when
installing or updating AL.
