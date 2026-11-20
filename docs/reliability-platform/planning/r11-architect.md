SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: STANDARD
CLASSIFICATION_REASON: R11 introduces a provider-facing contract over existing qualified native adapters and completes dashboard integration using predecessor APIs. It preserves existing authority, process ownership and role-routing protocols.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer implements capability/admission/measurement adapters and comparison APIs; Builder completes dashboard composition and compatibility/browser coverage.
DESIGNER_REQUIRED: NO
DESIGNER_REASON: No new provider transport, permission model or recovery protocol is proposed. If exact-version qualification reveals a changed security protocol, route that bounded change separately through Designer.

SPEC_SUMMARY

Preserve Claude as a primary supported provider and the existing Claude/Codex integrations. Expose explicit capabilities, validate compatibility before dispatch, normalize safe observations without inventing measurements, and complete all five dashboard areas through existing component APIs.

ASSUMPTIONS

- A1: Provider abstraction describes supported behavior; it does not promise identical capabilities.
- A2: AL role/model/effort policies remain authoritative and platform-specific.
- A3: R3, R5–R9 and later R10 own their business behavior. R11 composes their results rather than implementing competing versions.
- A4: Missing authentication or native qualification is an operational verification obligation, not a specification block.

INVARIANTS

- I1: Installation, protocol support, authentication, containment qualification and product acceptance are separate states.
- I2: No implicit model downgrade, provider substitution or installed-pin upgrade.
- I3: Existing approval, role, principal, epoch and operation-policy checks remain authoritative.
- I4: Unknown token/cost/context measurements remain unknown; bytes are not tokens.
- I5: Normalized retry advice cannot execute a retry or override R3/R10.
- I6: Dashboard reads grant no authority; mutation actions use their existing owning services.

IMPLEMENTATION_PLAN

### Actual inspected state

- `harness/native_runtime.py:39` rejects custom Codex providers without a verified restricted profile.
- `NativeRuntime` already builds provider-specific policies and native argv while sharing guarded operations.
- Claude uses role-specific MCP channels and identity hooks; Codex uses its restricted configuration/catalog and principal registration.
- `harness/native_tool_policy.py:39` explicitly allows execution versions:
  - Claude `2.1.310`, `2.1.325`, `2.1.326`.
  - Codex `0.164.0`, `0.165.0`.
- Harmless version commands now report **Claude 2.1.329** and **Codex 0.170.0**. Neither is in that execution allowlist. Codex emitted a PATH-alias permission warning but returned its version successfully.
- No authentication, live provider request or native efficacy test was attempted.
- `launch_readiness.py:24` checks installed protocol/resources and dependency layout. Its response explicitly reports authentication unverified and implementation/product acceptance unassessed.
- `authoring_native.py` has a separate authority-free authoring path. Authoring support must not imply execution qualification.
- Both `al_models.py` files pin role-specific model/effort and approved fallback slots. Codex and Claude slot definitions differ; preserve them.
- Current dashboard contains execution, reliability, timeline and Knowledge UI. `compare.js` compares diagrams; it is not historical execution comparison.
- `timeline.js:156` explicitly labels token/cost/native-child observations unavailable.

### 1. Provider profile and capability contract

Add `harness/providers/` with a small closed contract and Claude/Codex adapters.

A profile contains:

- Provider/host ID and adapter version.
- Native executable version/hash and selected runtime/package identity.
- Operation mode: authoring or governed execution.
- Requested role/model/effort and actual observed identity where qualified.
- Capability entries with `supported|unsupported|unverified`, qualification reference and limitations.
- Tool transport, structured-output mode, context-limit knowledge, usage accounting, cancellation, resume and error-normalization support.
- Authentication readiness as a status only; no credentials or raw configuration.

Use existing native builders for launch behavior. Do not create a second subprocess launcher, scheduler or direct provider API path.

### 2. Compatibility before dispatch

Introduce `validate_provider_request()` before existing launch preparation, returning a qualified profile receipt or structured refusal.

Check:

1. Exact executable identity and supported operation mode.
2. Selected AL package and fixed role slot.
3. Model and effort together.
4. Required tool/structured-output/context capabilities.
5. R3-enforced budgets and deadlines against actual measurable/enforceable capabilities.
6. Existing provider-specific policy and containment qualification.

Bind the receipt to executable/package/profile hashes and revalidate at dispatch. Source drift requires a new receipt.

An explicitly configured unsupported token/cost cap fails closed. An unspecified cap preserves legacy behavior without inventing usage.

