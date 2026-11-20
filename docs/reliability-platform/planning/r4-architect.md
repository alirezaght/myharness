SPEC_STATUS: SPEC_READY

TASK_CLASSIFICATION: STANDARD
CLASSIFICATION_REASON: R4 adds typed records to existing artifact publication, assignment delivery and consumption checks. It preserves the existing authority and transaction protocols; schema validation does not grant authority.
IMPLEMENTER: both
IMPLEMENTER_REASON: Engineer implements boundary validation and adapters; Builder completes compatibility fixtures, both-platform parity and public-path integration tests.
DESIGNER_REQUIRED: NO
DESIGNER_REASON: Existing immutable artifacts, admission checks and generated writer receipts supply the required mechanisms. No new approval, distributed transaction or authority protocol is needed.

SPEC_SUMMARY

Implement the mission’s ten typed handoff families at actual AL/Harness exchange boundaries. Each carries version, execution/task context, specification version, inputs, outputs, acceptance criteria, evidence, constraints, status and failure classification. Reject invalid or stale required handoffs before downstream admission, while retaining existing persisted formats and independent authority checks.

ASSUMPTIONS

- A1: Structured exchange metadata may reference existing immutable payloads; internal reasoning and Markdown narrative remain unrestricted.
- A2: “Execution identifier” is the actual AL run identity for standalone AL. Harness execution ID is additionally populated when bound. Pre-execution requirement/design records explicitly say `not_started`; never fabricate an execution.
- A3: Ordinary artifact storage remains available for supporting material. Only declared orchestration contracts require typed validation; storage alone never makes an artifact eligible for consumption.
- A4: R4 does not depend on whole Knowledge mission acceptance.

INVARIANTS

- I1: A valid contract is data, never an approval, capability, completed task or successful verification.
- I2: Existing producer identity, epoch, graph revision, specification, dependency, evidence and approval checks remain authoritative.
- I3: Required inputs resolve to exact immutable bytes and the required current revision. Names, headings and claimed status cannot substitute.
- I4: Failed validation leaves accepted contract indexes, task readiness and downstream admission unchanged.
- I5: Old artifacts remain readable with explicit legacy coverage. No rewritten historical bytes, retroactive typed-success claim or silent runtime-pin upgrade.
- I6: Contract errors identify fields and repair actions without echoing sensitive submitted values.
- I7: A dependency may provide current authored work without verified completion where existing policy allows it. R4 must preserve this distinction.

IMPLEMENTATION_PLAN

**Inspected boundary inventory**

| Requested family | Existing concrete seam | R4 addition |
|---|---|---|
| Requirement analysis | `harness/interview.py:78,83,273`; accepted package in `al/.../al_task.py:13` | Typed summary/checkpoint adapter with actual unresolved questions and requirement references; analysis is not acceptance. |
| Architecture proposals | `harness/design.py:75` `ArchitectureDesigner.propose()` | Reference exact proposal/coverage state; include alternatives, rationale and decision requirements. |
| Design decisions | `harness/decision_workflow.py:46,149`; `harness/interview.py:179` | Reference actual recorded verdict/version and registry provenance; never accept an agent’s `approved` assertion. |
| Implementation plans | `al/.../al_planning.py:153,248,269` | Exchange adapter around existing planning report/admission records; preserve their original schemas and bindings. |
| Agent task assignments | `al/.../al_writer_handoff.py:40`; `harness/assignment_handoff.py:15` | Typed receipt accompanying generated writer body, binding inputs, expected outputs, criteria and ownership. |
| Implementation results | `harness/execution_artifacts.py:133`; `al/.../al_artifact_cli.py:36` | Explicit result contract with authored output references and limitations; distinguish authored, verified and completed. |
| Verification results | Existing evidence ledger and typed review/planning proof consumers | Reference actual ledger/report records, candidate and scope; aggregate status derives from those records. |
| Failure reports | `al/.../al_operational_issues.py:10`; `harness/execution_contract.py:411` | Typed failure exchange referencing diagnostic evidence and classification, preserving operational issue routing. |
| Escalation requests | `al/.../al_execution_tasks.py:287` | Adapter for existing typed business blockers; operational failures remain issues, not fabricated developer decisions. |
| Final delivery reports | Existing delivery projection and completion bindings, `harness/execution_contract.py:345,522` | Delivery contract references actual authored work, verification, unresolved limitations and approval state; no automatic activation. |

Here `al/...` means both `al/codex/skills/al/scripts` and its Claude counterpart.

**Step 1 — closed exchange representation**

Add a small AL-owned validator/adapter module, proposed `al_agent_contracts.py`, plus its schema and Harness adapter. Use one common metadata envelope with ten discriminated payload types, not a new universal execution API.

Required envelope:

