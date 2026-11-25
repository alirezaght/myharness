PLAN_STATUS: REVISED

RESOLUTION

Use a **distinct verification/publication run after implementation settles**. The inspected public API cannot change this existing run’s merge base after writer registration.

Source-backed reasons:

- `al_scope.py:646–676`: `retarget_blockers()` rejects any registered writers or attributed contributions, even if subsequently finished. Its diagnostic explicitly directs starting a new run.
- `al_scope.py:188` and `al_task_attribution.py:143`: whole-MR review uses stored `identity.merge_base`; rebasing Git does not refresh that value.
- `al_inherited_lineage.py:45–49`: runtime successor initialization restores the predecessor’s target and merge-base anchors. A successor therefore does **not** solve this problem.
- `al_planning_rebind.py`: planning rebind derives a new planning report from an issuance retained in the same run; it does not retarget repository identity.
- `al_recovery.py`, `al_gate_recovery.py`: recovery checks require matching recorded identity. Check reconciliation addresses interrupted processes, not Git ancestry.
- `al_transitions.py:47–56`: explicitly supports verification-only runs over implementation that already exists, using ordinary phase transitions without inventing a writer.

Proceed as follows:

1. Finish or truthfully settle the current run’s actual writers and check processes through their existing public commands. Preserve run `c82f62fb`, its attribution, E1–E4 failures, recovery records and R4 debt. Do not call it approved or overwrite its state.
2. Freeze the completed rebased candidate. `69eaee9` is presently a rebase anchor, **not necessarily the finished candidate**: this inspection observed additional tracked modifications and untracked implementation/tests.
3. Record a separate publication task package containing the unchanged original request/accepted constraints, the exact candidate and target, and explicit historical obligations referencing the retained old artifacts. Describe the new scope as verification and publication of existing implementation. Do not claim the new run authored those changes.
4. Through the supervisor’s normal protected launcher, initialize a **new session**, not a successor and not a reused run ID:

```sh
"${NEW_STATE[@]}" start \
  --package-file "$PUBLICATION_PACKAGE" \
  --review-mode whole-mr \
  --target fef2cd1ec5650c7fba5cf9f056302d105f5c64a5
```

`NEW_STATE` means the supervisor’s supported protected-launcher invocation for the new session. After `start`, use the newly printed AL run ID wherever an explicit `--session` requires it.

5. Inspect the public start/status output. Require `identity.merge_base` and `target_oid` to equal `fef2cd1ec5650c7fba5cf9f056302d105f5c64a5`. Read-only Git checks:

```sh
git merge-base HEAD fef2cd1ec5650c7fba5cf9f056302d105f5c64a5
git diff --stat fef2cd1ec5650c7fba5cf9f056302d105f5c64a5
git status --short
```

6. Perform fresh current-run specification/routing, discovery, planning admission and evidence registration through the existing public workflow. Obtain actual Architect authorship; do not copy old packet bindings or mark old evidence current. The route must reflect the **remaining verification work**, rather than automatically inherit the original implementation’s complexity.
7. Follow the admitted route. For a route without Designer:

```sh
"${NEW_STATE[@]}" phase IMPLEMENTATION
# Include FINALIZATION if the admitted route requires it.
"${NEW_STATE[@]}" phase VERIFICATION
"${NEW_STATE[@]}" review-start
"${NEW_STATE[@]}" review-packet --stage fresh
```

The public transition guards still require planning admission, gates and evidence. These commands are not bypasses. No writer registration is needed merely to account for already-existing source. Run the separate comparison stage only after saving the fresh review.

Preservation limits:

- E1–E4 remain original failures in the old ledger. New executions receive new IDs; they may demonstrate that the same symptoms are now resolved, but cannot rewrite or supersede another run’s records.
- Preserve authored attribution in the original run and reference it as provenance. New whole-MR mode reviews the complete `fef2cd1..candidate` change, including preexisting implementation.
- Carry R4’s exact unresolved obligation into the new task’s acceptance/verification requirements. A new run does not clear R4 or renew permission to repeat exhausted implementation attempts.
- If R4 represents exhausted recovery authority, any further affected implementation must follow its existing recovery decision path. The distinct run is justified by the unusable review base, **not** by renewed correction budgets.
- A fresh review packet must remain free of prior review findings. Historical evidence obligations belong in the task/verification inputs; prior finding resolutions belong in the comparison stage under its public contract.
- If remote main advances, the fixed target is no longer sufficient for publication. Reconcile before review; use supported `retarget` only while its blockers remain absent, otherwise repeat this honest verification-run separation.

This is an operational limitation worth reporting under the authorized repository issue workflow: search first, then attach the concrete reproduction—start before rebase, register writer, rebase, observe unchanged merge base, and documented retarget refusal. No state patch, pin replacement, counter reset or waiver is warranted.