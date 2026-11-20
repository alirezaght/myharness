# Reliability audit verification

> Status update: P1–P5 are now accepted as DEC-0048–DEC-0052. See [approved status and current evidence](approved-status.md); pending statements below describe the original audit checkpoint.
This record verifies selected existing behavior at `2ad8bed9`, not the proposed reliability platform. No product code changed. Results use disposable fixture repositories/databases; no provider credentials or external databases were exercised.

## Environment and setup

Locked `uv sync --locked` completed in the isolated worktree. Interpreter Python 3.13.12; SQLite 3.50.4. The managed worktree is beneath the user's `.codex` directory; restricted operations intentionally hide that directory. A second environment at `/private/tmp/al-reliability-audit-venv` uses the same lockfile/dependencies and current editable checkout, allowing the containment fixture to run without putting the interpreter inside a hidden credential root. No security policy was weakened.

The existing SQLite prerequisite was checked after documentation QA: `tests/test_sqlite_capabilities.py:101` requires ≥3.51.3 or an explicitly sourced fixed backport, with no backports allowlisted. **Current SQLite 3.50.4 fails this requirement.** The other five capability checks pass. Provision a supported interpreter and rerun this check before migration/concurrency qualification; do not waive or weaken it. Installed-runtime inventory offered no compliant Python 3.13 alternative. The current run does not satisfy all existing runtime obligations.

## Executed checks

| Check | Actual result | Meaning |
| --- | --- | --- |
| Existing SQLite capabilities | 5 passed, 1 failed in 0.07 seconds | FTS5/WAL basics work; the repository WAL prerequisite fails on SQLite 3.50.4 |
| Representative existing behavior, 11 files listed below | 113 passed, 2 skipped in 117.91 seconds | Approval, SQLite, event replay, activity metadata, generated native policy, scoped Knowledge/lessons/accounting and dashboard reads work in these fixtures |
| Operation isolation, file access and epoch principal selection | 21 passed, 1 failed in 21.86 seconds | Original actual-boundary case could not execute the worktree interpreter under hidden `.codex`; preserve as failed run |
| Same boundary case, external locked interpreter, ordinary outer sandbox | 1 failed | `sandbox-exec: sandbox_apply: Operation not permitted`; outer test environment could not apply the nested sandbox; preserve as failed attempt |
| Same boundary case, external locked interpreter, same approved execution context as original selection | 1 passed in 0.32 seconds | Fixture checked admitted cwd/write, protected read/write/hardlink denials and host service access. This proves that fixture boundary in this context only |

The two skips are `test_installed_native_profile_prerequisites` for the fixture-required installed Codex 0.164.0 and Claude 2.1.310. They report those exact profiles unavailable. They do not establish authentication failure, successful provider execution, or qualification of a different installed version.

Representative selection:

```text
.venv/bin/python -m pytest -q \
  tests/test_db.py tests/test_approval.py tests/test_execution_events.py \
  tests/test_execution_activity.py tests/test_native_tool_policy.py \
  tests/test_knowledge_flow.py tests/test_knowledge_lessons.py \
  tests/test_knowledge_evaluation.py tests/test_knowledge_query.py \
  tests/test_dashboard_execution.py tests/test_knowledge_dashboard.py \
  --junitxml=/private/tmp/al-reliability-audit-tests.xml
```

Boundary selection:

```text
.venv/bin/python -m pytest -q tests/test_operation_isolation.py \
  tests/test_execution_files.py tests/test_epoch_principals.py \
  --junitxml=/private/tmp/al-reliability-audit-boundaries.xml
```

Successful controlled recheck:

```text
/private/tmp/al-reliability-audit-venv/bin/python -m pytest -q \
  tests/test_operation_isolation.py::test_actual_operation_boundary \
  -p no:cacheprovider \
  --junitxml=/private/tmp/al-reliability-audit-containment-external.xml
```

## Failure investigation

The original failure was `sandbox-exec: execvp() .../.codex/worktrees/.../.venv/bin/python failed: Operation not permitted`, with exit 71 before the child assertions. `harness/execution_isolation.py:306` adds the user's `.codex` tree to hidden roots; `:382` emits file-read denials. Moving only the locked test interpreter outside that root removed this path conflict. The first external recheck used the ordinary enclosing sandbox and failed earlier at `sandbox_apply`. Repeating with the same approved host execution context as the original run passed. Source, assertions, fixture policy and credential-root protection were unchanged. Neither failed attempt establishes a policy bypass; neither is silently reclassified as a pass.

## Source findings requiring future checks

- `execution_operations.py:433` persists arbitrary caught error text without the normal redaction projection.
- `execution_projection.py:269` persists original validated semantic payloads, including text fields, without a uniform pre-persistence privacy projection.
- No synthetic secret canary was injected into these persistence paths during the audit; exposure remains a source-backed risk rather than a reproduced leak.
- A standalone native isolation/provider-proxy helper is not proof that the production native worker calls it. Real operation subprocess containment and native host authentication have different scopes.

## Verification limits

No full repository or AL release suite, full lint/format/build, hosted Linux/macOS job, live native agent workflow, engineering quality benchmark, current Knowledge K1–K9 acceptance, or Chrome UI acceptance was run. Source-only audit does not require new UI, but any implementation must check desktop/narrow function and visual alignment, focus, overflow and scrolling. No visual defect claim follows from source inspection.

SQLite check command: `/private/tmp/al-reliability-audit-venv/bin/python -m pytest -q tests/test_sqlite_capabilities.py -p no:cacheprovider --junitxml=/private/tmp/al-reliability-audit-sqlite.xml`.

The public JUnit copies in `evidence/` retain the exact selected test results and initial failures. They may contain local fixture/worktree paths and synthetic test payloads. They are not an AL evidence-admission or approval receipt. The protected audit ledger is at `.codex/al-runtime/reliability-audit-20261119.json`; implementation and formal final AL approval remain pending.
