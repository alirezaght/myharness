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

For state machines, difficult concurrency, complicated algorithms, complex data
transformation, non-trivial transaction logic, retry/idempotency behaviour,
distributed workflows, subtle security logic, difficult domain rules, or a major
multi-component behaviour change.

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

## 5. A defect is not a reason to re-route

A small confirmed defect found during finalization or verification does not
re-run planning, does not add a Designer, and does not re-classify the task.
`"${STATE[@]}" rework` decides: fast path to AL-Engineer, or AL-Architect first when the
defect needs a decision. Only a genuine specification/design conflict, a changed
contract, an ambiguous scope or a safety decision reaches the Architect.

## 6. Escalating a skipped stage

If the work turns out to need a stage this path skipped, run it — but say so.
`"${STATE[@]}" phase <PHASE> --reason "<why>"` on a skipped phase prints
`AL · <Phase> escalated — this path had skipped it (<reason>)` and records it. An
escalation needs a concrete new reason, not second thoughts about thoroughness.
