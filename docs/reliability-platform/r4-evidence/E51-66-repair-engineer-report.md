IMPLEMENTATION_SUMMARY
Authored the admitted R4 return1 repair in reliability-r4-20261120 / 0aacaf92, writer r4-repair-001, phase REWORK. Source is settled; no check is active. Public writer finish --id r4-repair-001 --outcome success completed exit0, retained stable contribution evidence and closed this authored writer. Source editing is stopped pending sequential Builder admission. This is an authored repair handback for sequential Builder finalization, not formal AL approval, verification/rework clearance, or completion of R4/full mission.

The two previously source-identified setup flaws were actually reproduced before production edits in E51:
- test_unowned_no_budget_queue_enters_actual_dispatch_before_bytes observed five real preparation-manifest reads with CURRENT=None through the production tick scheduler.
- test_agent_registration_defers_setup_bytes_to_own_dispatch observed five real preparation reads during actual public agent-register under its unrelated outer al_command/taskNone OperationIntent.
The same public-path cases pass in E59. Tests use real protected controllers, production admission/handlers/transactions and authenticated registry observations. No process closure or producer identity was fabricated.

Minimal production repair:
- auto_prepare performs metadata-only scoped request validation and queues those concrete requests. It never reads setup inputs or launches installation under the outer AL Attempt. The outer AL path no longer attaches that unrelated Attempt to pending setup.
- tick restores/revalidates the exact stored owning setup Attempt, or enters public dispatch for a fresh queued effect regardless of optional policy bindings. It defers setup effects when called inside another current Attempt; there is no nested admission or relabeling.
- Actual prepare requires a current environment_operation Attempt matching execution, principal, task and revision before consuming target inputs. It retains ordinary real task/dependency validation; there is no verify_outputs=False effect bypass.

Four new real setup cases cover both causal counterexamples, retained exact queue/process owner using a benign publication barrier around actual child completion, metadata-only public poll/cancel, durable unqualified legacy liability after poll/cancel, and refusal to relabel a real unrelated reserved AL Attempt. Its sole unstarted test control is released only through the existing exact reserved/no-permit CAS, not PID/group/status inference.

Preserved E31 assertions with correct current contexts:
- Pure path/template task_unit calls explicitly use metadata-only mode.
- Genuine Node/Python setup, cached failure and lock-precedence tests now publicly register complete task metadata and invoke the actual handler under genuine production reservation. The helper preserves original OS exception types rather than weakening FileNotFoundError assertions. Original frozen-install/no-hooks, private venv, lock precedence, Unavailable-not-Ready, and no-product-writer/poll assertions remain.
- The scoped setup test publicly registers its actual complete task instead of installing an incomplete task dictionary. No writer authority is invented.
- The genuine legacy continuity journey retains original admissions and refuses direct source-byte validation without its owning Attempt. Its original bytes-changed assertion is preserved separately in test_public_reserved_consumer_preserves_original_source_drift_refusal: real strict public apply_patch admission detects drift before destination mutation. No legacy row was deleted to produce strict success.
- test_unqualified_setup_refuses_before_source_or_manifest_bytes proves actual metadata preflight performs no target reads and strict unqualified setup refuses before patched target readers or setup/process mutation. It does not qualify arbitrary installers/native processes.

Confirmed both missing parity modules independently against actual paired bytes and added only the equal shared-file inventory entries to both manifests:
al_policy_retry.py = 891760934e216806caacf7cdb0331fee7e76f5353c4587ca911d8d6477ce9137
al_retry_control.py = 4a04ffb2d7b048141d2f37e58bdbca0a0ff44b3d6166f0f306a64bc53f1ca883
No platform-specific adapter or AL/installer script bytes changed. The sealed control source hash maps therefore require no regeneration from this reference-inventory-only change. Old pins, helper grammar, policies and qualification boundaries are unchanged.

CHANGED_FILES
- harness/execution_operations.py — defer automatic setup ownership; always dispatch fresh queued effects, restore exact retained owner, prevent nested setup effects.
- harness/task_environment.py — reject missing/unrelated current setup ownership; metadata-only automatic queueing.
- al/codex/skills/al/reference/parity-manifest.json — add two byte-identical shared modules.
- al/claude/skills/al/reference/parity-manifest.json — same verified inventory correction, no platform mirroring.
- tests/test_agent_contracts_setup.py — new actual continuation/counterexample, queue lifetime, poll/cancel and unrelated-owner tests plus genuine public registration/reservation test helpers.
- tests/test_task_tooling.py — use actual admitted execution/task context for effects; explicit metadata-only pure template checks; retain every original installer/cache/precedence assertion.
- tests/test_execution_operations.py — complete public scoped-setup registration, retaining no-writer/poll/foreign-owner/stale-revision assertions.
- tests/test_execution_continuity.py — retain real legacy/direct-byte refusal and admissions; original drift proof moved to supported public strict consumer companion.
- tests/test_agent_contracts_mutable.py — real strict source drift and pre-read unsupported setup companions.
Exact nine-path final content hashes are retained in /private/tmp/al-r4-repair-source-manifest-001.json, compared to frozen pre-repair source /private/tmp/al-r4-frozen-review-002. git diff --check was clean. All pre-existing/user/Builder changes outside these nine paths, including pyproject/docs/provenance, were preserved.

DEVIATIONS_FROM_PSEUDOCODE
- none to the binding final bounded Architect/Designer protocol. Automatic preparation now reports the existing Queued state and executes later under its own dispatch, concretizing the explicitly required separate effect ownership.
- The original low-level installer tests use genuine production reserve/scope/handler invocation to retain original OS exception assertions; public setup dispatch/polling and the causal continuation tests independently exercise the real tool routes.

