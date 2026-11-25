SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: COMPLEX
CLASSIFICATION_REASON: protocol: migration-history reconciliation and sealed execution accounting across retained handles and newly reachable cache/service effects. The recovered protocol covers the original hard parts; current main introduces effects requiring a bounded Designer amendment.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer integrates correctness-sensitive contracts; Builder follows with dashboard, packaging, documentation and candidate verification.
DESIGNER_REQUIRED: YES
DESIGNER_REASON: Amend only cache/service effect admission, sealed-control closure qualification and resource cleanup ownership. Preserve recovered pseudocode elsewhere, subject to the concrete migration correction below.

ASSUMPTIONS
- A1: Publication rebases checkpoint `ded9054310d1f8ffe90736b192fcfaec557be3c9`, whose parent is `2ad8bed9bdcaf82212f5f5ab3c8585b79943ca9d`, onto verified main `fef2cd1ec5650c7fba5cf9f056302d105f5c64a5`. Preserve the original checkpoint reference. This follows the latest explicit user instruction.
- A2: Full mission authorization and publication authorization remain effective; this integration does not represent completion of R5b–R11.
- A3: Main still owns migrations 0019/0020 exclusively. No additional main migrations appeared since `6b1da29`. The recovered mapping of checkpoint 0019–0023 to canonical 0021–0025 remains valid.
- A4: Main’s root `AGENTS.md` now requires `al/verify_changed.py`; its fast tier replaces the prior assumption of serial host-suite execution. No applicable nested instruction files were found in the inspected tree.
- A5: The integration worktree switched from main to the checkpoint during inspection as confirmed by the supervisor. Subsequent observations use immutable `git show fef2cd1:...`; no mixed working-tree content is an authoritative candidate.

INVARIANTS
- I1: Preserve main history, main migrations and all checkpoint facts; ordinary non-force publication only.
- I2: Migration identity requires exact recognized filenames and compatible schema, never version numbers alone. Relocation preserves `applied_at` and application data.
- I3: Fully current canonical databases remain readable while another connection holds a write transaction.
- I4: Policy attempt, process instance, epoch, resource claim, retained handle, storage allocation and publication identity remain distinct. Publication or group disappearance cannot establish qualified whole-tree closure.
- I5: Cache hits, service responses and events confer no execution authority, process settlement or incident evidence.
- I6: `next --do`, `verify` and automatic evidence execution cannot inherit read-only or sealed no-descendant qualification from their command prefix.
- I7: Historical pins and qualification records remain immutable. Only newly inspected candidate closures may be regenerated.
- I8: Main planning-session guards, source investigation, Knowledge consumers, storage custody and checkpoint typed/incident guards all remain enforced.

