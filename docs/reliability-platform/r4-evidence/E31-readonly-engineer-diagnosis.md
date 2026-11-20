E31 contains three distinct causes; no edits or checks were performed.

- **Seven `test_task_tooling.py` failures:** `task_environment.task_unit:45–56` now dereferences execution/controller state before the old path/template/installer branches. These direct helper fixtures supply `None` controllers and incomplete task rows. Pure path/template calls should explicitly use metadata-only validation. Actual `prepare()` installer/cache tests should use a genuine admitted execution with complete dependency metadata; making `prepare()` metadata-only would bypass the required effect boundary.

- **Scoped setup failure:** `test_execution_operations.py:385` installs a synthetic task without `dependencies`. Public dispatch reaches `resources.prepare → resource_bindings.derive → agent_contracts.dependency_records:300`, which raises `KeyError('dependencies')`. The test has **not reached polling**. Supply the real task shape/public registration and preserve its no-product-writer and poll assertions. Existing polling and background revalidation already correctly pass `verify_outputs=False` at `execution_operations:791` and `:1414`.

- **Continuity drift failure:** `test_execution_continuity.py:173` directly invokes byte validation without its owning Attempt. `execution_authority:205–210` correctly refuses before reading. Its genuine legacy AL predecessors cannot be erased to manufacture strict success. Preserve this refusal in the lifecycle test, and preserve the original `"bytes changed"` assertion in a separate supported public consumer case using the existing strict fixture and real dispatch reservation.

Two additional source-backed continuation seams need focused repair/proof:

- `execution_operations.tick:1383–1388` redispatches an unowned queued effect only when policy bindings exist; the no-policy fallback calls `prepare()` without admission. Fresh effects must enter public dispatch regardless of optional budgets. Stored owning setup Attempts should retain the existing restore/revalidate path.
- `al_command:1213 → auto_prepare()` currently runs preparation inside the outer AL-command Attempt, whose task/resource intent may differ. Defer such setup to its own explicit dispatch rather than reusing or relabeling that Attempt. No nested admission or new qualification is needed.

Focused companions should prove zero dependency-byte reads during preflight/poll/cancel; exact owning Attempt retention during queued continuation; fresh no-budget admission; unrelated outer AL Attempt rejection; and drift refusal after a genuine source reservation. E31 remains **FAIL9/PASS17/SKIP4**.