VERIFICATION_RUN
Every execution below used the managed public ledger and qualified Python. All earlier failures/used output paths remain retained. No source writes occurred while checks were active.
- E51 · r4-setup-protocol → FAIL (11 failed, 17 passed, 4 skipped)
- E52 · r4-lint → FAIL (counts=unknown)
- E53 · r4-lint → PASS (counts=unknown)
- E54 · r4-format → PASS (counts=unknown)
- E55 · r4-parity → PASS (1 passed)
- E56 · r4-setup-protocol → FAIL (1 failed, 29 passed, 4 skipped)
- E57 · r4-lint → PASS (counts=unknown)
- E58 · r4-format → PASS (counts=unknown)
- E59 · r4-setup-protocol → PASS (30 passed, 4 skipped)
- E60 · r4-consumer-regressions → PASS (26 passed, 4 skipped)
- E61 · r4-mutable-protocol → FAIL (1 failed, 51 passed)
- E62 · r4-lint → PASS (counts=unknown)
- E63 · r4-format → PASS (counts=unknown)
- E64 · r4-mutable-protocol → PASS (52 passed)
- E65 · r4-launch-protocol → PASS (5 passed)
- E66 · r4-parity → PASS (1 passed)

E51 is genuine pre-fix source red, with both new causal failures and all nine original E31 failures (11 failed/17 passed/4 skipped). E52 was local test style/unused variables, fixed without changing assertions. E56 was solely the migrated root-unit fixture trying public product ownership ['.']; the existing guard correctly refused it before preparation. It now uses specific ['app'] task ownership while still testing cwd='.' setup; no root ownership restriction was relaxed.
E59 passes all four new setup lifecycle cases and all original consumers: 30 passed/4 skipped. E60 separately passes the exact original E31 three-file recipe: 26 passed/4 skipped. E61 failed only in its new post-refusal assertion because the pre-existing policy fixture has SimpleNamespace tooling without a directory: both actual safety refusals had already occurred. The final TEST-ONLY adjustment checks actual unchanged setup_outcomes and process handles instead; the current full mutable/resource suite passes all52 in E64.

SOURCE CURRENCY: Production has not changed since the E53/E54 candidate. E59/E60 ran before the last test-only E61 postcondition correction. Their actual functional passes are retained, but their broad declared scope includes tests, so final CURRENT/VALID status must be determined by the runtime. Do not present those old inputs as final-current proof or claim formal rework clearance. E62/E63/E64/E65/E66 ran after the final source/test edit. No further source changes followed.

Latest used bindings: setup-protocol003, consumer-regressions002, mutable-protocol006, launch-protocol004, parity003. Their XML paths are /private/tmp/al-r4-<area suffix>-<number>.xml; basetemp uses the same path without .xml. Protected logs: .codex/al-runtime/reliability-r4-20261120/logs/0aacaf92-E<n>-<area>-<kind>.log. Handoff004 was regenerated and read intact after the last output-only registration; non-output argv/cwd/tier/kind/full dependency scope were unchanged.

Four skips are still real obligations: signed-in Codex and Claude smoke tests are opt-in; test_native_auth_selection_and_profile_tamper and test_actual_native_controller_reads_guarded_al_and_resume report installed Codex0.164.0 unavailable. They were not converted to passes or removed. Each check also reported retained descendant_settlement_unproven; functional PASS does not qualify whole native/process-tree settlement.

LEFT_FOR_BUILDER
- Complete final-current broad support/source-closure/regression verification and source/evidence documentation after this settled handback. In particular, refresh unique outputs and rerun original consumer and setup areas if needed for final full-scope VALID evidence after the last test-only change; E60's actual matching recipe pass is preserved, not erased or promoted beyond its currency.
- r4-control-regressions002 and r4-harness-contracts006 remain NOT RUN in this repair. Other first-finalization E28–E50 results remain their actual historical source outcomes, not automatically current after this repair. Source closure/installer/native/corruption/delivery/migration/policy/preexecution/publication/worker/AL-family checks can be completed sequentially by Builder as relevant; never claim unknown/unexecuted checks passed.
- Update docs/inventory/evidence archives with E51 causal source red, E56/E61 fixture-only failures, E59/E60 actual functional passes and final-current focused results. Explain that automatic setup queues metadata and launches through a separate effect dispatch; existing Queued schema is unchanged. Preserve all actual planning reports and the Builder's narrow formatter-only exclusion verbatim.
- Preserve exact claim and legacy/native uncertainty boundaries: no general installer/native/AL qualification, no source stability from immutable typed exchange, no PID/group/status closure, no cleanup or result-verification gate on authenticated permission release.
- No UI source changed and no Chrome run was performed; any later UI support change requires actual functional plus visual/UX inspection.

OWNERSHIP_CONFLICTS
- none. All edits are within admitted harness/al/tests ownership; docs/config were preserved for sequential support.

CONCERNS
- This closes only authored repair contribution work, not the R4/final mission or formal AL proof/review. Rework1/2 and all original history are retained; no run/counter/phase reset or clearance is inferred.
- New setup tests use actual production admission and genuine isolated benign installer children; their controlled completion barrier is test synchronization, not proof of arbitrary process-tree closure. Unqualified legacy setup liability explicitly survives poll/cancel. Strict affected setup remains unsupported before target bytes.
- E59/E60 broader-input currency after the last test-only correction remains a named final verification obligation; no extra multi-minute repetition was performed merely for bookkeeping before the authorized sequential Builder boundary.
- Root alone repaired capacity through exact settled disposable R3 basetemp cleanup. Audit /private/tmp/al-reliability-disposable-cleanup-20261120-004.jsonl; the5GiB reserve and all protected evidence/pins remained unchanged. Engineer performed no cleanup or global installation.
