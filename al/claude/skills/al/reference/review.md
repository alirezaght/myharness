# AL review contract

AL-Architect reviews a frozen candidate in two separate, context-isolated
invocations. The runtime packet is the source of truth for each stage. A fresh
invocation receives the original request and accepted proposal, criteria and
invariants, full selected diff, repository constraints, pre-existing attribution,
valid evidence/gates and escape leads. It excludes the plan, pseudocode and all
implementer summaries. Save its structured report before comparison is unlocked.
Escape leads retain their recorded provenance: `user-reported` means the user
described the class, not that the runtime independently reproduced an incident.
Investigate them within scope and never treat them as current candidate evidence.

`repository_review_rules` includes repository instructions, hook and gate rule sources, and their statically referenced local text files. Each entry carries its path, content hash, immutable artifact reference and contents. Discovery never executes a hook. Bounded or unreadable sources appear in `omissions` for the reviewer to resolve; an empty list does not certify rules outside this discovery surface. These are repository-owned rules, with no universal project-specific rule identifiers added by AL.

The comparison invocation receives a different packet and a new invocation ID.
Start it with `review-compare-start --plan FILE`; also pass `--pseudocode FILE`
when the route required a Designer. Optional `--approval-source TEXT` records
approval provenance, and repeated `--artifact FILE` values add supplemental
material only. The compare packet contains typed approved-input references and
their contents; inspect them in full. Its report must list the packet hash and
each required plan and pseudocode hash in `inspected_artifacts`. Supplemental
hashes do not substitute for those required inputs. It may also inspect the
implementer summaries and saved fresh findings. Check every criterion and
verification link, identify plan
defects, and resolve each earlier blocking finding with rationale, source
location, fix location and verification evidence. Whole-MR review covers the
complete selected merge-base diff and all earlier corrections.

Each stage packet contains an immutable `attempt` binding with `attempt_id`,
`stage`, `role`, `model`, `slug`, `effort`, `policy_digest`, and
`fallback_event_reference`. It records the approved Architect slot effective
when that packet was issued; the current run model is not a substitute. Reports
must keep the required `packet_hash` and `model`, which bind the report to the
issued attempt. `attempt_id`, `effort`, and `policy_digest` are required in modern v2 reports and must match the packet. Genuine legacy v1 reports retain their separately documented optional echoes. See `schemas/review-attempt.json`,
`schemas/review-packet.json`, and `schemas/architect-report.json`.

If a model is recorded unavailable before packet issuance, issue the stage packet
with the currently approved fallback. If it becomes unavailable after packet
issuance but before a report is saved, record the unavailability and explicitly
replace only that unsaved stage with
`review-packet --stage fresh|compare --replace-unavailable-attempt`. The runtime
archives the abandoned packet and attempt; use only the replacement packet.
Never replace a stage that already has a saved report or claim an unrecorded
fallback. An accepted fresh report keeps its original binding when comparison
later uses an approved fallback. A fallback binding must point to its recorded,
role- and model-matched unavailability event.

Gate manifests use `env_names` for declared environment dependencies: a
duplicate-free list of variable names, never values. Omission means no declared
environment dependency; the `env` alias is invalid. Gate discovery and admission
also retain actual selected paths, current and earlier anticipated paths, units,
and independently sourced rule hashes. A replacement manifest cannot erase
those obligations by omitting them. Source-free fixtures may use reasoned N/A;
known repository obligations stay executable or unresolved. See
`schemas/gate-manifest.json`.

CI planning context is data-only and run-bound. A supervisor records the fixed profile selection; the read-only Architect does not choose it. It supplies no ambient CI variables or evidence. Unsupported sources and expressions remain unresolved; see `gates.md`.