Do not inspect or expose authentication material to populate this descriptor.

### 3. Safe normalized observations

Define a versioned observation containing:

- Execution/epoch/run/role correlation.
- Requested identity versus qualified observed identity.
- Provider/profile/version references.
- Lifecycle outcome and settlement status.
- Closed error category: configuration, unsupported capability, authentication, availability/rate limit, timeout, cancellation, protocol, containment or unknown.
- Retryability: `yes|no|unknown`, with supporting method/reference.
- Safe diagnostic code; no arbitrary stderr text.

Do not infer authentication failure or rate limiting from an unqualified free-text substring. Preserve `unknown` when the provider supplies no supported typed signal.

Provider errors remain distinct from task/test failure and uncertain process settlement.

### 4. Usage and cost

Each metric carries value/unit, scope, collection method, source reference, status and completeness.

Keep separate:

- Input/output/cache/reasoning tokens where qualified.
- Requested answer limits versus measured generation usage.
- Model alias versus observed revision.
- Estimated monetary cost versus actual billed amount.
- Owned-process duration versus provider inference latency.

Cost estimates require qualified usage plus a frozen versioned price basis, currency and pricing assumptions. Missing input produces null, not zero. Subscription access does not imply zero marginal or billed cost.

Do not scrape private transcripts. Existing scoped native usage refusal remains intact until separately qualified.

### 5. Controlled provider comparisons

Feed profiles into R7’s frozen experiment manifest.

- Harness-effect experiments keep provider/model/tool/task/environment inputs fixed.
- Provider-effect experiments explicitly name provider/model as the treatment.
- Reject incompatible required capabilities before either arm starts.
- Preserve exact AL role slots; evaluation cannot silently substitute arbitrary models.
- Record unsupported arms and incomplete pairs without excluding them from reported coverage.
- Native efficacy requires actual qualified engineering runs; adapter fixtures establish protocol behavior only.

### 6. Complete the five dashboard areas

| Area | Existing/prior owner | R11 completion |
|---|---|---|
| Execution Explorer | R1/R2 traces/activity; R3 budgets; R10 retries/recovery | Provider identity/readiness, actual budget/usage labels, links to verification, measured historical run comparison. |
| Incident Intelligence | R5/R5b catalog, investigations, corrections and grouping | Cross-links from execution/provider failures; consistent scope and provenance presentation. |
| Improvement Center | R6 proposals; R8 approval/deployment/rollback | Compose actual candidate/evaluation/activation states; do not derive deployment from proposal approval. |
| Evaluation Lab | R7 benchmarks, paired results and eligibility | Provider capability comparison, frozen treatment display and missing-measurement explanations. |
| Knowledge Explorer | R9 entities, queries and evidence | Reliable cross-navigation to incident/decision/component/candidate/evaluation sources. |

Use existing HTML/JavaScript/CSS patterns and APIs. No new frontend framework or dashboard application.

Historical comparisons must show:

- Exact selected execution identities and coverage.
- Task/specification, Harness/policy/provider differences.
- Comparable metrics with shared units/scope.
- Unavailable or incomparable cells.
- Descriptive differences versus controlled experimental conclusions.

Never label a naturally faster historical run as evidence that a Harness version improved performance when inputs differ.

### 7. Live qualification obligations

Before claiming native execution support for the presently installed versions:

- Reobserve executable version/hash.
- Run exact-version profile/root/role/start/resume/tool-denial/identity-hook/cancellation qualification.
- Verify existing containment and principal boundaries on the actual supported host.
- Record authentication availability without changing auth configuration.
- Run ordinary synthetic governed tasks and preserve refusal/cleanup evidence.
- Qualify usage independently; successful task execution does not qualify token attribution.

Current Claude 2.1.329 replaces the earlier 2.1.328 observation as the version to inspect. These checks are outstanding, not passing claims. No global installation or allowlist expansion is authorized merely by this plan.

INFRASTRUCTURE_CONSTRAINTS

No new service, provider transport or framework. Reuse existing persistence; add only a bounded versioned receipt/measurement migration if predecessor storage cannot retain the required fields. No historical measurement backfill or GET mutation.

Preserve locked CI recipes and sandboxed Chrome setup. Installed binaries are not evidence of authentication or native qualification.

CORE_SCOPE: Provider profiles, admission compatibility, normalized safe observations/measurements and historical comparison API.

SUPPORT_SCOPE: Five-area composition, source links, compatibility fixtures and browser verification.