IMPLEMENTATION_PLAN
- Step 1: Supervisor prepares the isolated integration branch at the preserved checkpoint and rebases its contribution onto verified main. Engineer resolves conflicts under writer ownership. Do not replace upstream files wholesale with checkpoint versions.
- Step 2: `harness/db.py`, `harness/migration_compatibility.py`, `harness/migrations/` — retain recovered canonical numbering and transactional relocation. **Amend the recovered unconditional-lock flow:** inspect ledger and schema in one consistent read transaction; if fully canonical and current, return without `BEGIN IMMEDIATE`. If relocation or migration is needed, end the read transaction, acquire `BEGIN IMMEDIATE`, reread and validate before every mutation. Never promote the preliminary read into mutation authority. Preserve `is_busy()`, registered session functions, application session guards and protected COMMIT/rollback behavior.
- Step 3: `harness/execution_operations.py` and `harness/policy/resources.py` — use main’s `read_only(args)` consistently in dispatch, child-role authorization and policy intent derivation. Checkpoint’s `args[0] not in READ_COMMANDS` would otherwise misclassify `next --do`. Retain new `brief`, `agent`, `progress`, `ask`, `handoff-record`, `verify` and `knowledge_consumers` interfaces with their actual effects and admission.
- Step 4: Designer amendment covers `OperationController.al_cache_grants()`, `al_facts()`, `execution_isolation.al_grants()`, `execution_al_service`, and sealed qualification. Main’s profile preparation now performs launcher lookup, Git inspection, directory creation and optional service access. Specify which admitted owner accounts for each effect before enabling it on a qualified path. Existing generic conservative refusal remains authoritative until qualification is established.
- Step 5: `execution_check_channel.py`, `execution_check_process.py`, `execution_processes.py` and `policy/processes.py` — retain recovered per-handle ownership, deadline and liability rules while preserving main’s private `/tmp/h-*` environment. Define cleanup ownership separately from policy closure; cleanup cannot release durable claims. Handle spawn failure, post-spawn storage failure, delayed acknowledgement and unresolved descendants without losing the exact temporary-directory or process association.
- Step 6: `execution_worker.py`, `execution_al_service.py`, `execution_isolation.py` — preserve current event subscription/polling fallback and Linux snapshot-only descriptor relay. Revalidate execution authority through existing replay paths. A service notification triggers observation; it is not a trusted terminal-state report. Preserve cleanup on subscription failure and worker cancellation.
- Step 7: `task_environment.py`, `task_tooling.py`, setup continuation — retain recovered owning `environment_operation` admission, completion-time validation and separately admitted prepared-check planning. These source files did not change between prior and current main; no new setup design is required.
- Step 8: Both AL script trees and `al/installer/` — integrate typed publications into current planning, evidence, writer, recovery and review handlers. Preserve main’s source-aware caches, current service coverage, review-delta and evidence retirement semantics. Preserve launcher-only `verify_pin_cached`; full `verify_pin` remains mandatory for its existing non-launcher callers. Re-audit the sealed retry/finish closure after source settles. Do not qualify `writer finish --evidence auto` merely by extending the existing six-argument matcher: automatic checks have additional effects.
- Step 9: `execution_bootstrap.authority_snapshot()` — preserve main’s dynamic Knowledge-module inventory and storage-core snapshot while including checkpoint policy/reliability packages. Verify every newly reachable module is present in the protected snapshot.
- Step 10: Builder integrates dashboard routes and assets, packaging and migration documentation after core interfaces settle. Preserve main planning-session/source-investigation UX alongside incident/timeline/policy views. Update remaining-feature handoff against actual rebased consumers.
- Step 11: Complete required candidate checks and independent review. Recheck remote main before publication; if advanced, rebase again and refresh affected evidence. Supervisor publishes normally and verifies the remote commit.

INFRASTRUCTURE_CONSTRAINTS
- Applicability and trigger: SQLite migration reconciliation is involved. No cloud database or Pub/Sub subscription is introduced; `SourceEvents` is a local AL service subscription.
- Inspected sources and findings: `.github/workflows/{ci,browser,containment,al-release}.yml`, `pyproject.toml`, main migration inventory, `execution_al_live.environment_flag`, `execution_worker.SourceEvents`, and local service/relay implementation. Workflows contain direct jobs without reusable-workflow inheritance or explicit job timeout overrides. `HARNESS_AL_SERVICE_EVENTS` defaults enabled unless its value is `"0"`; unavailable service transport falls back to polling. No staging/pilot deployment configuration was identified in the tracked infrastructure inventory.
- Consequences: Migration reconciliation must remain atomic and bounded to known histories; preserve the no-write-lock current-database path. Upgrade only after old checkpoint writers are closed. No cloud deployment or service startup is authorized by this plan.
- Unresolved gaps: Hosted runner/project timeout limits and externally configured environment overrides are not available from these repository sources. Record them as external verification limits; do not invent values. Current service/check discovery must be repaired and bound to the rebased candidate before final proof.

CORE_SCOPE (owned by AL-Engineer): Rebase conflict resolution, migration bridge, policy/resource/process/setup integration, AL contracts and exact candidate qualification.
SUPPORT_SCOPE (owned by AL-Builder): Dashboard composition, packaging, documentation, distribution inventory and candidate verification.
INTEGRATION_POINTS: Session SQL functions; owning Attempt; retained CheckChannel records; ExecutionStorage; AL command classification; protected bootstrap; installer closure; Knowledge consumers.
EDGE_CASES: Fully current DB under writer lock; checkpoint partial prefixes; crash after remap; malformed schema; `next --do`; auto-evidence finish; cache miss/tamper; absent service; Linux relay refusal; orphaned process; retained old handle acknowledgement.
RISKS: Mechanical conflict resolution can preserve syntax while weakening authority. Regenerated hashes alone do not qualify newly reachable effects.