Reports are JSON artifacts submitted through `review-fresh --file` and
`review-result --file`. Required report fields: schema version, review ID, stage,
invocation ID, reviewer role, resolved model, task hash, candidate hash, packet
hash, inspected artifact hashes, criteria, findings, and status. Modern reports require `schema_version: 2`, `kind: architect-review-report-v2`, `document_hash`, `verification_authority_hash`, `attempt_id`, `effort`, and `policy_digest`. Fresh requires `counterexample_search` and forbids `prior_resolutions` and every finding `resolution` key, even null. Comparison requires `prior_resolutions`, `plan_defects`, and `assessments`, and forbids `counterexample_search`. Planning reports remain v1; genuine legacy review contracts are in the separately named `*-v1.json` schemas. Counts come from
findings; do not supply a verdict that contradicts them. `APPROVED` requires zero blocking findings; `CHANGES_REQUIRED` retains actual findings.

`prior_resolutions` requires an explicit status (`fixed`, `unresolved`,
`deferred`, or `disputed`). Only a verified `fixed` resolution clears a prior
blocker. Fresh reports contain no history resolutions; comparison alone handles
consolidated historical findings. Each criterion links to a real `path:line` or a hashed non-code artifact with an
explanation, plus valid evidence IDs. Each finding has a stable ID and source
criterion or invariant. A resolution records status, rationale, concrete fix
locations, and valid verification evidence. Never invent a location or evidence
reference.

For every accepted comparison, AL stores an immutable `resolution_validation`
receipt alongside the fresh and comparison report references. The receipt binds
both report hashes, the exact fixed-resolution records, the PASS evidence entries
at validation time, and the validation timestamp. Historical reconstruction
checks the binding and evidence provenance before carrying a fixed resolution
forward. A legacy history without a receipt must still validate its rationale,
locations, and current evidence; a raw `status: fixed` dictionary alone never
clears a blocker. Omitted, unresolved, deferred, or disputed findings remain in
the consolidated history.

Severity is tied to observable impact. BLOCKING covers unmet requirements,
correctness or safety defects, likely regressions, broken integration, required
verification failures, and materially missing coverage. NON_BLOCKING covers
optional style and unrelated cleanup. One bounded notes pass can record fix,
defer or dispute with a rationale. A fix is allowed only for a concrete, in-scope
defect and returns through implementation, verification, fresh review, and
comparison.

Rollout assessment explicitly addresses expand/contract compatibility,
deployment order, environment propagation through aggregators, consumer
deployment, and DLQ/redrive. UI changes include browser, viewport, hashed real
screenshots/artifacts, functional checks, alignment observations, and UX
observations. A non-applicable assessment gives a concrete reason; omitted
applicable evidence blocks review and approval.

Approval is revalidated by the runtime against the latest reports, unchanged
candidate and package hashes, criteria/evidence/gates/repros, assessments, and
absence of active or unresolved writers/checks. A prompt or prose verdict alone
never records approval.

## Rework 6 report, recovery, and command ownership rules

A fresh report cannot contain `prior_resolutions` or any finding `resolution` key, including a null value. Every `UNSATISFIED` criterion must link to a stable, same-report finding with matching source, `BLOCKING` severity, and no fixed resolution. The linked finding is one blocker, not a second count for the criterion. Comparison may clear an inherited blocker only through an explicit matching `prior_resolutions` entry with `status: fixed`, rationale, concrete locations, and current VALID evidence; a comparison finding's embedded fix or a status flip alone is insufficient. Accepted clearances use a v2 receipt bound to `stage: compare`, the comparison report hash, both report hashes, validated prior fixes, and evidence provenance. Older fresh-stage receipts remain historical and grant no clearance.

Recovery of changed rules preserves completed work and phase occurrences. Completed review/verification work needing replanning returns to PLANNING with SPEC_READY and affected evidence invalidated. An unfinished exceptional phase may replan stale obligations only after SPEC_READY and with no active or unresolved writer/check; AL records a checkpoint-bound `gate_recovery` marker and retains the current phase and completion history. A current manifest grants no replacement privilege. Planning records a replacement manifest but does not clear the obligation. Scoped implementation may continue while coverage and base-check repair remain pending; starting a writer never clears that debt. Complete relevant coverage and required evidence are enforced at final proof/review/approval. Archive recovery markers only through supported verified repair. Corrupt manifests and checkpoints remain hard errors. A second PLAN_CONFLICT occurrence requires a new intact Architect resolution bound to its occurrence.

