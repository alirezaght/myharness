# Reliability mission capability inventory

Classifications describe the audited source at `2ad8bed9`. **F — Implemented and functional** is limited to the exact deterministic behavior checked this session. **P — Partially implemented** has usable primitives but lacks part of the requested scope. **I — Implemented but insufficiently integrated** exists without the required complete workflow. **M — Missing** was not found in the inspected tracked schemas and relevant source/callers. **D — Duplicated or architecturally inconsistent** requires a demonstrated conflict; none was established for a whole subsystem. Intentional Claude/Codex distribution mirrors are not automatically a D finding.

Current runtime evidence is in [verification.md](verification.md). F does not mean native-provider qualification, Knowledge K1–K9 acceptance or production readiness. Missing-component searches covered all numbered SQL migrations, Harness execution/Knowledge/dashboard modules, evaluation scripts and AL runtime/replay packages. File references below are repository-relative.

## Phase 0 and governance

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| AL supervisor, Architect/Designer/Engineer/Builder/Scout roles and risk-based routing | P | `al/codex/skills/al/SKILL.md`, `al/codex/agents/`, `al/claude/agents/`; existing governance, not a new reliability loop; full installed/native acceptance not run here |
| Requirement/interview/design/test-plan/versioned decision workflow | P | `harness/interview.py:51`, `design.py:23`, `testplan.py`, `decision_workflow.py:102`; current source supports human verdicts; reliability choices pending |
| Missing, altered or stale spec approval rejects implementation authority | F | `harness/approval.py:76`, `:117`, `execution_authority.py:18`; current `tests/test_approval.py` passes |
| Persistent local state, owner permissions, numbered migrations and compatibility upgrades | F | `harness/db.py:45`, `:92`; current `tests/test_db.py` passes for its explicit upgrade/privacy scenarios |
| Current SQLite runtime prerequisite | P | `tests/test_sqlite_capabilities.py:101` requires SQLite ≥3.51.3 or sourced backport; actual 3.50.4 fails that check (5 other capabilities pass). Existing fixture passes do not qualify concurrent WAL safety |
| CI/lint/format/build, integration/security/browser/release tiers | P | `.github/workflows/{ci,containment,browser,al-release}.yml`; representative tests run, full hosted suites/build/native/Chrome not established |
| Existing instructions and platform version ownership | P | `.claude/commands/harness.md`, both AL skills/roles, `al/release-layout.json`; parity/release workflows exist, not run in full here |

## Phase 1 Unified execution observability

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Stable execution/session/run IDs, spec version/hash and approval binding | F | `execution_contract.py:8`, `execution_projection.py:239`; current execution-event tests check binding/replay rejection |
| Versioned bounded event schema, sequence consistency and atomic idempotent ingestion | F | `harness/execution_contract.py:200`, `execution_projection.py:269`; identical replay accepted; conflicts/gaps rejected in current tests |
| Stable trace/span IDs across agents, epochs and workflows | P | Execution lineage/epoch IDs exist in migrations `0011`, `0014`; no complete span contract |
| Parent/child relationships, role, model/provider and Harness/policy identity | P | `execution_principals.py`, `native_tool_policy.py`, AL `al_models.py`; identities exist across stores, not complete unified observations |
| Tool calls/outcomes at real dispatcher; files modified/artifact references | P | `execution_operations.py:377`, `execution_files.py:216`, event ownership/output kinds; audits lack full timing/trace coverage |
| Approval requests/verdicts, decision versions, test/validation and final acceptance | P | `execution_decisions.py:164`, `execution_contract.py:23`; actual authority gates exist, complete boundary sensors missing |
| Retries/reasons, errors, timeouts, escalation and retrieval/evidence correlation | P | AL rework/recovery, `native_diagnostics.py`, `knowledge/integration.py:92`; fragmented sources |
| Token usage, latency, estimated monetary cost and pricing provenance | P | Knowledge usage/accounting helpers exist in `scripts/knowledge_evaluation/usage.py`, `scripts/knowledge_eval.py`; qualified native usage unavailable; no unified execution cost/latency model |
| Agent/tool timeline, retry/failure/cost/latency/policy/verification and historical comparison UI | P | `dashboard/static/execution.js`, `execution_activity.py`; current activity/check views tested, full trace/comparison view missing |
| Redaction before every persistence/export/index boundary | P | `execution_logs.py:145`, `execution_operations.py:399`; exception branch `:433` and event persistence `execution_projection.py:269` need explicit privacy regressions |
| Observation independent of optional agent reports; OpenTelemetry compatibility | P | Dispatcher audit is actual observed activity, AL committed state emits authoritative result kinds; role narratives remain reports. No OpenTelemetry exporter/complete boundary schema found |

