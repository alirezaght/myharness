PLAN_STATUS: REVISED

RESOLUTION

**Correct the earlier adapter condition: functional success requires successful owned-process cleanup, not qualified whole-tree producer settlement or storage removal.** My previous wording conflated those contracts.

The source explicitly separates them:

- `verification_runner.py:108–116,144–156` requires cleanup without errors/unresolved owned processes and invalidates functional reconciliation on incomplete cleanup.
- `owned_process.py:230–243` can report `complete=True`, `leader_reaped=True`, `owned_group_settled=True`, while retaining `descendants_settled=None` and `settlement_basis="owned-process-group-only"`.
- `verification_plan.py:230–244` separately applies `producer_settled()` and retains storage with `descendant_settlement_unproven`.
- `verification_contracts.py:750` requires separately qualified namespace teardown for positive producer settlement.
- `finalize_verification_storage().complete` means all allocations were **removed**. It is not the functional runner’s cleanup status. `verify_changed.py` writes its report before that finalization.

Amend the adapter requirements:

1. Require the runner’s `verification.cleanup.complete == true`, empty `errors` and `unresolved`, no interruption/cause, and complete selected functional reconciliation.
2. Validate retained per-launch ownership/producer receipts for actual leader reaping and owned-group settlement. Reject missing or contradictory receipts, active/unreaped owned processes, unknown launch outcomes and actual cleanup failures.
3. Permit the existing, explicitly recorded combination of group settlement with unknown descendant settlement. Preserve it verbatim in the adapter output and retained artifacts.
4. Do **not** require `producer_settled == true`, `completion.settled == true`, storage finalization `complete == true`, or allocation removal to establish functional assertion success.
5. Expected retention for `descendant_settlement_unproven` is not a failure to execute assertions. Missing/corrupt publication records or unverified storage errors are distinct and must not be silently classified as expected retention.
6. Grant no producer closure, retirement, resource refund, whole-tree containment qualification or clearance of policy liabilities. Existing storage and resource guards remain authoritative.

Suggested result fields:

```text
functional_assertions: PASS
raw_runner_status: FAIL
timing_disposition: accepted checkpoint limitation
owned_process_cleanup: complete
descendant_settlement: unknown
storage: retained; descendant settlement unproven
producer_closure_claim: none
```

Keep every other adapter check from the prior guidance: exact invocation/source/selection binding, complete node accounting, zero functional failures/errors/skips/missing nodes, and timing-only explanation of exit `1`.

Add two contrasting adapter tests: group-settled/descendants-unknown with valid retained receipts is eligible for functional success; actual incomplete cleanup or an unresolved owned process is rejected. The user’s timing amendment changes neither outcome’s custody semantics.