An authorized amendment during gate recovery first validates the old marker and checkpoint against the pre-amendment task and phase. It then performs normal amendment invalidation and returns the run to PLANNING. The old checkpoint remains immutable; the active marker is rebound to a new PLANNING checkpoint for the amended task, while its outstanding reasons and superseded-manifest history remain. The replacement manifest is reset. The append-only `gate_recovery_supersessions` history records `SUPERSEDED_BY_AMENDMENT`, the amendment ID and artifact, the full old and new checkpoint references, the prior marker, and the timestamp. Supersession is not completion: current inventory, base readiness, and normal admission are still required.

Git safety applies to explicit invocation options and environment overrides, not arbitrary machine-wide Git configuration. Parse separated and attached `-c` at executable positions; allow only the documented inert identity/display keys and validated values. Reject executable/config-indirection settings, `--config-env`, external diff/textconv, pager execution, executable-path options, and invocation-supplied Git execution/config or pager/SSH-askpass overrides, including through `env`, assignments, and supported wrappers. Consume option operands and stop at `--` so quoted search/format values and filenames are data. For declared command lists, commas and newlines delimit only outside quotes; backslash escapes and quoted commas remain in their original command data, and each declaration is checked. Fully quoted nonexistent executables fail closed.

`git config` is query-only under a bounded positional grammar. Operations such as `--get`, `--get-all`, and `--list` are accepted only as actual options with valid operand counts. Scope and value options consume their operands, and `--` ends option parsing; a later token such as `--get` is then data, not a query selector. Setter forms, conflicting operations, unsupported options, and malformed arity are refused. A quoted search term or consumed operand remains literal data.

Git subcommands use bounded, subcommand-specific option and operand tables. Unknown options and abbreviations are refused rather than expanded. Recognized operands are consumed before later tokens are interpreted, and the actual `--` ends option parsing. Interpreter checks likewise classify the real startup selector: Python inline `-c` is refused even when attached or clustered, and the module exception applies only to an actual `-m pytest` or `-m compileall` entry point. Unknown startup grammar and inline-evaluation selectors for other recognized interpreters fail closed; literal arguments after a selected script or module remain arguments. These checks classify invocations and do not sandbox scripts.

Shell command wrappers are accepted only as `sh|bash|dash|zsh -c SCRIPT [ARGUMENTS...]`. Bash may additionally use one each of `--noprofile` and `--norc` before `-c`. Startup clusters, other options, `+` options, `--`, script-file or stdin forms, missing scripts, and repeated `-c` selectors are refused; the parser never searches later arguments for a selector. After the selected script operand, remaining arguments are positional data. The selected script is still recursively checked through wrappers and substitutions, with inherited environment and `find` context preserved.

For Claude's native conversation adapter, a new implicit `start` validates the previous pointer, state schema and repository identity before creating artifacts, but does not load or resume the old run. The old run's working directory does not prevent a new run elsewhere in the same repository; each run remains bound to its own directory for later execution. Publication atomically replaces the conversation pointer only after the new state is saved. If start fails, it restores the prior pointer and removes only resources created by that start; the old run and its artifacts remain intact.

## Recovery lineage and Architect reassessment
Fresh and comparison packets include the runtime-validated factual recovery lineage. Inspect the retained recovery decision and continuation reports and receipts required by the packet; each accepted report and receipt hash must appear in `inspected_artifacts`. A count or status line does not replace the underlying history. The closed decision request and continuation request schemas are in `schemas/rework-recovery-decision.json` and `schemas/rework-recovery-continuation.json`; accepted receipts use `schemas/rework-recovery-receipt.json`.

An Architect reassessment is a separate, read-only action in the `REWORK_REASSESSMENT` phase, not a fresh/comparison review. Inspect the immutable packet and its evidence, then return its bound structured decision. The supervisor persists it using the public command. It does not create review findings, waive criteria, authorize permissions, or imply approval.

Modern fresh packet evidence is a factual array; comparison retains its ledger object and comparison-only inputs. Fresh omits escapes and prior comparison context. Validate real issued artifacts against the published schemas. An acceptance code assessment is a separate non-authorizing artifact, never a formal review substitute.
