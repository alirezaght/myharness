The installed versions remain unsupported for **governed execution**, for a precise local reason: exact-version admission rejects them before native launch.

- Observed now: Claude **2.1.329** at `/Users/alirezaghiastabari/.local/bin/claude`; Codex **0.170.0** at `/Users/alirezaghiastabari/.nvm/versions/node/v24.14.0/bin/codex`.
- `harness/native_tool_policy.py:39–42` permits Claude `2.1.310`, `2.1.325`, `2.1.326` and Codex `0.164.0`, `0.165.0`.
- `executable_identity()` checks exact membership after observing the resolved executable’s version and SHA-256. `NativeRuntime.__init__()` and `launch_readiness.readiness()` consume that check.
- Codex `0.170.0` has a **separate authority-free authoring profile** in `harness/authoring_policy.py:67–104`. That is not execution qualification. Claude authoring currently shares the execution version set.
- Codex’s version command emitted a PATH-alias permission warning but returned its version successfully. No authentication or engineering request was attempted.

The retained R11 plan’s version observation is therefore still accurate. These observations do not establish model availability, authentication, containment or engineering efficacy.

**Qualification is an existing test workflow, not a one-command allowlist update.** `tests/native_model_fixture.py:15–46` independently restricts qualification candidates to the same older versions. Setting `HARNESS_TEST_CLAUDE_VERSION=2.1.329` or `HARNESS_TEST_CODEX_VERSION=0.170.0` today deliberately fails as an unsupported candidate; omitting the overrides can skip because the fixture defaults are older versions.

The next bounded implementation/qualification sequence is:

1. Reobserve the exact resolved executable identity immediately before qualification. Retain version, binary SHA-256, platform, source revision and test environment identity.
2. In the admitted qualification change, introduce explicit **test-only candidate admission** for these exact versions, preserving production refusal. Exercise the real policy builder with that candidate admission; do not mock successful native dispatch or broaden versions by range.
3. Run the existing native suites with mandatory-native mode so absence cannot become a skip. Once candidate admission exists, these are the concrete selections:

```sh
env HARNESS_REQUIRE_NATIVE_TESTS=1 \
  HARNESS_TEST_CLAUDE=/Users/alirezaghiastabari/.local/bin/claude \
  HARNESS_TEST_CLAUDE_VERSION=2.1.329 \
  uv run pytest tests/test_native_tool_policy.py -k 'prerequisites and claude'
```

```sh
env HARNESS_REQUIRE_NATIVE_TESTS=1 \
  HARNESS_TEST_CLAUDE=/Users/alirezaghiastabari/.local/bin/claude \
  HARNESS_TEST_CLAUDE_VERSION=2.1.329 \
  uv run pytest tests/test_claude_tool_boundary.py \
  tests/test_claude_child_boundary.py tests/test_claude_generated_policy.py \
  tests/test_claude_native_attestation.py tests/test_claude_agent_effects.py \
  tests/test_claude_handoff_native.py tests/test_claude_stop_native.py
```

```sh
env HARNESS_REQUIRE_NATIVE_TESTS=1 \
  HARNESS_TEST_CODEX=/Users/alirezaghiastabari/.nvm/versions/node/v24.14.0/bin/codex \
  HARNESS_TEST_CODEX_VERSION=0.170.0 \
  uv run pytest tests/test_native_tool_policy.py -k 'prerequisites and codex'
```

```sh
env HARNESS_REQUIRE_NATIVE_TESTS=1 \
  HARNESS_TEST_CODEX=/Users/alirezaghiastabari/.nvm/versions/node/v24.14.0/bin/codex \
  HARNESS_TEST_CODEX_VERSION=0.170.0 \
  uv run pytest tests/test_native_tool_boundary.py \
  tests/test_native_controller_lineage.py
```

These commands are future execution instructions, **not commands run here**. They require the locked project environment and disposable state. The native fixture supplies a local synthetic model service; it exercises installed CLI behavior without proving real-provider authentication or efficacy.

4. Preserve deterministic guard coverage: `test_claude_agent_guard.py`, `test_claude_stop_guard.py`, `test_native_tool_policy.py`, `test_authoring_native.py` and `test_native_diagnostics.py`. In particular, retain the assertion that authoring admission does not expand execution support.
5. Qualify actual host containment and current worker/policy interactions separately. `.github/workflows/containment.yml` explicitly requires `HARNESS_REQUIRE_OPERATION_TESTS=1` and runs `test_operation_isolation.py`, `test_guarded_check_channel.py`, `test_private_runtime_operation.py` and `test_execution_operations.py` on Linux with bubblewrap. That Linux evidence cannot substitute for the actual macOS boundary.
6. Add each exact production version only after its required profile/boundary evidence succeeds. Then re-run production admission and the same native paths without test-only admission. Retain unsupported refusal if a security-relevant protocol differs; route only that demonstrated protocol change separately.
7. Actual authenticated governed tasks, cancellation/settlement, engineering efficacy and usage qualification remain separate obligations. Successful synthetic protocol tests do not qualify token attribution, arbitrary native descendants or production authentication.

Existing tests cover useful concrete behavior: Codex root/child identity and resume across `ROLES` (`test_native_controller_lineage.py:24–175`); Claude child roles and resume (`test_claude_child_boundary.py:17–185`); forbidden handlers, hook failures, assignment delivery and owned-work stop handling in the listed Claude suites. Generated-policy coverage is narrower than every-role coverage and must not be described as exhaustive by itself.

Available prerequisites are the two installed binaries, versioned policy builders, synthetic protocol fixtures and existing test paths. Missing or unverified prerequisites are candidate admission, current locked runtime readiness, permitted loopback/host containment, exact-version results, complete cancellation/settlement coverage, actual provider authentication and measured usage. These are operational verification obligations, not user or Knowledge gates.

R11 changes are **not provably independent of R5a as a whole**:

| Area | Actual overlap or compatibility risk |
|---|---|
| `native_tool_policy.py`, `native_runtime.py`, `authoring_policy.py` | Version-specific feature gates, restricted catalogs, generated role policies and authoring/execution separation. Narrow qualification edits could have a separate write set, but their consumers must be rebound before integration. |
| `execution_worker.py` | R11 provider lifecycle/error normalization overlaps R5a immutable evidence preparation and event ingestion ownership; current native presence/admission/deadline handling must remain intact. |
| `execution_projection.py`, timeline/reliability APIs | R11 normalized observations and historical comparisons consume the same event identities and provenance R5a extends. |
| Existing dashboard/API routing | R11 composes Incident Intelligence and other areas that R5a changes. Shared UI, routes, response contracts and browser fixtures prevent an independent writer claim. |
| Both AL distributions | Fixed role/model policy must remain unchanged. R5a modifies ledger/review/outbox producers; R11 packaging or role-policy integration can affect those same artifacts and qualification fingerprints. |
| Test infrastructure | `native_model_fixture.py`, native boundary fixtures and packaging/runtime evidence are shared qualification inputs, even where production edits are disjoint. |

A later narrowly scoped native-qualification task can be assessed for disjoint ownership; this preparation does not authorize parallel writers. The broader R11 implementation should consume stabilized predecessor contracts. No files, configuration, allowlists, pins or credentials were changed, and no tests or services were started.
