IMPLEMENTATION_SUMMARY
Partial authored-contribution handoff for r4-escalation-repair-004, reliability-r4-20261120/run0aacaf92, IMPLEMENTATION. Root requested this settled boundary to admit sequential r4-delivery-buffer-repair-005 with the required Harness ownership. Exact-buffer authentication remains UNRESOLVED; this is not completed R4, approval, security qualification or mission completion. RR1 pending, continuity1, used2/total2/automatic_authorizations0 are unchanged.

Both original source-traced variants were reproduced before production edits through real public producers on both platforms: identical blocker retry after independent task registration rejects /context, and replay after a real Registry-backed accept delivery/resolution rejects /payload. No fixture eligibility failure or invented decision/identity/closure was used.

Implemented owned lifecycle mechanics: initial escalation registration now retains an immutable escalation-observation-v1 snapshot containing schema_version, exact original record/companion and null transition. Subsequent authorized decision and scoped revalidation transitions retain immutable next observations plus required transition artifact references. History is bounded to original + decision + optional revalidation. Consumption authenticates original registration digest, payload, required bytes, captured graph and specification lineage, then each exact allowed state/delivery transition and its durable acknowledgement/revalidation link. Current record and companion must equal the latest observation. Historical graph is bounded; other context remains current, accepted specification must match or be a preserved amendment prefix. Duplicate registration authenticates before its existing digest return and separately revalidates current affected task authority. Existing assignment/result graph invalidation is unchanged. Older formats without this history keep their original consumer semantics; no old provenance is fabricated.

Decision/revalidation producers now authenticate the prior escalation before mutation and publish the next companion before save/outbox consumption. Missing observation files return fixed /required_inputs instead of leaking FileNotFoundError paths. Exact delivery replay remains idempotent; conflicting reuse, altered original/current records/companions/artifacts, unsupported/missing references, foreign delivery and terminal authority refuse without accepted state changes.

IMPORTANT INCOMPLETE CANDIDATE: the current AL decision path performs existing T.authority checks, then bounded ordinary/nofollow input reading of the fixed private inbox and object equality before use/snapshot. This rejects the executed ordinary forged reject→accept case, but does NOT prove replacement safety: T.authority currently returns no authenticated digest/buffer. A later read is not the same buffer actually authenticated by the bridge. The current comparison must not be described as closing that guarantee. Root selected the required successor fix: capture the validated row['delivery'] digest/buffer at the exact authority inbox/Registry comparison point, return a closed decision-only receipt, then AL's single bounded buffer must match before parse/use/snapshot/mutation. Preserve nondecision reply shape, current epoch/approval/ack semantics and fixed roots; add controlled replacement refusal. No Harness source was edited by this writer.

CHANGED_FILES
- al/{codex,claude}/skills/al/scripts/al_agent_exchange.py — record_escalation, historical/transition validation, fixed missing-input error; prior plan/issue/verification handling preserved.
- al/{codex,claude}/skills/al/scripts/al_execution_tasks.py — authenticating blocker replay/current task authority and original snapshot publication; blocked-task consumer uses authenticated escalation history.
- al/{codex,claude}/skills/al/scripts/al_execution_decisions.py — authenticating previous observation, current next companion for delivery/revalidation, incomplete fixed-inbox candidate comparison explicitly pending trusted receipt.
- al/{codex,claude}/skills/al/tests/test_execution_decisions.py — two causal public regressions, immutable history/idempotency assertions and ten refusal parametrizations including real rejected delivery forged to accept.
- harness/policy/control_sources_{codex,claude}.py — refreshed exact reachable source hashes, no method/profile expansion.
Exact ten authored-path hashes: /private/tmp/al-r4-escalation-authored-hashes-001.json. Shared original files were checked paired before mirroring. Git diff/status and resulting helper source inspected. No other Harness production, docs, UI, migration, dependency, installer, old pin or global installation changes.