INTEGRATION_POINTS: NativeRuntime, native policy builders, launch readiness, fixed AL model routing, R2 timeline, R3 budgets, R7 experiments and predecessor dashboard APIs.

EDGE_CASES: Unsupported version, custom provider, authoring-only capability, model/effort mismatch, changed binary, unknown usage, subscription pricing, incomplete pair, timeout with unsettled process, stale dashboard request.

RISKS: Overclaiming provider parity, exposing diagnostics, bypassing role policy, treating missing usage as zero, duplicating predecessor state machines.

WRITE_OWNERSHIP

CORE_FILES:
- New `harness/providers/` contract/adapters.
- Minimal `native_runtime.py`, `native_tool_policy.py`, `launch_readiness.py` integration.
- New historical comparison projection/API.
- Existing R3 mapping points only after stable contract revalidation.

SUPPORT_FILES:
- Dashboard view/static composition and scoped styles.
- Provider contract/readiness/comparison tests.
- Browser tests and qualification documentation.

SHARED_FILES: Public profile/result contracts and focused tests; sequence Engineer then Builder.

PARALLEL_ASSESSMENT

BUILDER_EARLY_WORK: none.
BUILDER_DEFERRED_WORK: UI and integration coverage after core contracts stabilize.
INTERFACE_STABILITY: Provider receipt and comparison fields are new.
SHARED_STATE_COMMANDS: Packaging, migrations and broad verification sequenced.
PARALLEL_SAFE: NO.
REASON: Dashboard and fixtures consume changed backend contracts.

TEST_PLAN

- T1: Both adapters preserve existing launch argv and security configuration.
- T2: Unsupported version/mode/model/effort/capability refuses before dispatch.
- T3: Existing approved fallback slots work; unapproved downgrade/provider substitution fails.
- T4: Unknown usage/cost remains null; unsupported explicit enforcement fails closed.
- T5: Provider failures normalize without raw-secret leakage or false task-failure classification.
- T6: R7 receives exact profile/treatment bindings and rejects incompatible pairs.
- T7: Historical comparisons preserve units, coverage and confounders.
- T8: All five dashboard areas navigate actual persisted predecessor records.
- T9: Chrome verifies task/execution switching, delayed responses, comparison selection, keyboard/focus behavior and desktop/narrow alignment.
- T10: Separate actual native qualification proves only its measured capabilities; mocked protocol tests remain labeled accordingly.

VERIFICATION_COMMANDS

Register focused provider/readiness/comparison tests, then existing CI recipes: Ruff check/format check, non-browser pytest, browser pytest and build. Run native qualification separately through existing governed test paths when operational prerequisites are satisfied.

No tests or authentication calls were performed here.

EVIDENCE_PLAN

Separate source/contract tests, installed binary observations, native boundary qualification and engineering efficacy. Include predecessor API contracts and R3 implementation hashes in evidence scope after stabilization.

RUNTIME_PREREQUISITES

Locked supported Python/SQLite; disposable local state; Chrome for UI. Actual native checks additionally require exact qualified CLI/profile, supported role models, containment and existing authentication availability.

ACCEPTANCE_CRITERIA

- R11-01: Claude/Codex remain supported through their existing integrations and fixed AL role policies.
- R11-02: Capability/configuration mismatches fail before dispatch.
- R11-03: Provider observations distinguish requested, observed, unsupported and unknown states.
- R11-04: Usage/cost claims have qualified provenance and correct semantics.
- R11-05: Provider comparisons preserve frozen controlled inputs.
- R11-06: All five dashboard areas compose actual predecessor behavior without duplicate authority.
- R11-07: Historical comparisons expose comparability limits.
- R11-08: Live qualification limitations are visible and cannot become implied passes.
- R11-09: No new permission, authentication, provider transport or pin-upgrade path is introduced.
- R11-10: Functional, compatibility, security and visual browser evidence covers the stabilized candidate.

Inspection hashes requiring later revalidation:

- `native_runtime.py`: `eb98a42b3f359e14b17ca6fe00d9ae4e71eb50c2b01a2d6dd0c21534a03f5fe1`
- `native_tool_policy.py`: `12c23966480b3d764155e43b0f0591a2fc17c3a41d7fb6e2ecdf9dfd1e254164`
- `launch_readiness.py`: `b2a84965d727fcc71ffec8d679cb6254092d1ba6fafce1af6e8ef16598e1ec35`

These are planning observations, not qualification or implementation approval.