## Phase 2 Runtime policy

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Allowed/prohibited tools and delegation roles | P | `native_tool_policy.py:13`, `:125`, `execution_operations.py:235`; supported host/tool admission exists; unified configurable engine absent |
| Tested read/write path/alias and operation subprocess boundaries | F | `execution_files.py:25`, `:216`, `execution_isolation.py:230`; current file and operation-boundary fixture checks pass within their scope |
| Structured command grammar and execution classification | P | `command_safety.py` and AL command guards exist; the selected tests do not qualify their complete grammar |
| Network, database targets and production access permissions | P | `command_safety.py`, operation sandbox, delivery environment controls; no general DB/production policy schema. No external database test |
| Subprocess/alternative tool/delegated/direct-provider bypass resistance | P | `execution_isolation.py:243`, `native_runtime.py`, `execution_worker.py:792`; operation tree containment and host restrictions differ. Native authentication/host remains separately trusted |
| Global/project/workflow/role/execution scopes with deterministic conflict rules | M | Existing approval/principal/Knowledge scopes are narrower; no unified composed policy in inspected admission/native-policy paths |
| Maximum writes and shared retry/token/cost budgets | M | AL retry/rework caps and byte bounds exist, but no requested centralized quantitative accounting/reservation system |
| Execution time limits, cancellation and bounded retries | P | `execution_processes.py:110`, `:221`; local deadlines/cancel/rework, not complete inherited execution budgets |
| Required approvals and default-deny without current authority | F | `approval.py:76`, `execution_authority.py:18`, current approval/event/epoch tests; exact operation-wide human capability model still incomplete |
| Operation/resource/scope/execution-bound approval tokens | P | `command_safety.py`, execution decision deliveries and principal policy bindings; no universal expiring quantitative capability schema |
| Explicit policy decision events and accurate security-boundary documentation | P | Real operation audit/native policy digests exist; no unified closed policy-event producer contract; [audit findings](audit.md) state actual boundary |

## Phase 3 Typed agent contracts

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Requirements, architecture, decisions, plans, assignments, implementation/verification/failure/escalation/final reports | P | AL schemas under `al/codex/skills/al/reference/schemas/`; `harness/design_models.py`, execution/decision/contribution contracts. Extend missing common fields/families rather than replacing them |
| Version, execution/task/spec, inputs/outputs/criteria/evidence/constraints/status/failure fields | P | Task-package/planning/review and execution contracts cover portions; no universal proof every requested handoff has every field |
| Reject invalid contracts, missing approvals/decisions/artifacts; actionable feedback | P | `execution_contract.py:200`, `execution_authority.py:116`, `execution_projection.py:239`; execution-envelope behavior checked; new families need ingress tests |
| Schema evolution and persisted compatibility without structuring private reasoning | P | Versioned schemas/readers and knowledge projection versioning exist; future reliability contract adapters not implemented |

## Phase 4 Failure intelligence

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Persistent incidents for implementation/test/architecture/spec/authorization/loop/dependency/API/tool/context/retry/budget/CI/staging/human rejection failures | M | Existing events/issues/logs/browser/delivery records are inputs; migration inventory has no complete incident registry |
| Incident ID/trace/category/symptoms/evidence/root-cause confidence/components/related incidents/failed controls/corrections/status | M | No normalized incident/revision model; reuse existing execution and Knowledge identities |
| Facts versus hypotheses; deterministic and model-assisted classification | M | Knowledge origin/verification/confidence primitives exist (`knowledge/models.py:93`), incident-specific classification absent |
| Related grouping/deduplication, search, investigation, human correction and Knowledge integration | M | General graph/search exists; no incident source or incident lifecycle in `harness/knowledge/` |