DEVIATIONS_FROM_PSEUDOCODE
- No plan deviation; the required exact-buffer producer proof exposed an unavoidable unowned bridge seam. Root authorized a sequential ownership extension, not a weakened guarantee or fabricated completion.

VERIFICATION_RUN
- E106 · r4-escalation-codex-repro → FAIL (failed=2; 3 deselected), genuine pre-fix context/payload causal cases, Codex001.
- E107 · r4-escalation-claude-repro → FAIL (failed=2; 3 deselected), same genuine cases, Claude001.
- E108 · r4-lint → PASS (counts=unknown), interim source, now historical/stale.
- E109 · r4-escalation-codex-repro → FAIL (failed=1 passed=10; 3 deselected), missing immutable observation produced raw FileNotFoundError; original causal cases and other refusals passed. Codex002 retained.
- E110 · r4-escalation-codex-repro → FAIL (failed=2 passed=10; 3 deselected), retained missing-file fault plus genuine public forged delivery acceptance: actual Registry reject delivery, submitted verdict changed to accept, public decision returned0. Codex003 retained.
- E111 · r4-escalation-codex-consumers → PASS (passed=15), full existing module including original amendment/revalidation and new cases, consumers001. Functional proof only; replacement-safety gap above remains.
- E112 · r4-escalation-claude-consumers → PASS (passed=15), corresponding full module, consumers001; same qualification limit.
- E113 · r4-lint → PASS (counts=unknown), current candidate.
- E114 · r4-format → PASS (counts=unknown; 654 files already formatted), current candidate.
- E115 · r4-parity → PASS (passed=1), parity030.
- Current focused r4-escalation-codex-repro004 and r4-escalation-claude-repro002 → NOT RUN; both destinations unused. Full modules execute these cases, but exact focused-area green remains a successor obligation; do not count the unused plans as passes.
- r4-control-regressions030 and r4-installer-closure030 → NOT RUN; registered destinations unused, defer expensive final closure verification until the required authenticated receipt source settles.
- Both exact broader AL recipes → NOT RUN on this source; required sequential Builder support after core completes. Preserve old used001 artifacts and refresh only output paths, never guessed selectors.
Every executed check used the managed protected launcher and qualified Python serially. All checks settled; no process was interrupted or outstanding. Functional group/runner evidence does not qualify arbitrary descendants/native/providers.

LEFT_FOR_BUILDER
- SUCCESSOR ENGINEER FIRST: implement the chosen exact-buffer receipt in harness/execution_authority.py plus the AL authority consumer/decision path, controlled replacement refusal and relevant bridge tests. This is required core, not a documentation-only limitation. Capture trusted expected digest at validation, never a later reread. Final functional passes must be rerun on that source with unique outputs.
- Full consumers001 outputs are USED for both platforms. Focused Codex004/Claude002, control030 and installer030 remain unused; parity030 is USED. Regenerate/read actual current successor handoff before checks. Parent recipe files retain exact semantic argv/cwd/scope; do not overwrite artifacts.
- Sequential Builder: exact two broader AL recipes, relevant final source closure and docs/evidence updates explaining original observations, authorized transitions, actual E106–E115 history and final receipt behavior. Include the full decision modules/new cases in final meaningful coverage.
- Preserve RR1 rejected publication/pending credit, initial two returns, implementation-continuity facts and remaining formal proof/coverage debt. Full R5a/R5b/R6–R11 mission remains authorized.

OWNERSHIP_CONFLICTS
- harness/execution_authority.py — current ownership excludes it; the fixed bridge discards the authenticated delivery buffer and emits no digest. Exact-buffer authentication cannot be completed robustly through a post-authority file reopen within AL alone. Root chose a sequential expanded writer; no unauthorized edit was made.

CONCERNS
- The lifecycle mechanics and ordinary forgery refusal are tested, but the exact-buffer replacement guarantee remains pending and must be fixed before the repair is considered complete. Source labels or typed status are not proof of authority.
- No formal approval, recovery credit, current full-suite/service coverage, native qualification or full mission completion is claimed. Earlier R4 results remain their actual source history.
