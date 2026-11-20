SPEC_STATUS: SPEC_READY  
TASK_CLASSIFICATION: STANDARD  
IMPLEMENTER: both, Engineer then Builder sequentially  
DESIGNER_REQUIRED: NO

The retained R5a plan remains applicable. This is preparatory inspection of current production source, not R4 review, test evidence or R5a admission. Repeated hashes of the principal producer/transaction seams remained stable during inspection.

**Necessary implementation guidance**

1. **Ledger reconciliation is not the missing terminal receipt producer.**  
   `al_store.save():130–155` currently calls `al_events.prepare_commit()` before producing a missing terminal `verification-result` companion. `al_events.prepare_commit():75–91` emits mutable ledger projections containing status, counts and validity; it does not attach an immutable accepted terminal-entry receipt.

   `al_recovery.cmd_reconcile():83–116` now preserves the original entry/companion in `check-observation-v1`, snapshots the reconciliation artifact and publishes the authorized `INFRA` transition. `al_agent_exchange.consume_verification():161–190` authenticates that history. Preserve it; do not substitute reconciliation observations for receipts from ordinary completed checks.

   R5a still needs a closed immutable terminal-result producer in both distributions. Prepare its receipt/reference before the corresponding outbox fact is assembled, within the existing guarded save flow. Preserve existing companion production and reconciliation ordering. A later validity change must not rewrite the original receipt or create another incident merely because a new event sequence was assigned; retain stable evidence/result identity and explicit subsequent observations.

2. **Review acceptance remains the real capture boundary.**  
   `al_review.cmd_report():115–137` validates the report and attempt, snapshots the exact `raw` bytes, retains the accepted reference and saves. Add the closed finding-reference publication there, using that accepted snapshot and stable finding IDs. There is still no dedicated accepted-review-finding source event. Do not ingest an arbitrary report file or infer acceptance from its JSON status.

3. **Typed rejection capture remains absent.**  
   `execution_operations.py:467–478` still catches actual `ContractError` through `ValueError`, records generic `DISPATCH_REJECTED`, then stringifies it. Capture the actual typed exception before that conversion. `await_assignment():1326–1337` returns safe typed feedback without a durable rejection receipt. Add that separate authenticated producer after descriptor validation unwinds.

   Generic exceptions, subprocess failures and strings resembling contract errors are not typed rejection evidence. Preserve exact setup ownership in `task_environment.prepare()` and `OperationController.tick()`; do not attribute a continuation to an ambient outer attempt or manufacture another dispatch observation.

4. **Transaction ownership is unchanged.**
   - `policy.admission.audit():27–55` inserts the actual eight-column policy decision using its caller’s connection. Denials have `attempt_id=NULL`. Add policy source/catalog insertion on that connection; preserve positional old inserts. Missing pool/dimension/measurement stays unknown unless captured at the real rejecting check in a separate typed sidecar.
   - `reliability.store.finish():109–160` **owns** `_publication()` and its current-epoch transaction. Add observation-related receipt/catalog writes through internal connection-taking helpers inside that transaction, not a nested publication connection.
   - `execution_projection.ingest_event():239–317` remains the atomic event/projection/timeline/cursor boundary. `execution_worker.py:320–347` supplies the bounded, bound AL outbox. Prepare accepted evidence from server-selected issuing roots/indexes before entering ingestion’s transaction; consume those exact validated buffers inside it. Preserve binding, sequence, replay and current-epoch checks.

5. **The newer R4 delivery repair is a useful exact-buffer pattern, not a general import capability.**  
   `execution_authority.py:93–119` derives the decision digest from trusted persisted delivery bytes after inbox/Registry checks. `al_execution_decisions.apply_delivery():28–42` reads one bounded buffer, compares its digest before parsing and uses the authenticated object. R5a must preserve these semantics and must not repurpose the decision-only receipt as authority for reports, historical incidents or arbitrary artifacts.

   The retained implementation-plan observation and escalation history likewise authenticate their own specific lifecycle transitions. They do not qualify native identity, arbitrary source stability or a future incident producer.

**Compatibility and verification obligations**

