Independent non-authorizing source assessment, updated after SA-01 correction.

**No demonstrated publication-blocking source defect remains within the inspected scope.** This is not formal AL review, approval, production-readiness certification or authorization to bypass pending checks.

Current clean candidate: `feee467a06bb0d4841b4d258fb8d803eeb274771`. Original assessment: `ee846e8d59d537fa5605961e549f4aabaceedec5`, against base `fef2cd1ec5650c7fba5cf9f056302d105f5c64a5`. I independently confirmed that the intervening change affects only `tests/test_al_functional_verification.py`.

**SA-01 — HIGH — PUB-08 — fixed.** The original tests required an unpublished absolute workstation amendment path, causing clean-runner failure. Current lines 16–18 resolve the already tracked amendment within the repository; lines 39–50 verify its exact hash and copy its bytes into the test temporary directory before invoking the public adapter. The other direct invocation also uses this fixture.

The regression at lines 292–306 denies capture of the old absolute path while exercising the real adapter and three-suite runner. It verifies three passing obligations, a temporary amendment path, the unchanged production amendment hash and emitted functional evidence.

Inspected evidence:

- Delta SHA-256: `f3c00102723a2c183f7df514ff034618dbd74f86642f9abcf583cf37efb04c12`.
- Tracked amendment SHA-256: `8cdf41f2fb8582c37cab8fbb53bab7aebf571f76a30a18d623260eda48f797db`, matching the unchanged production constant.
- E11 XML: 38 passes, one failure reproducing the precise old-path `FileNotFoundError`. SHA-256 `1e1f5a344fed23311f0264c3da071528630e1b9ea14b9090df0fb42b83678320`.
- E12 XML: 39 passes, zero failures/errors/skips, 15.736 seconds, including the same denied-path regression. SHA-256 `dad9dcb0142a652ef997d326ab80ca0b27f83ffc93e4697327b0c91d2ce47064`.

This verifies the repair without weakening amendment validation, runner accounting or timing budgets.

The initial source conclusions remain applicable because their application/runtime files are unchanged:

| Criteria | Assessment |
|---|---|
| PUB-02 | Recognized migration histories and schema identities are validated; checkpoint remapping and individual migrations use transactions and locked revalidation. No demonstrated defect found. |
| PUB-03 | Process, policy and storage records remain per handle. Owned-group cleanup does not establish descendant or qualified boundary closure. |
| PUB-04 | Setup retains owning authority; mutable dependency consumption checks current claims/source fencing; subsequent planning dispatches separately. |
| PUB-05 | Typed exchanges validate producer/task/revision, specification and dependencies. Incident facts and operator interpretations remain separate; rejected payloads are not persisted as incident metadata. |
| PUB-06 | Sealed qualification binds executable closure, launcher/interpreter and finite helper operations; unsupported identities refuse. |
| PUB-07 | Reliability routes/assets integrate with the existing dashboard. Historical browser/distribution results retain their original scope. |
| PUB-08 | SA-01 fixed with inspected red/green evidence. Remaining current checks and provider proof are separate obligations. |
| PUB-01, PUB-09, PUB-10 | Rebase/publication provenance, remaining-features handoff comparison, remote refresh and published-commit verification remain supervisor obligations. |

The preliminary queued-setup reservation finding remains **withdrawn**. Environment attempts start before queueing and intentionally retain unqualified legacy liability. It must not justify a refund or corrective edit.

Eight supplied Chrome captures were inspected across desktop 1280×800 and narrow 390×844 layouts. Controls align and no page-level horizontal overflow is visible. Optional UX observation remains: evidence multi-select labels clip trailing classifications/codes, making similar incidents harder to distinguish. No repair is requested for this note.

Verification limits remain explicit:

- E13 lint and E14 format passes, and their VALID status, are supervisor-reported; I did not independently inspect those receipts.
- E8 migrations, E9 handles and E10 conformance preceded the one-file test correction; they are not silently promoted into whole-candidate proof.
- The original four-file functional check was still running at this handoff. No outcome is inferred.
- Historical E38 remains raw **FAIL** with its accepted timing limitation. Timing acceptance does not alter other guards.
- Issue #47, blocked/not-run provider checks, native proof limits and unknown descendant/storage liabilities remain unresolved.
- R5b–R11 remain subsequent mission work.

No checks, services, database connections or writes were performed during either assessment. No assessment file was created; this complete text is supplied for supervisor capture.
