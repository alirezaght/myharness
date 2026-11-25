# Non-authorizing source/plan comparison

**No additional demonstrated code blocker was found in this comparison. Formal AL approval remains pending.** This is an independent source/plan comparison, not a formal `review-compare` report, waiver, or publication authorization.

Inspected candidate: `feee467a06bb0d4841b4d258fb8d803eeb274771`. The working tree was clean. Its observed merge base with the supplied main commit is `fef2cd1ec5650c7fba5cf9f056302d105f5c64a5`. No checks, services, database connections, source edits or Git mutations were performed.

## Comparison basis

I read the updated independent assessment, accepted publication package, current Architect and Designer guidance, final adapter plan, publication archive and remaining-features handoff. I inspected relevant current migration, process/setup, runtime, materialization, adapter, distribution-test and dashboard source. This was a bounded comparison of the whole candidate’s requirements and integration boundaries, not a line-by-line re-review of all 609 changed files.

Confirmed artifact hashes:

- Recovered Designer: `2e81c89131f7a482d9642e0f5b9acdeee6ce56bce231757aec3292d27478e080`.
- Delta Designer: `9ba2a2a7346434c296fdda3b9df106c6e15adf7f4837343bb28f30e319e98c5b`.
- Current functional result: `73faa5b5ae91c44e25b232c814e77ff9ac6b19a1c6aef0a87be1ab069353b054`.
- Current raw runner report: `c5091b5d50f67d41b6684520e9b061b037af8b192ee345be57e2028d6e0a60ef`.

## Findings and prior resolution

**SA-01 — fixed in source; supporting red/green evidence remains as recorded.** `tests/test_al_functional_verification.py:16–18` now resolves the tracked amendment relative to the checkout. Lines 39–50 verify its exact hash and copy its bytes into owned test storage. This removes the unpublished workstation-path dependency without relaxing the production amendment identity. The independent fresh assessment inspected E11/E12; this comparison inspected the correction itself and does not claim to have rerun those checks.

The withdrawn queued-setup reservation finding remains withdrawn. I found no basis to revive it or introduce a speculative resource refund.

**Plan correction:** My final plan’s suggested `verify_changed.py --path scripts/... --path tests/...` invocation was unsuitable for these root-level adapter tests because those paths do not select the intended AL suite. The actual root test invocation and its real miniature shared-runner children provide the relevant adapter evidence. The incorrect proposed command receives no verification credit.

## PUB-01–PUB-10

| Criterion | Comparison |
|---|---|
| PUB-01 | The observed candidate merge base matches the intended main commit. The archive consistently identifies the preserved checkpoint and rebase. Preservation of both implementations is supported by the inspected integration boundaries and fresh assessment, not inferred merely from ancestry. |
| PUB-02 | The implementation follows the corrected migration protocol. `harness/db.py:139–166` validates in a read transaction, avoids a write lock for an already-current canonical database, and revalidates under write locks before mutations. `migration_compatibility.py` checks exact histories, packaged SQL hashes, ledger structure and attached managed objects. The upstream 0019/0020 files have no diff against the supplied base. |
| PUB-03 | `execution_check_channel.py:212–265` attaches temporary directories and policy hooks to individual retained records. Cleanup and durable liability remain distinct. The adapter separately reports owned cleanup and unknown descendant settlement; it does not manufacture closure. |
| PUB-04 | `task_environment.py:179–202` requires the exact owning `environment_operation` Attempt and matching execution, principal, task and revision. Subsequent prepared planning remains a separately dispatched effect in `execution_operations.py:1690` onward. No direct setup fallback or borrowed authority was identified. |
| PUB-05 | No conflicting deviation from the integrated typed/incident and upstream planning/recovery protocol was identified. The fresh assessment covers the relevant validation boundaries; current E15 also includes actual planning-rebind, parity, consistency, documentation and resource selections. This is not blanket proof of every recovery path. |
| PUB-06 | The source implements the Designer’s bounded optimization exclusion. `execution_operations.py:433–505` suppresses cache grants for enrolled policy execution. `runtime_run.py:73,124,131–133` activates control before the relevant service/cache paths and disables those effects for control execution. `runtime_pin_boot.py:54` uses `write=False`. Exact-method qualification remains distinct from generic process completion. |
| PUB-07 | The distribution probe at `test_planning_session_distribution.py:210` onward exercises installed resources outside the checkout, including schema manifest and canonical migrations. Dashboard fact/source/history rendering remains available at `reliability.js:578–696`. Historical package/browser evidence retains its actual scope and currency limits. |
| PUB-08 | Current functional evidence is honest and separately named. The actual report records 111 functional passes while preserving raw runner FAIL/exit 1 and 23 timing violations. Formal coverage, provider and native limitations remain unresolved. |
| PUB-09 | The handoff retains subsequent R5b–R11 work, provisional migration 0026, merged-consumer revalidation and the explicit rejection of a blanket Knowledge gate. The R4 erratum correctly preserves two returns used, zero automatic grants and unaccepted RR1. Final publication facts still require a current append/update after actual operations. |
| PUB-10 | Still an operational obligation: fresh remote fetch, reconciliation if main advanced, ordinary nonforce push, and remote commit verification. No publication outcome is inferred here. |

