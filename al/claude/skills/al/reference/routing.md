# AL routing — the spec gate, the classification, and the three paths

Routing is decided ONCE, by AL-Architect, in `MODE: PLAN`. AL executes the
decision; it does not re-derive it and does not add stages "to be safe".

## 1. The specification validity gate

AL-Architect's first output line is `SPEC_STATUS: SPEC_READY` or
`SPEC_STATUS: SPEC_BLOCKED`.

`SPEC_BLOCKED` is for MATERIAL ambiguity only — contradiction, missing required
behaviour, unspecified security behaviour, several fundamentally different
implementations that all satisfy the wording, or a conflict with another
explicit requirement. It is **not** for detail a competent engineer settles from
the codebase.

- `SPEC_READY` ⇒ `"${STATE[@]}" spec-gate SPEC_READY`, print `AL · SPEC_READY`, continue.
- `SPEC_BLOCKED` ⇒ `"${STATE[@]}" spec-gate SPEC_BLOCKED --reason "<short>"` (exit 8).
  Print the Architect's `BLOCKING_QUESTIONS` (each with its `RECOMMENDED` answer)
  and `WHAT_IS_CLEAR` verbatim, finish
  with `STATUS: SPEC_BLOCKED`, and **implement nothing**. Never convert a
  blocking question into an assumption, and never invent a major requirement to
  keep the run moving.

## 2. Task classification

AL-Architect returns `TASK_CLASSIFICATION`, `IMPLEMENTER` and
`DESIGNER_REQUIRED`. AL records them with:

```
"${STATE[@]}" classify <SMALL|STANDARD|COMPLEX> --designer <yes|no> \
       --implementers <al-engineer[,al-builder]> \
       --reason "<classification reason>" --designer-reason "<why / why not>"
```

The command prints the classification, the path, and — when the Designer is
skipped — the reason. A skipped Designer is **expected behaviour, not an error**.

`/al` means "use AL's quality controls". It does not mean "spend every agent on
every task".

## 3. The three paths

### SMALL
`Architect → (Engineer OR Builder) → Verify → Architect review`

For small bug fixes, localised behaviour changes, straightforward refactors,
small API changes, one/few-file changes, well-understood existing patterns,
little algorithmic uncertainty.

- Designer is skipped. No pseudocode is produced.
- ONE implementer, chosen from the work itself: `al-engineer` for a
  reasoning-heavy bug or correctness-sensitive change, `al-builder` for routine
  implementation. `both` only when both are genuinely needed — and then
  sequentially, Engineer first.
- No early parallel Builder work, no finalization barrier unless the implementer
  reports work left over (then FINALIZATION runs, and `"${STATE[@]}" phase` records the
  escalation).
- No read-only fan-outs.

### STANDARD
`Architect → Engineer → Builder if needed → Verify → Architect review`

Normal feature work where planning matters but pseudocode would not materially
improve the implementation.

- Designer is skipped; the Architect's plan is the implementation guidance, and
  it must be concrete enough for that.
- AL-Engineer implements. AL-Builder runs at the finalization barrier only when
  there is real support work (`LEFT_FOR_BUILDER` non-empty, or the plan's
  `SUPPORT_SCOPE` is not trivial). No Builder round for its own sake.
- Parallel writers only in the unusual case where the six conditions in
  `parallelism.md` all hold with no Designer second opinion — normally
  sequential.

### COMPLEX
`Architect → Designer → Engineer + safe independent Builder work → Builder
finalization → Verify → Architect review`

For a specific new difficult algorithm, coupled invariant, concurrency or
transaction protocol, security design, or domain rule that remains after
clarification and inspection. Name why pseudocode helps. A localized change to
an existing state machine or retry implementation may still be SMALL; inherited
subsystem complexity and keywords are insufficient.

- Designer runs (see below), read-only, on the real code.
- Early parallel Builder work is allowed when — and only when — `parallelism.md`
  §B clears it. The finalization barrier always runs.

## 4. When AL-Designer runs

Designer is invoked when `DESIGNER_REQUIRED: YES`, which AL-Architect answers
YES only when pseudocode **materially reduces implementation risk**. A COMPLEX
classification always uses the Designer (`classify COMPLEX --designer no` is
refused); the choice applies to SMALL and STANDARD. In practice:

- **Skipped** for SMALL and most STANDARD tasks; skipped whenever the plan is
  already concrete enough to implement from. Record it in the output:
  `AL · Designer skipped — Architect plan is sufficiently concrete.`
