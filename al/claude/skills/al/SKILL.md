---
name: al
description: "AL — Multi-Agent Development Orchestrator. Gates the specification, classifies the task SMALL/STANDARD/COMPLEX, then runs only the stages that task needs: AL-Architect (plan) → AL-Designer (pseudocode, COMPLEX only) → AL-Engineer and/or AL-Builder (parallel only when provably independent) → Builder finalization → verification → AL-Architect fresh review with a bounded correction loop. Use for any implementation task that warrants AL's quality controls; invoke with /al <task> or select it directly."
argument-hint: <complete task specification>
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
- **COMPLEX:** name a specific difficult algorithm, coupled invariant,
  concurrency/transaction protocol or security design. Architect and Designer
  address those difficult parts, followed by Engineer and useful Builder support.
  Explain how pseudocode reduces actual risk.

Reuse agent context. Do not escalate because of file count, repository size,
security keywords, missing tools or an incomplete discovery scan. Resolve routine
engineering details internally. Do not add agents or approval rounds “to be safe.”

## 3. Start through the protected launcher

Use one explicit session and repository for every command:

```sh
STATE=("$HOME/.local/bin/alctl" run --repository "<repo-root>" --platform claude --session "<fresh-id>" --)
"${STATE[@]}" start --package-file <package.json> --review-mode task --target <revision>
```

Whole-MR review explicitly names its target, normally `main`. Do not invoke
`al_state.py` directly, set `AL_RUNTIME_DIR`, edit a pin/state file, reset counters
or transplant an old run into a new runtime. `clear` removes a pointer, not history.
A new installation does not upgrade an existing pin. Use supported recovery for
an existing Harness execution, preserving its approval and requirement lineage.

Record SPEC_READY and the justified classification. Defined scoped implementation
may proceed while planning proof, coverage or tooling is incomplete. Collect
required final proof alongside the work; do not ask the developer to waive it.
Use [the detailed command workflow](reference/workflow.md) only for the operation
being performed, and [gates](reference/gates.md) for task-relevant discovery and
check planning. Preserve actual changed paths and root/shared obligations.

## 4. Implement with the right agents

Use the fixed role/model/effort slots from `show --models` and `show --spawn ROLE`:
Architect Astra/MEDIUM, Designer Astra/LOW, Engineer Sol/MEDIUM, Builder Luna/HIGH.
Only genuine model unavailability enables the documented fallback; never silently
downgrade or invent availability. See the detailed workflow for that failure path.
A role that cannot run is a technical capability problem, not a specification
question; continue other authorized work.

Start the scoped writer before dispatch. Generate its handoff through
`writer handoff --id ID --body-file BODY` after registering the relevant evidence
areas, and deliver the generated prompt intact. Reuse the existing agent for
follow-up. The runtime generates identity/evidence metadata; do not make agents
reconstruct hashes or ask the developer to approve bookkeeping. Include the
original specification and actual plan/pseudocode artifacts, rather than a chain
of summaries. [Handoff templates](reference/handoffs.md) provide each role's
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
Load [review](reference/review.md) at that boundary for versioned report schemas,
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
