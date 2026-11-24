# AL agent rules shared by every role

The AL agent definitions keep only role-specific instructions. The rules below apply to every
role that is pointed here, exactly as if they were written in its definition. Read the section
your situation needs; do not read the whole file up front.

## Recovery reassessment

When the supervisor supplies an immutable `rework-recovery packet`, assess that bounded recovery request separately from PLAN_CONFLICT and final review. Read the packet and its referenced evidence, failure history, task criteria, candidate, pin, and the exact approved attempt. Do not launch checks, invoke services, or mutate AL state; the supervisor owns those commands.

Return one closed `rework-recovery-decision-v1` JSON document matching `skills/al/reference/schemas/rework-recovery-decision.json`. Bind the supplied occurrence, candidate/history binding, packet hash, attempt ID, model, effort and policy digest. Include the packet and attempt hashes in `inspected_artifacts`, the actual remaining criteria, a concrete bounded `next_action`, and a substantive `rationale`.

Use `decision: allow` only with basis `verified-progress`, `new-independent-defect`, or `revised-diagnosis`, and cite the supplied supporting evidence. Repeating the same unsuccessful approach without new supporting evidence is insufficient. Use `decision: decline` with basis `no-useful-next-step` when no authorized useful route remains. Do not treat rework credit as permission, a specification amendment, a review rejection, or approval. The supervisor validates and submits the response through `rework-recovery decide`; your report alone does not change state.

## Rework, recovery and command ownership rules

A fresh report cannot contain `prior_resolutions` or any finding `resolution` key, including a null value. Every `UNSATISFIED` criterion must link to a stable, same-report finding with matching source, `BLOCKING` severity, and no fixed resolution. The linked finding is one blocker, not a second count for the criterion. Comparison may clear an inherited blocker only through an explicit matching `prior_resolutions` entry with `status: fixed`, rationale, concrete locations, and current VALID evidence; a comparison finding's embedded fix or a status flip alone is insufficient. Accepted clearances use a v2 receipt bound to `stage: compare`, the comparison report hash, both report hashes, validated prior fixes, and evidence provenance. Older fresh-stage receipts remain historical and grant no clearance.

Recovery of changed rules preserves completed work and phase occurrences. Completed review/verification work needing replanning returns to PLANNING with SPEC_READY and affected evidence invalidated. An unfinished retained phase receives a checkpoint-bound `gate_recovery` marker; only that marker permits replacement discovery/planning in the retained phase. Planning records a replacement manifest but does not clear the obligation. Scoped implementation may continue while coverage and base-check repair remain pending; starting a writer never clears that debt. Complete relevant coverage and required evidence are enforced at final proof/review/approval. Archive recovery markers only through supported verified repair. Corrupt manifests and checkpoints remain hard errors. A second PLAN_CONFLICT occurrence requires a new intact Architect resolution bound to its occurrence.

Git safety applies to explicit invocation options and environment overrides, not arbitrary machine-wide Git configuration. Parse separated and attached `-c` at executable positions; allow only the documented inert identity/display keys and validated values. Reject executable/config-indirection settings, `--config-env`, external diff/textconv, pager execution, executable-path options, and invocation-supplied Git execution/config or pager/SSH-askpass overrides, including through `env`, assignments, and supported wrappers. Consume option operands and stop at `--` so quoted search/format values and filenames are data. For declared command lists, commas and newlines delimit only outside quotes; backslash escapes and quoted commas remain in their original command data, and each declaration is checked. Fully quoted nonexistent executables fail closed.

## Harness-bound execution reports

When invoked for a harness-bound run, report actual agent and task lifecycles through the installed integration commands in reference/execution-integration.md. A planned role is not a started agent. Scoped implementation authority is separate from final evidence and approval; progress and prose never complete work. Raise a typed task-scoped blocker only for an unresolved material specification or developer engineering decision. Report missing tools, incomplete discovery and provider failures as operational issues with diagnostics and an automated repair action; keep defined independent work eligible. Consume current authored dependency outputs without pretending they are verified completion. Reconnect only refreshes recorded status; explicit resume continues the stored native conversation under current epoch authority. Terminal agents need fresh observed invocations. A missing PID or stale heartbeat never establishes process settlement.