## Phase 5 Failure to improvement

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Evidence-derived instruction/validator/test/permission/contract/retrieval/planning/routing/sensor/escalation proposals | M | No incident-to-proposal registry or producer found; decisions and lessons are adjacent capabilities |
| Evidence/change/benefit/workflows/side effects/evaluation/rollback/approval per proposal | M | Existing decision recommendations are narrower than evaluated improvement lifecycle |
| Deterministic controls preferred to prompts; rule owner/scope/purpose/dedup/version/consolidation/retirement | P | Existing code controls and versioned decisions/lessons; no complete reliability rule inventory/retirement mechanism |
| Prevent self-authorized activation and require human approval for high impact | P | Existing AL/spec/decision gates are real; improvement activation and evaluated promotion are not implemented |
| Approved lesson export, exact import grants, revocation and unverified suggestions | F | `knowledge/lessons.py:129`, `:192`, `:330`; current lesson tests pass. This is not automatic failure-to-improvement |

## Phase 6 Evaluation lab

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Versioned registry covering all requested engineering/failure/task types | M | Existing knowledge experiment package and AL fixtures do not form a general engineering benchmark registry |
| Historical incident replay with reconstructable environment and honest fidelity | M | `al/codex/skills/al/scripts/al_replay.py:60` uses disposable public-command simulation and baseline comparisons; no incident reconstruction |
| Compare Harness/policy/workflow/retrieval/providers with model/task/tools/environment held fixed | P | AL replay and frozen Knowledge experiment inputs are reusable; full comparison runner/qualification missing |
| Completion/criteria/tests/regressions/policy/interventions/retries/tools/duration/tokens/cost/recovery/escalation metrics | P | Existing tests/events/accounting contain subsets. `scripts/knowledge_eval.py:150` measures accounting, not engineering outcome quality; unknown native usage stays unknown |
| Deterministic evaluation plus independent review, not self-confidence | P | AL actual evidence and isolated review exist; general benchmark scoring and native outcome acceptance missing |
| Prevent contamination/leakage/overfitting and require improvement before promotion | P | Packet-local scopes and explicit held-out restrictions exist (`docs/knowledge/recovery-20261119.md:12`); general release gate absent |
| Baseline comparisons, regression thresholds, canary and rollback | P | Replay comparisons exist; general improvement thresholds and monitored version rollout/rollback missing |

## Phase 7 Cross agent knowledge

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| One sourced graph for agents/tasks/specs/decisions/code/dependencies/tool execution | P | `0015_knowledge.sql:27`, `knowledge/adapters.py:154`, `extract.py:44`; partial static/dynamic/runtime relationship coverage |
| Incident/root-cause/test/sensor/policy/proposal/evaluation/Harness-version graph | M | General entities can represent them, but missing reliability sources prevent complete relationships |
| Queries about contributing decisions, similar failures, detecting tests, prevention policies, impacts, rejected proposals, version regressions and unresolved incidents | M | No integrated reliability graph for these eight questions; current lexical/entity query alone cannot supply missing facts |
| Provenance/time/source/fact/inference/decision/supersession and incremental refresh | P | Migrations `0015`, `0016`; snapshot/cursor/FTS/evidence/derivations; current query/flow tests check scoped provenance, broader K6/K9 acceptance open |
| Hybrid lexical plus bounded graph retrieval, with coverage and uncertainty | F | `knowledge/query.py:307`, `:398`, `:593`; current query/flow tests. Embeddings are not necessary merely to satisfy hybrid retrieval |
| Cross-agent context reuse without granting authority | I | `knowledge/integration.py:136`, `contributions.py:71`; scoped source-report ingestion exists but genuine donor/recipient and clean-review qualification remain closed |

## Phase 8 Adaptive recovery

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Plan/approve/execute/verify/recover/escalate with bounded retry/rework | P | AL `al_rework.py`, `al_rework_recovery.py`, `al_recovery.py`; general incident-aware diagnosis/strategy missing |
| Repeated failure, identical failed action and same-file/no-improvement stagnation | P | Caps/reassessment exist; no full outcome-sensitive edit-loop detector established by inspected paths |
| Retry/cost budgets and recovery strategy selection | P | Retry counters exist; shared cost accounting and evaluated strategy routing missing |
| Persisted checkpoints, safe resume and current identity fencing | P | `execution_hosts.py:171`, `execution_epochs.py:25`, planning/runtime recovery modules; fixture fencing checked, full native demonstration not run |
| Human escalation, cancellation and truthful terminal failure/unknown closure | P | Blocker decisions, `execution_processes.py:221`, `native_diagnostics.py`, current recovery record; general reliability integration missing |
| No silent architecture/criteria changes; side-effect idempotency/recovery | P | Sealed approval/amendment gates and owned operation records exist; arbitrary external rollback is not supported |

