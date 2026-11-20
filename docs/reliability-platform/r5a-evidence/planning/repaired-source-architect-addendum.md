SPEC_STATUS: SPEC_READY  
R5a route remains STANDARD, Engineer then Builder sequentially, no Designer. This is source revalidation against `/private/tmp/al-r4-frozen-review-003`, not R4 review or verification.

The retained R5a plan remains applicable, with these precise integration notes:

- **Setup continuation:** `task_environment.prepare():175` now requires its exact admitted `environment_operation` owner, including execution, principal, task and revision. `auto_prepare():405` queues requests instead of executing setup under the outer AL invocation. `OperationController.tick():1354` waits until there is no ambient attempt; it resumes an explicitly retained owner or calls `dispatch(..., "environment_operation", request)`. R5a must preserve that ownership. Do not transfer an outer attempt, add another setup dispatcher, or interpret a swallowed setup failure as an observed test failure.
- **R4 rejection capture is still required implementation:** `execution_operations.py:467–478` catches `ContractError` through its `ValueError` ancestry and currently records generic `DISPATCH_REJECTED`. `await_assignment():1311` returns sanitized `contract_error` feedback but creates no dedicated rejection receipt. Detect the actual typed exception before stringification; generic errors remain generic. The retained receipt design and authenticated, current-epoch publication remain unchanged.
- **Observation transaction:** `reliability/store.py:109` owns its publication transaction through `_publication():27`; it does **not** already accept a caller transaction. Correct that description in the risk draft. Extend the existing finish transaction to insert the closed rejection receipt and canonical source relation atomically with the observation/audit/incident changes. An internal connection-taking insertion helper is appropriate; a second publication connection inside `finish()` is not. Assignment rejection uses a separate short publication after descriptor validation unwinds, with fresh epoch checks.
- **Other producers/API seams remain unchanged:** `policy.admission.audit():27` uses the existing eight-value positional insert and caller connection; denial still need not have an attempt. `execution_projection.ingest_event():239` remains the event/projection/cursor transaction seam. Accepted report provenance must still be prepared from exact immutable buffers before that transaction, then bound and committed within it. No fabricated AL event, measured budget dimension, remote-CI provenance, or R6 consumer is justified by this repair.

All six risk-evidence hashes were recomputed against frozen003 and are unchanged:

| Source | SHA-256 |
|---|---|
| `harness/execution_projection.py` | `2cd1af0d57511aa4afe1f0e012b8775bd8019d8b8832fba0ff21d526c22be9e7` |
| `harness/workspace_proposals.py` | `3cdb3b96dadb177a91b4fe6bd407e182211158b06f26c504d6d7e60b1d47639a` |
| `harness/reliability/store.py` | `47cf1677eedb1a7404627fd15511977cdcf54d38183f7f2874e32652cb761012` |
| `harness/policy/admission.py` | `9a49b1d2a502e89ab444b59a60e099eba017f4e7358a82fe0aeec505df836cf9` |
| `harness/execution_artifacts.py` | `af8abf800bed03288013918b3ea2104a5204f70eff86e1e5de4fe2b13e02399d` |
| `al/codex/skills/al/scripts/al_review.py` | `8a6a9324fed2650be8e5d1c0e4c8b1ff4a030813859468782e0e6645437fcea0` |

Additional repaired integration bindings:

- `harness/execution_operations.py`: `a765afc925615c674f7838cdc81616aa6248d4a32653711dd844a03924560f1c`
- `harness/task_environment.py`: `5fc28563317d8bf899f19c84f1f4b9c7e7bf7d527b9deb59e33ecc5aec3d4ac3`
- `harness/agent_contracts.py`: `0ce4ff994fd6af5a46225d1b364447f62a81f0a4b567812387a0cfe20525b8d6`

Retain the planned atomic rollback/replay, stale-epoch, safe-field, old-eight-column-insert, immutable-report tamper and readonly legacy-union tests. Add a regression proving queued setup rejection is attributed to its actual environment-operation boundary and cannot inherit the outer AL attempt; explicitly retained continuations must not fabricate a second dispatch observation.

No new material engineering choice remains. Startup stays schema-only, GET stays readonly, bounded late-legacy projection remains governed by its retained addendum, and R5b remains separate. Rebind to final Builder source before implementation admission; these hashes establish inspected source currency only. No tests or services were run.