- **Used** for COMPLEX logic where writing the flow out first prevents a wrong
  implementation.
- The Architect may also request it for a STANDARD task with one genuinely hard
  sub-problem; that is a `DESIGNER_REQUIRED: YES` answer, and `"${STATE[@]}" classify
  --designer yes` records it.

Designer never runs merely because the agent exists, and never to spend a
stronger model on an easy task.

`PLAN_CONFLICT` from the Designer goes back to AL-Architect
(`MODE: RESOLVE_CONFLICT`) before any implementation starts, and implementation
never continues with an unresolved conflict.

## 5. Rework exhaustion routes to a bounded Architect reassessment

A small confirmed defect found during finalization or verification does not
re-run planning, add a Designer, or re-classify the task. `"${STATE[@]}" rework`
sends a confirmed local defect to AL-Engineer, or routes an unresolved decision
to AL-Architect. A successful second return proceeds normally. A needed return
after the batch is exhausted persists `REWORK_REASSESSMENT`; exit 10 now means
inspect the saved state, not end the task. The supervisor obtains
`rework-recovery packet`, dispatches the actual approved Architect reassessment,
and submits the bound decision with `rework-recovery decide --file FILE`.

The run allows an initial two returns plus at most two automatic grants of two
returns each: six default returns total across review-correction boundaries.
A genuine review correction retains its separate six-correction cap and creates
no rework credit. A declined/no-path decision or the automatic cap creates a
precise stopped checkpoint. A later explicit user continuation may grant exactly
two credits for its bound latest checkpoint and current candidate; identical
replay does not grant again. Permission, scope, service, or model blockers remain
independent. Old pinned issuers and their stopped runs are never migrated by an
updated installation; they keep their original command surface.

## 6. Escalating a skipped stage

If the work turns out to need a stage this path skipped, run it — but say so.
`"${STATE[@]}" phase <PHASE> --reason "<why>"` on a skipped phase prints
`AL · <Phase> escalated — this path had skipped it (<reason>)` and records it. An
escalation needs a concrete new reason, not second thoughts about thoroughness.


## Bounded recovery commands

Use `rework-recovery packet [--output FILE]` only for a persisted pending Architect reassessment. The packet contains the immutable request, candidate and history binding, current evidence, criteria, gates, readiness, and approved Architect attempt. The supervisor sends that packet to the actual approved Architect. Submit its closed decision JSON through `rework-recovery decide --file FILE`; only a valid allow consumes the pending return and adds an automatic two-credit grant. A decline records its reason and stopping checkpoint.

For a subsystem stop caused by decline or the automatic cap, the only budget continuation is `rework-recovery continue --file FILE`. The report must bind the latest checkpoint, current candidate and task/history identity, a concrete next action, and an intact `explicit-user-continuation-v1` artifact recorded through the public artifact command. Each distinct accepted request grants two once; exact replay returns the retained receipt. Generic resume cannot clear these stops. Pre-capability issuers refuse the new commands and remain immutable.


## Classify the remaining implementation work

Initial Harness complexity is provisional interview depth. Reassess after clarification and repository inspection, even when Harness already supplied a category. Classify the change being implemented, not the complexity of its surrounding subsystem. Keywords, importance, repository size and file count alone never justify COMPLEX.

Use the lightest justified route:
- SMALL: local behavior follows an inspected existing pattern, with clear acceptance and no new difficult design. Changing a retry limit or adding a guard to an existing state machine can be SMALL.
- STANDARD: ordinary feature work or understandable changes across components; a concrete plan is enough.
- COMPLEX: name a specific new difficult algorithm, coupled state invariant, concurrency/transaction protocol or security design, and explain how pseudocode materially reduces the remaining risk. A fully clarified new distributed transaction can still be COMPLEX.

Ask about unresolved material behavior and scope choices. Inspect code to settle routine implementation details and questions already answered by the repository. Do not invent a complete specification or assume away consequential uncertainty. Missing requirements call for clarification, not automatic escalation to COMPLEX. Do not repeat settled questions or add a Designer for reassurance. Escalate only when new evidence shows a concrete harder design problem.

Before the final classification, prepare the closed risk assessment described in `reference/routing-risk.md`, citing inspected files and current hashes. Pass `classify ... --assessment-file FILE` for standalone AL; a current Harness handoff carries the same record. Preserve explicit developer routing choices. The runtime validates supplied evidence and refuses a contradictory category. Legacy runs without this additive record remain readable.