## Phase 9 Provider infrastructure

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Preserve Claude primary, explicit Codex adapter and same approved spec | P | DEC-0019; `execution_hosts.py:146`, `:171`; no silent provider substitution; native qualification untested here |
| Model identity/capabilities/tool calling/structured output/context/usage/cost/retry/timeout/error interface | P | Existing host adapters, fixed model catalog, native diagnostics and profile qualification cover subsets; no full provider capability/accounting contract |
| Cross-provider evaluation without spec changes; capability differences explicit | I | Host-specific paths already exist; no general fixed-case cross-provider engineering evaluation or qualified usage |

## Phase 10 Dashboard

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Execution Explorer traces/tool/cost/budget/retries/verification | P | Current `execution.js`, `execution_activity.py`, `dashboard/server.py:288`; functional scoped execution/log/activity reads checked; trace/cost comparison absent |
| Incident Intelligence history/root cause/related/components | P | R5a adds persisted observation sources, scoped API and dashboard investigations/group decisions/source descriptors; human hypotheses do not prove root cause. See [R5a contract](r5a-api-contract.md) and [progress/evidence](implementation-progress.md#r5a-incident-intelligence-dashboard) |
| Improvement Center evidence/evaluation/approval/deploy/rollback | M | Existing approval UI does not implement proposal promotion |
| Evaluation Lab benchmarks/version comparison/metrics/regressions | M | Existing scripts do not supply this dashboard |
| Knowledge Explorer entities/relationships/decisions/impact | P | `dashboard/knowledge_view.py`, `static/knowledge.js`; deterministic reads tested, reliability sources and current Chrome/native acceptance missing |
| Reuse framework/API/persistence and real durable data | F | Existing dashboard read tests pass against current SQLite; no replacement dashboard is needed |

## Phase 11 Engineering quality

| Capability | State | File evidence and remaining boundary |
| --- | --- | --- |
| Modular ownership/versioned contracts/durable persistence/safe migration/idempotency | P | Current modules/SQL migration runner/atomic event importer provide patterns; new reliability components not built |
| Explicit errors/structured logs/automated integration/security checks/documentation/backward compatibility | P | Existing native closed diagnostics, tests, schemas and CI; uniform privacy and all new reliability obligations remain |
| Existing stack, minimal dependencies and in-process architecture | F | `pyproject.toml` has no runtime third-party dependencies; stdlib HTTP/sqlite3 architecture; locked development setup completed |

## R4 typed exchange update (authored source after the audit snapshot)

The Phase 3 rows above describe the earlier audited source. R4 now has a closed version-1 typed companion at each of the ten actual families: requirement-analysis, architecture-proposal, design-decision, implementation-plan, task-assignment, implementation-result, verification-result, failure-report, escalation-request and final-delivery. Concrete producer/consumer paths, tests and package closures are indexed in [r4-implementation-inventory.md](r4-implementation-inventory.md); envelope and public-call semantics are in [r4-api-contract.md](r4-api-contract.md).

This update records the authored source, not a blanket classification of every requested mission capability as complete. Typed validation does not grant approval, capability, producer identity, verification or completion. Historical untyped records retain legacy behavior. Immutable snapshot validation remains distinct from current mutable-source validation; strict source consumption retains R3 predecessor claims and refuses unresolved legacy/native liability. The exact tested support area and remaining current-source/check limitations are recorded with EIDs in [implementation-progress.md](implementation-progress.md); the broader mission inventory and native/provider/Knowledge boundaries are unchanged unless explicitly updated in later evidence.

## R5a incident capability update (authored source after the audit snapshot)

The Phase 10 incident row above now reflects R5a's authored catalog, scoped
service and existing-dashboard workflow. It is partial: incident producers
cover the current supported event families, but the UI does not establish a
universal incident registry, proven root cause, automatic remediation,
improvement promotion or evaluation. See [the R5a API contract](r5a-api-contract.md)
and the EID/currency record in [implementation progress](implementation-progress.md#r5a-incident-intelligence-dashboard).