- `schema_version`: exact integer `1`.
- `kind`: one of the ten named families.
- `context`: actual AL run/session, Harness task/execution where applicable, subtask, graph revision, producer identity and current epoch/generation where applicable. Absence uses explicit applicability/reason.
- `source_specification`: immutable package/document reference and effective amendment/version binding.
- `required_inputs`: typed references with purpose and required/current semantics.
- `expected_outputs`: named kinds and scope, not promises of success.
- `acceptance_criteria`: references to actual registry IDs and source binding.
- `evidence_refs`: existing immutable evidence/report references, possibly empty with reason.
- `constraints`: references or bounded explicit constraints.
- `status`: family-specific closed values.
- `failure`: `null` or closed classification plus immutable diagnostic reference and bounded repair guidance.
- `payload`: family-specific fields or reference to an existing typed authoritative payload.

Use existing bounded JSON parsing and artifact reference formats. Do not introduce a JSON Schema runtime dependency into the stdlib Harness.

**Step 2 — assignment producer and consumer**

Extend `al_writer_handoff.cmd_handoff()` to publish a typed assignment receipt with the generated prompt. Retain the body and managed evidence block unchanged as presentation.

Generate trusted fields from current writer/task/package/evidence state. Explicitly declare required input references and expected result contract. Validate referenced bytes before saving the receipt.

Extend `assignment_handoff._selected()` to verify the typed receipt in addition to its existing writer, phase, graph, plan-digest and presentation checks. The resource binding includes the typed receipt identity, preventing pagination against a superseded assignment.

Do not infer a complete plan from Markdown headings. Existing planning admission remains required.

**Step 3 — actual output submission and consumption**

Add an explicit typed submission path to the existing AL artifact and Harness `submit_output` surfaces. Do not guess contract kind from filenames.

Validate syntax, family, supplied binding and authenticated producer context before publishing the accepted typed record. Preserve generic supporting output publication as non-contract storage.

At `require_authored_report()` and `require_dependency_outputs()`, require the declared output contract for newly typed assignments; continue resolving actual output bytes and producer provenance. Existing completed-agent and candidate checks remain in force.

An output can be typed and authored while still unverified. Its contract must not manufacture a completion binding.

**Step 4 — reuse existing specialized validators**

Implement adapters for all ten families. Planning, review, decision, completion and evidence payloads retain their specialized validation. The exchange layer checks references and context, then invokes the existing consumer.

For `integration handoff`, currently `{from,to,text}` at `al_execution_tasks.py:213,477`, add an explicit typed-reference form for new typed task exchanges. Keep legacy text as historical semantic information. Neither form admits a writer by itself.

**Step 5 — compatibility and errors**

Newly generated assignments declare contract requirements. Old receipts lacking that declaration retain existing behavior and are labeled legacy, without a synthetic contract.

Unknown contract versions fail closed at required consumption with supported-version guidance. Merely viewing an old artifact succeeds.

Return a bounded error object containing:

`code`, `contract_kind`, `schema_version`, `field_pointer`, `expected`, `repair_action`, `state_changed:false`.

Use fixed descriptions for binding mismatch, unresolved artifact, stale specification, missing approval, unsupported version and wrong producer. Do not include raw rejected values.

INFRASTRUCTURE_CONSTRAINTS

- No database migration, Pub/Sub subscription, provider activation or new service is required.
- Inspected CI: `.github/workflows/ci.yml` uses locked `uv sync`, Ruff lint/format, non-browser pytest and build on Linux/macOS. AL release verification is `.github/workflows/al-release.yml`, including owned prerequisite checks and both-platform verification.
- No timeout value is inferred; these inspected jobs declare no explicit `timeout-minutes`.
- Existing runtime pins remain immutable. Packaging changes require existing release/parity verification; no installed runtime or other task is upgraded automatically.

CORE_SCOPE: Envelope validators, family adapters, typed writer receipt, output submission/consumption checks, compatibility behavior and focused rejection tests.

SUPPORT_SCOPE: Public CLI/Harness integration fixtures, ten-family matrix, legacy fixtures, parity/package checks and documentation.

INTEGRATION_POINTS: Existing immutable artifact stores, writer handoff generation, authenticated assignment pagination, `submit_output`, dependency admission, planning/evidence/review validators and decision registry.

EDGE_CASES: Pre-execution records; standalone AL identity; unknown version; stale graph/specification; amended plan; missing artifact; modified bytes; incomplete producer; current authored but unverified dependency; legacy receipt; output replay; declined proposal; operational failure incorrectly submitted as business escalation.

RISKS: Overstating authority through metadata; accidentally blocking allowed authored dependencies; silently upgrading legacy artifacts; drifting duplicated AL platform copies.

WRITE_OWNERSHIP

CORE_FILES:
- Both AL platforms: new `scripts/al_agent_contracts.py` and family-adapter modules; `al_writer_handoff.py`, `al_artifact_cli.py`, `al_execution_tasks.py`.
- Harness: new `harness/agent_contracts.py`; `assignment_handoff.py`, `execution_artifacts.py`, `execution_authority.py`, and the minimal `execution_operations.py`/`execution_mcp.py` wiring.
- Specialized producer modules only for explicit adapter calls; preserve existing authoritative validators.

