IMPLEMENTATION_SUMMARY
R4 writer r4-review-repair-002, session reliability-r4-20261120/run0aacaf92, existing REWORK return2/2 preserved. All three functional findings and the documentation finding received the bounded repairs in the actual Architect resolution. Public writer finish --id r4-review-repair-002 --outcome success completed exit0 after all checks settled, retaining source contribution evidence. This report is an authored-contribution handoff, not R4/full-mission completion or formal approval.

001: check-reconcile now authenticates the existing entry/typed companion before mutation and independently revalidates current Harness authority. It retains content-addressed immutable JSON containing schema_version=1, the exact original entry, and its exact original companion (or explicit null for a genuinely legacy observation). The current entry carries reconciliation={schema_version:1,reason,artifact,observation}. The actual bounded ordinary-file proof and original snapshot are required inputs of an explicitly produced new verification-result companion BEFORE save/outbox verdict calculation. entry_verdict authenticates the current companion, reads/authenticates the retained original companion and proof, and permits only the authorized status/note/ended_at/reconciliation transition. Original log changes, original payload changes, symlink proof, living runner and duplicate reconciliation refuse without accepted state changes. The result remains INFRA. al_store.save is untouched; no generic fingerprint repair or native/process closure is inferred.

002: issue update authenticates the previous exact payload/refs/run/session and captured graph context separately from current authority. Captured graph must be a nonnegative historical revision no greater than current; every other context field must match, original specification must match, and captured amendments must be an exact prefix of current lineage. Current binding/task existence, live authority revalidation and task-path coverage are independently checked. Existing immutable identity/scope, exact next revision, resolved immutability and fresh current matching PASS/diagnostic resolution checks remain. Prior companion is retained in agent_contract_history on real updates; next companion uses current context. Identical legitimate retries do not rewrite observations. This is not a blanket current=False waiver.

003: both standalone artifact output adapters compare acceptance_criteria against actual task criteria, falling back to accepted package criteria exactly as Harness does. Comparison precedes snapshots/index/idempotent return and uses fixed /acceptance_criteria. Valid current submissions/idempotence remain; invented and empty/applicability-evasion contracts refuse even when same output content/name already exists.

004: both user-facing documents now show the complete real command: al artifact output --name result.md --file result.md --contract-file contract.json --writer WRITER_ID. They explain the current active writer, actual assignment/criteria and exact result content binding.

Public causal coverage uses real CLI producer children, SIGINT and observed Popen reaping; Harness coverage uses actual mapped public background al_command, actual runner identity check, public polling, write_artifact and check-reconcile. No runtime identity/settlement fabrication, legacy-row clearing or method qualification was added. Existing native uncertainty remains.

CHANGED_FILES
- al/{codex,claude}/skills/al/scripts/al_agent_exchange.py — shared authenticating consume helper, versioned reconciliation validation and historical issue context validation; actual reconciliation refs join existing resolver.
- al/{codex,claude}/skills/al/scripts/al_recovery.py — authorized reconciliation snapshots and explicit companion publication before save/outbox.
- al/{codex,claude}/skills/al/scripts/al_ledger.py — reconciliation-aware verification consumer before unchanged specialized assess/prerequisite checks.
- al/{codex,claude}/skills/al/scripts/al_operational_issues.py — independent current authority/scope, authenticated historical observation, retained prior companion and current next observation.
- al/{codex,claude}/skills/al/scripts/al_artifact_cli.py — exact authoritative criteria before publication/idempotence.
- al/{codex,claude}/skills/al/tests/test_agent_contracts.py — ten public causal/refusal cases per platform, including changed original/log/proof, living/duplicate check, issue graph growth/current revision/evidence/scope/binding, valid and invalid output/idempotence.
- tests/test_agent_contracts_recovery.py — actual Harness-bound interrupted reconciliation, retained original entry/companion, INFRA status and preserved legacy admission IDs.
- harness/policy/control_sources_{codex,claude}.py — regenerated exact reachable source hashes; supported retry/finish grammar/profile is unchanged.
- docs/reliability-platform/{user-guide,r4-api-contract}.md — real full artifact output command and binding explanation.
Exact 17-path hashes: /private/tmp/al-r4-review-repair-exact-hashes-001.json. Exact delta against frozen005: /private/tmp/al-r4-review-repair-exact-delta-001.diff. Five shared AL module pairs and test pair are byte-identical; platform-specific start/store and all other prior edits preserved. Git diff/status inspected read-only. No Harness production behavior/UI, installer source, old pin or global environment changed.

DEVIATIONS_FROM_PSEUDOCODE
- none. The actual review-resolution approach is followed; immutable original observation uses content-addressed existing artifact storage with explicit versioned consumer validation rather than a history label alone.