## Migration rollout and compatibility

The migration changes match the accepted finite-history bridge rather than a general repair mechanism:

- Published main migrations remain unchanged.
- Checkpoint ledger relocation changes recognized version/name identities while preserving `applied_at`.
- Ledger structure and arbitrarily named objects attached to managed tables are checked before mutation.
- Missing migrations execute with their ledger insertion inside transaction protection.
- Unknown schema/history refuses rather than backfilling, dropping objects or marking missing work applied.
- The fully-current path preserves concurrent-reader behavior.

This does not make concurrent old checkpoint binaries safe. Close old writers before operational upgrade, retain historical pins, and do not edit existing ledgers manually. A cloud deployment, Pub/Sub transition or production database upgrade is not established by these disposable tests.

## Adapter and custody comparison

`scripts/verify_al_functional.py:35` onward uses descriptor-based bounded capture with `O_NOFOLLOW | O_NONBLOCK`, regular-file/single-link checks and identity revalidation. Its validation path checks the supplied selection, source binding, collection inventory, assignments, child receipts, launch ownership, reconciliation and recomputed timing violations.

At approximately lines 479–530, functional acceptance requires complete selected success and a consistent raw exit/verdict. The output explicitly retains:

- raw runner FAIL and exit 1;
- unchanged timing violations;
- owned-process cleanup complete;
- descendant settlement unknown;
- storage retained because descendant settlement is unproven;
- no producer-closure claim.

I inspected the actual E15 artifacts: `source_stable=true`, `selected_success=true`, and runner cleanup `complete=true` with empty errors, unresolved and secondary-signal arrays. The result is consistent with the accepted timing amendment. It is not timing compliance, whole-tree settlement or storage-retirement proof.

The earlier optional fixture-copy change also follows the bounded guidance: `source_materialization.py:303–317` performs the initial native observations before selecting the ordinary copy path. Production cloning remains the default. No further timing optimization is requested.

## UI, packaging and verification limits

E43 installed-distribution PASS2 and E44 Chrome PASS11 remain earlier evidence for unchanged application/package/browser source. I inspected their source contracts and archive descriptions; I did not independently rerun them or open screenshots in this comparison. The fresh assessor independently inspected eight captures. The optional clipped evidence-picker labels remain a disclosed usability limitation, not a new repair request; full fact, provenance and history rendering exists.

E8 migration PASS85, E9 handles PASS13 and E10 conformance PASS38 preceded the one-file adapter-test correction. Preserve that scope distinction. E11/E12 red/green and E13/E14 lint/format retain their actual records. At this assessment’s handoff, the affected AL fast and focused setup checks were reported running; no result is assumed.

Issue 47 and the unresolved `verification-root` binding remain separate from functional correctness. E1 remains actual INFRA with raw exit 126 and provider provenance unavailability/mismatch; no authentication cause is inferred. Provider job attestation is not authenticated guarded-pytest proof. The adapter supplies neither the missing CI binding nor formal AL approval.

## Remaining publication operations

1. Capture the actual outcomes of already-running relevant checks; investigate concrete failures without timing-only optimization.
2. Retain this comparison and the fresh assessment as non-authorizing assessments, with formal AL approval explicitly pending.
3. Record a current publication-status addendum. Preserve the archived core checkpoint and old reports; distinguish their historical “pending” statements from new results.
4. Fetch origin immediately before publication. If it advanced, rebase and reassess affected source and evidence.
5. Perform the already-authorized ordinary nonforce push and verify the remote commit.
6. Update the handoff from those actual Git results. Continue R5b–R11 as subsequent approved work.

No new product decision or user approval question arises from this comparison.