SUPPORT_FILES:
- New focused contract tests under `tests/` and both AL test trees.
- Existing `test_writer_handoff.py`, `test_artifact_consumers.py`, parity manifests and handoff/artifact reference documentation.

SHARED_FILES: Any Engineer-authored focused tests subsequently extended by Builder; sequence ownership.

PARALLEL_ASSESSMENT

BUILDER_EARLY_WORK: none.
BUILDER_DEFERRED_WORK: Integration, compatibility and parity work after core interface stabilization.
INTERFACE_STABILITY: New typed receipt and submission arguments are unstable until Engineer handoff.
SHARED_STATE_COMMANDS: Packaging/parity updates and shared verification must be sequenced.
PARALLEL_SAFE: NO.
REASON: Builder tests consume the changed assignment and submission contracts.

TEST_PLAN

- T1: Public handoff generation → authenticated paginated assignment → typed result submission → dependency consumption. Catch a schema that exists but is never enforced.
- T2: Parameterized ten-family valid/invalid records through actual adapters. Catch mismatched kind, missing fields and false approved/success claims.
- T3: Missing/tampered input, stale graph/specification and wrong producer through actual admission paths. Assert no downstream writer/tool starts and no readiness mutation.
- T4: Current authored output remains consumable where allowed; malformed or stale authored output does not. Catch accidental blanket completion gating.
- T5: Legacy receipt/report fixtures remain readable and existing pinned behavior remains unchanged; unsupported new version is rejected at required consumption.
- T6: Existing typed planning/review/evidence/decision validators reject forged underlying payloads even inside a valid envelope.
- T7: Errors contain actionable field pointers but no submitted secret sentinel.
- T8: Both platform distributions expose equivalent contracts and pass existing parity checks.

VERIFICATION_COMMANDS

After implementation and locked environment provisioning:

- `uv run pytest tests/test_agent_contracts.py tests/test_artifact_pagination.py`
- `uv run pytest al/codex/skills/al/tests/test_agent_contracts.py al/codex/skills/al/tests/test_writer_handoff.py al/codex/skills/al/tests/test_artifact_consumers.py`
- Corresponding Claude focused tests.
- CI recipes unchanged: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -m "not browser"`, `uv build`.
- Existing AL release verification through its managed prerequisite/profile route; do not substitute a count-only local run.

These are proposed checks, not execution claims.

EVIDENCE_PLAN

Focused contract evidence covers both platform validators/adapters, Harness producer/consumer files, fixtures, artifact helpers, authority validators, manifests, `pyproject.toml`, `uv.lock` and CI recipe sources. Broad evidence uses the existing CI and AL release recipes after stable source capture. The supervisor must register exact recipes and full dependency scopes before writer handoff; this planning inspection does not supply final gate discovery.

RUNTIME_PREREQUISITES

Supported Python/SQLite, locked dependencies, ripgrep, and owned disposable artifact/runtime directories. No network provider, authentication change, external database or service required for contract tests.

ACCEPTANCE_CRITERIA

- AC1: All ten requested families have validated versioned exchange records consumed at actual orchestration boundaries.
- AC2: Every required common field is present or explicitly inapplicable with a legitimate lifecycle reason.
- AC3: Missing approvals, decisions or required artifacts prevent affected downstream execution through existing authority checks.
- AC4: Invalid/stale contracts produce actionable sanitized errors without changing accepted state.
- AC5: Typed metadata cannot forge approval, verification, task completion or producer identity.
- AC6: Required references resolve exact current immutable bytes; altered or missing bytes are rejected.
- AC7: Historical artifacts remain readable; old pins remain unchanged; no fabricated conversion or success.
- AC8: Current authored dependency consumption continues where existing policy allows it.
- AC9: Both AL platforms and Harness pass actual boundary integration and compatibility tests.
- AC10: Internal reasoning and general supporting artifacts remain free-form; contract enforcement applies to declared exchanges.

Source-bound routing evidence captured during inspection:

- `harness/assignment_handoff.py`: `086aa8e7172c0ab1679a3e0b46ab10f2b19283c8250c8cdb817e47813e87b126`
- `harness/execution_artifacts.py`: `4f36c1a56a5aa69d23f8d806e376e2bd1731ba591c4cd446dc2b6e3269c1af56`
- `harness/execution_authority.py`: `7a7b318fa881235000a62b8e921e8ab62b0ef06278b3bba3117af6397a099a91`
- `al/codex/skills/al/scripts/al_writer_handoff.py`: `0deb9093866878cf148309ff61efba5d1eeaaab146358ac4ec3dc3912f1a02ea`
- `al/codex/skills/al/scripts/al_execution_tasks.py`: `eefe1b69094ebbab3c77a6fb751d2aa63bf4a3578da791e85763cf937b881b0d`

Routing assessment: `scope=cross_component`, `existing_pattern=true`, `behavior_clear=true`, `hard_parts=[]`. Refresh these bindings against the stabilized implementation base before admission.