VERIFICATION_RUN
Actual pre-fix causal results, preserved:
- E75 · r4-review-codex-repro → FAIL (failed=4; 16 deselected). Reconciliation completed CLI mutation then evidence status failed /payload; historical issue retry failed /context; invented and empty criteria were incorrectly accepted.
- E76 · r4-review-claude-repro → FAIL (failed=4; 16 deselected), same genuine symptoms.
- E77 · r4-review-harness-repro → FAIL (failed=1), fixture eligibility miss: probe.txt outside actual registered task scopes. Not product proof; corrected fixture to work/probe.txt without guard weakening.
- E78 · r4-review-harness-repro → FAIL (failed=1), genuine Harness reconciliation /payload in actual save/outbox path after real interrupted producer. Corrected pre-fix source retained /private/tmp/al-r4-harness-causal-repro-002.py, SHA256 7fbfa5790b1679b1823f0cde68f5c4ac1a4ba7f322053456994d7ba780b44ddc.
- E79 · r4-lint → PASS (counts=unknown), initial candidate, now STALE after test-only fix.
- E80 · r4-format → PASS (counts=unknown; 651 files already formatted), initial candidate, now STALE after test-only fix.
- E81 · r4-review-codex-repro → FAIL (failed=1 passed=9; 16 deselected), test helper refactor NameError before reconciliation call. Repaired by carrying actual observed Popen returncode; no invented value or production change.

Current source, sequential actual checks:
- E82 · r4-review-codex-repro → PASS (passed=10; 16 deselected), Codex003.
- E83 · r4-review-claude-repro → PASS (passed=10; 16 deselected), Claude002.
- E84 · r4-review-harness-repro → PASS (passed=1), Harness003.
- E85 · r4-parity → PASS (passed=1), parity010.
- E86 · r4-control-regressions → PASS (passed=24), control010.
- E87 · r4-installer-closure → PASS (passed=18), installer010.
- E88 · r4-lint → PASS (counts=unknown).
- E89 · r4-format → PASS (counts=unknown; 651 files already formatted).
Public evidence status after all checks confirms E82–E89 VALID. All earlier reds/fixture failures remain in immutable logs/XML, their old recipes now PLAN_CHANGED, not erased or reclassified PASS. Every run used the managed qualified Python via protected AL launcher. Latest intact generated writer handoff: /private/tmp/al-r4-review-repair-handoff-005.md; output-only updates retain all semantic argv/cwd/scope/kind/tier. No running check remains.
- Broader current-source AL consumer suites → NOT RUN in this scoped repair; sequential Builder work below. Prior broad scoped passes are historical/stale, not current proof.
- Chrome → NOT RUN; no UI change in this repair.

LEFT_FOR_BUILDER
- Run meaningful affected AL public consumers independently for BOTH platforms. Existing registered full r4-al-codex/r4-al-claude selections cover test_agent_contracts.py, test_writer_handoff.py, test_artifact_consumers.py, test_output_contracts.py; refresh unique outputs first. Full module executes prior family/context/producer/ref validations in addition to the causal selection.
- Additional material existing selectors to register as appropriate: tests/test_implementation_continuity.py (especially test_issue_is_not_a_question_and_resolution_needs_fresh_evidence), tests/test_evidence_integrity.py, tests/test_evidence_cancellation.py, tests/test_execution_receipts.py. These cover operational issue authority/resolution and shared ledger/log/outbox/receipt consumers. Shared consume extraction also affects other typed-family consumers; retain broad family evidence honestly.
- Inspect tests/test_runtime_batch5.py::test_retry_requires_crashed_check_reconciliation before running: it directly rewrites a typed terminal entry into RUNNING without updating its authenticated companion. If that disposable fixture now refuses, preserve the failure and replace its synthetic producer with a genuine interrupted public check; never waive original companion authentication. Both real new platform interruption fixtures demonstrate a usable pattern. No existing assertion was weakened in this repair.
- Root/Builder maintain final docs/evidence inventories, perform independent source reinspection and resolve formal relevant coverage/proof debt. Do not rerun unrelated unchanged Harness setup/policy/browser suites solely because broad scope hashes stale; select actual affected seams and report remaining global obligations.
- Full original R5a/R5b/R6–R11 work remains authorized after R4 support/review; this scoped success does not complete it.

OWNERSHIP_CONFLICTS
- none.

CONCERNS
- Reconciliation retains INFRA and existing liveness guards. Group absence/reaped owned leader in tests does not qualify arbitrary descendant/provider/native closure. AL ledger retained test allocations with descendant_settlement_unproven; no deletion or stronger closure claim made.
- Genuinely legacy records remain explicit legacy. Already-corrupted historical typed observations are refused rather than silently regenerated or migrated. The public new transition cannot retroactively authenticate a prior invalid mutation.
- No additional qualification method, no generic AL/native exemption, no writer/result verification gate, and no policy/claim/resource/enrollment change. Existing permission release and typed-result consumption distinctions remain.
- Service/current final proof coverage remains incomplete pending Builder/independent review. No formal approval or full mission completion is claimed.