WRITE_OWNERSHIP
CORE_FILES (AL-Engineer writes these): `harness/*.py`, `harness/policy/**`, `harness/reliability/**`, `harness/knowledge/**`, `harness/migrations/**`, `al/installer/**`, `al/{codex,claude}/skills/al/**`, `al/tests/**`, non-browser `tests/**` relevant to those modules.
SUPPORT_FILES (AL-Builder writes these): `harness/dashboard/**`, `docs/reliability-platform/**`, `pyproject.toml`, browser `tests/test_*browser.py`.
SHARED_FILES (both would need to write — must be sequenced): Any support-owned file needing textual rebase conflict resolution is first resolved by Engineer, then handed to Builder; final qualification records wait until all contributing source edits settle.

PARALLEL_ASSESSMENT
BUILDER_EARLY_WORK: none
BUILDER_DEFERRED_WORK: Dashboard integration, packaging inventory, migration documentation, browser validation and final verification.
INTERFACE_STABILITY: Migration validation, process temporary ownership, policy command classification and profile grants change; support consumes their completed behavior.
SHARED_STATE_COMMANDS: Rebase, dependency preparation, build output, final qualification generation and publication.
PARALLEL_SAFE: NO
REASON: Rebase and cross-cutting authority/closure changes require sequential ownership and a stable candidate.

TEST_PLAN
- T1: Recognized migration upgrades preserve data and timestamps; unknown histories refuse atomically — disposable SQLite fixtures in `tests/test_db.py` — seam `connect/apply_migrations` — catches skipped main migrations, duplicate schema and partial remap — misses live operational upgrade behavior.
- T2: Canonical current DB opens during another writer transaction — `tests/test_state_db_busy.py` — seam `connect`, CLI and dashboard — catches unconditional reconciliation locks — misses remote filesystem behavior.
- T3: Mutating intent cannot pass read-only admission — policy dispatch/resource tests through `OperationController.dispatch` — catches `next --do` prefix classification and auto-evidence bypass — misses arbitrary hostile-host execution.
- T4: Retained handles and temporary resources remain attached to exact liabilities — guarded-check, process, policy and storage tests — seam channel requests and controller process APIs — catches acknowledgement releasing unrelated claims or post-spawn ownership loss — misses unqualified whole-tree containment.
- T5: Cache/service paths retain exact source and operation boundaries — `tests/test_al_sandbox_grants.py`, `tests/test_al_service_relay.py`, AL pin-cache tests and sealed-control tests — seam launcher/controller/relay — catches cache tampering acceptance, expanded relay operations and newly reachable unqualified effects — misses provider authentication.
- T6: Setup and session behavior survive integration — task-tooling, agent-contract setup and planning-session tests — public setup/session APIs — catches borrowed Attempt, stale completion and missing SQL guards — misses real provider execution.
- T7: Dashboard navigation and source selection remain functional and aligned — browser suite, desktop and narrow viewport artifacts — catches clipping, stale selection, broken focus and route regressions — misses platforms not exercised.