## Named report outputs

If the handoff provides an OUTPUT_ARTIFACT name and the Harness submit_output tool is available,
submit the final report there and return only its immutable reference. This report outbox does
not authorize source edits by read-only roles. Otherwise use an explicitly authorized output
file or host final-message capture; never claim a file was saved if the host refused it.

## Classify the remaining implementation work

Initial Harness complexity is provisional interview depth. Reassess after clarification and repository inspection, even when Harness already supplied a category. Classify the change being implemented, not the complexity of its surrounding subsystem. Keywords, importance, repository size and file count alone never justify COMPLEX.

Use the lightest justified route:
- SMALL: local behavior follows an inspected existing pattern, with clear acceptance and no new difficult design. Changing a retry limit or adding a guard to an existing state machine can be SMALL.
- STANDARD: ordinary feature work or understandable changes across components; a concrete plan is enough.
- COMPLEX: name the hard part — the specific new algorithm, invariant or protocol (concurrency, transaction, security) — and explain how pseudocode materially reduces the remaining risk. Record it with `classify COMPLEX --hard-part algorithm|invariant|protocol:<name>`. Breadth (files, services, modules) and defect or finding counts are never reasons; without a named hard part the task is STANDARD. A fully clarified new distributed transaction can still be COMPLEX.

Ask about unresolved material behavior and scope choices. Inspect code to settle routine implementation details and questions already answered by the repository. Do not invent a complete specification or assume away consequential uncertainty. Missing requirements call for clarification, not automatic escalation to COMPLEX. Do not repeat settled questions or add a Designer for reassurance. Escalate only when new evidence shows a concrete harder design problem.

Before the final classification, prepare the closed risk assessment described in `reference/routing-risk.md`, citing inspected files and current hashes. Pass `classify ... --assessment-file FILE` for standalone AL; a current Harness handoff carries the same record. Preserve explicit developer routing choices. The runtime validates supplied evidence and refuses a contradictory category. Legacy runs without this additive record remain readable.

## Portable investigation

Before final classification/planning, inspect existing implementation, manifest/path
ownership, literal call sites, nested instructions and locally known main divergence.
Use routing-audit --request "APPROVED REQUEST" in standalone AL, or Harness read_file
with harness:repository-audit. Keyword counts and test volume do not establish ownership
or remaining complexity. Resolve material ownership uncertainty before planning.
In Harness, enumerate harness:skill/index and read references/schemas through
harness:skill/reference/... with bounded cursors; do not assume access to global paths.

## Coverage obligations

Incomplete relevant repository, service, consumer and required-check discovery creates verification obligations; it does not block scoped implementation. Relevant uncertainty requires conservative check selection and repair. Final proof and approval require complete relevant coverage and real mandatory bindings. Unknown, unavailable and unexecuted checks are never passes. Report operational issues through issue-update, and reserve developer blockers for actual specification or consequential engineering choices.

## Agent communication

- **Progress.** At each real milestone (not on a timer), run
  `progress --note "<what is done>" [--done N --total M] --role <your role>` through the
  launcher command your handoff gives. The supervisor and the Harness dashboard see it
  without asking you.
- **Questions.** When a material question arises mid-run, record it at once with
  `ask --blocking|--non-blocking --question "<question>" --role <your role>` and keep
  working on the parts it does not affect. The answer comes back to you in this same
  conversation; do not stop and wait unless nothing independent remains.
- **Handoff record.** End with a structured record (`schemas/agent-handoff-record.json`):
  decisions with reasons, assumptions, files touched, verified claims with ledger evidence
  IDs, unverified claims with the reason, open questions, risks. Return it as a JSON block
  in your final report; the supervisor stores it with `handoff-record --file`. It reaches
  the next agent by hash, so do not also write a prose summary of the same facts.
- **Inputs by hash.** Long artifacts reach you as a hash plus the relevant original
  excerpt or delta. Read the full artifact by hash when you need more; never work from a
  paraphrase.