Startup remains schema-only. Keep R1 records, IDs and APIs intact; mixed reads use the retained legacy/catalog union without mutation. Explicit projection selects at most 100 currently unindexed rows, with request replay and checkpoint revision; late/backdated old-pin rows remain discoverable. No timestamp watermark or permanent “fully indexed” claim.

Registered check purpose alone does not prove remote CI/staging provenance. A failed check establishes verification failure, while review findings and operator reports retain their attributed status. Reconciliation is not successful execution or proven external-effect settlement. R6 candidate consumption under R5a-10, the R7 result adapter and R5b standalone collection remain real downstream obligations—not implemented consumers or prerequisites for defined R5a work.

Retain focused tests for terminal receipt ordering, validity-only updates, interrupted/reconciled history, accepted-review replay, safe typed rejection capture, policy null-attempt compatibility, exact-buffer replacement, transaction rollback, stale publication, historical operator revisions and late legacy projection. No new material engineering decision is unresolved.

**Current source bindings**

All paths below are relative to the isolated worktree. For `{codex,claude}`, the inspected pair has the same hash unless separately listed.

| Production source | SHA-256 |
|---|---|
| `harness/execution_projection.py` | `2cd1af0d57511aa4afe1f0e012b8775bd8019d8b8832fba0ff21d526c22be9e7` |
| `harness/workspace_proposals.py` | `3cdb3b96dadb177a91b4fe6bd407e182211158b06f26c504d6d7e60b1d47639a` |
| `harness/reliability/store.py` | `47cf1677eedb1a7404627fd15511977cdcf54d38183f7f2874e32652cb761012` |
| `harness/policy/admission.py` | `9a49b1d2a502e89ab444b59a60e099eba017f4e7358a82fe0aeec505df836cf9` |
| `harness/execution_artifacts.py` | `af8abf800bed03288013918b3ea2104a5204f70eff86e1e5de4fe2b13e02399d` |
| `harness/execution_operations.py` | `a765afc925615c674f7838cdc81616aa6248d4a32653711dd844a03924560f1c` |
| `harness/agent_contracts.py` | `0ce4ff994fd6af5a46225d1b364447f62a81f0a4b567812387a0cfe20525b8d6` |
| `harness/execution_worker.py` | `785f15ab0fd22d7a820a665d08c8ca8389e7d22cfb4c45fa52de614668e34c65` |
| `harness/task_environment.py` | `5fc28563317d8bf899f19c84f1f4b9c7e7bf7d527b9deb59e33ecc5aec3d4ac3` |
| `harness/execution_authority.py` | `010a481b6019d09f83ee929c6b93c244af4c258dd8587cfd278b54eb739b67ca` |
| `al/{codex,claude}/skills/al/scripts/al_review.py` | `8a6a9324fed2650be8e5d1c0e4c8b1ff4a030813859468782e0e6645437fcea0` |
| `al/{codex,claude}/skills/al/scripts/al_events.py` | `98b53191aa4df6b0fb0ebd9332feb0e77a6e0d0e7477b238bb11ba0e15d68d34` |
| `al/codex/skills/al/scripts/al_store.py` | `4c8f82a0f545b7e943290de1a536ed41b3bc3fa0c453eef55b5cd7aa99416049` |
| `al/claude/skills/al/scripts/al_store.py` | `3f69dca86b93b0d6938a51bd29cf545061609a9f13555762a47c4806d802219c` |
| `al/{codex,claude}/skills/al/scripts/al_recovery.py` | `30a24ba79b38016e74183bbc15db0c1e147b197e7881554dfeec43239d6f35ef` |
| `al/{codex,claude}/skills/al/scripts/al_agent_exchange.py` | `999e1c9ade510a2893b1a5fba6c3b55bcd83bcf3e183c1d796cc930b12957848` |
| `al/{codex,claude}/skills/al/scripts/al_execution_decisions.py` | `db1606cfced09e84a235859c3aeb620fe02412bd25b05576391f21cffa56c9f1` |
| `al/{codex,claude}/skills/al/scripts/al_planning.py` | `cff470d3821e6722ded26c5ef5a717ee960f3630812e4bedc5065e6c95500a76` |

The original six risk-evidence bindings remain unchanged. The additional lifecycle bindings above must accompany the later fresh packet. No tests, services, database operations, native/authentication activity or edits were performed.