VERIFICATION_COMMANDS
- `uv run pytest tests/test_db.py tests/test_state_db_busy.py`
- `uv run pytest tests/test_guarded_check_channel.py tests/test_execution_operations.py tests/test_al_sandbox_grants.py tests/test_al_service_relay.py tests/test_task_tooling.py`
- `uv run python al/verify_changed.py --base fef2cd1ec5650c7fba5cf9f056302d105f5c64a5`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest -m "not browser"`
- `uv build`
- `uv run pytest -m browser`

EVIDENCE_PLAN
- migrations | focused | cwd integration root | scope harness,tests,pyproject.toml,uv.lock,.github/workflows/ci.yml | preflight owned-sqlite | needs - | `uv run pytest tests/test_db.py tests/test_state_db_busy.py`
- execution | focused | cwd integration root | scope harness,tests,al,pyproject.toml,uv.lock,.github/workflows/ci.yml | preflight owned-runtime | needs - | execution command above
- al-fast | focused | cwd integration root | scope al,harness,AGENTS.md,CLAUDE.md,pyproject.toml,uv.lock,.github | preflight owned-runtime | needs - | exact `al/verify_changed.py --base ...` command above
- lint | broad | cwd integration root | scope repository source/configuration inventory including .github,pyproject.toml,uv.lock | preflight locked-python | needs - | `uv run ruff check .`
- format | broad | cwd integration root | scope same lint inventory | preflight locked-python | needs - | `uv run ruff format --check .`
- harness-tests | broad | cwd integration root | scope harness,tests,al,pyproject.toml,uv.lock,.github | preflight owned-runtime | needs migrations,execution | `uv run pytest -m "not browser"`
- build | broad | cwd integration root | scope harness,al,README.md,pyproject.toml,uv.lock,.github | preflight locked-python | needs - | `uv build`
- browser | broad | cwd integration root | scope harness,tests,al,pyproject.toml,uv.lock,.github/workflows/browser.yml,owned Chrome wrapper | preflight sandboxed-chrome | needs harness-tests | `uv run pytest -m browser`
- Required CI setup is `uv python install`, then `uv sync --locked`; preserve CI tool targets and flags. Linux CI installs ripgrep; macOS uses Homebrew ripgrep. Browser setup must reproduce the workflow’s sandboxed Chrome flags. No checks were executed here.
- AL slow flows deliberately modified by this integration require affected slow-tier selection through the repository runner; do not launch serial per-host pytest. Designer/Engineer must identify the actual changed slow flows before registering that selection. This remains verification debt, not an omitted pass.
- Recipe source hashes at inspected main: CI `d9598df1686a82661dd5e5a805655379114e8c0dc243fe4c10bfd117823bb219`; browser `8760c6f5166da734e5ea83b2123f634b9173c573f5e4157fa7aa25020fd95e76`; containment `53cd47429e7eca636e7c25c1276c38c8730594720b2a8b74b7d2a0e37c05bfdc`; release `f9cb03fc313d46757fd1f7baa8f577707e41b18c3a54d8545845553719d6f00f`; pyproject `8084bf5ceec025e82f8699b1abeed08a8d05365f5b90a8d5289ca831795f18e8`; lock `d1f4eb93144031415f3335ae20012345b26288a8350095dd02597c12986479b0`.

RUNTIME_PREREQUISITES
- locked-python: Python ≥3.13, uv, locked dev dependencies.
- owned-sqlite: disposable owned database paths; no production/default Harness state.
- owned-runtime: Git, ripgrep, isolated test homes/runtime storage, loopback and Unix socket permissions, platform containment prerequisites.
- sandboxed-chrome: executable configured through `HARNESS_BROWSER_EXECUTABLE`, owned profile and loopback; actual sandboxed launch proof.
- Linux containment: bubblewrap and `HARNESS_REQUIRE_OPERATION_TESTS=1` for the workflow’s exact four-test command. macOS evidence cannot discharge this obligation.
- Service coverage: identified local runtime, SQLite, browser and containment dependencies; exhaustive current discovery remains pending. No services or database connections were started during this inspection.

ACCEPTANCE_CRITERIA
- AC1: Rebased candidate includes verified remote main history and preserves the checkpoint’s implemented capabilities.
- AC2: All recognized migration histories upgrade without losing facts; incompatible histories refuse without mutation; current databases remain readable during concurrent writes.
- AC3: Every supported operation retains current authority, exact policy ownership and conservative unresolved liability.
- AC4: Main cache/service/intent interfaces operate without bypassing policy, source validation or sealed qualification.
- AC5: Main session, investigation, Knowledge, storage and installer behavior remains integrated with typed contracts and incident capture.
- AC6: Dashboard functionality and desktop/narrow presentation are verified on actual rebased source.
- AC7: Required current evidence and independent review precede ordinary publication; missing platform/service evidence remains explicit.
- AC8: Handoff distinguishes implemented checkpoint capabilities from remaining R5b–R11 work.

Open questions: none requiring a user decision.

```json
{
  "decisions": [
    {
      "decision": "Retain recovered Designer protocol with a narrow cache/service qualification amendment and a concrete lock-free current-database correction.",
      "reason": "Current main introduces new profile effects and explicitly preserves concurrent database readers."
    },
    {
      "decision": "Rebase the preserved checkpoint onto verified main; implement sequentially.",
      "reason": "Explicit user instruction and shared authority/closure surfaces."
    }
  ],
  "assumptions": [
    "The supervisor preserves the original checkpoint and captures this report.",
    "Main hashes refer to fef2cd1ec5650c7fba5cf9f056302d105f5c64a5."
  ],
  "files_touched": [],
  "verified_claims": [],
  "unverified_claims": [
    {
      "claim": "The eventual rebased candidate passes verification.",
      "reason": "No implementation or checks were performed in this read-only planning invocation."
    }
  ],
  "open_questions": [],
  "risks": [
    "Incomplete current service/check discovery must be resolved before final proof.",
    "Hash regeneration without auditing new cache/service effects cannot qualify sealed execution."
  ]
}
```