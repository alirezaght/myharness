R4’s approved behavior and STANDARD/noDesigner route remain unchanged. The following source-delta addendum is required before implementation.

### Preserve R3’s dependency-validation ordering

Current `OperationController.admitted()` now accepts `verify_outputs=True`, derives affected resource claims, checks existing liabilities and passes that flag recursively into `require_dependency_outputs()`.

R4 must distinguish:

- **Pre-admission metadata validation:** closed schema, declared family, context and required-reference shape. No dependency file/hash reads.
- **Post-reservation validation:** resolve and verify required artifact/file bytes while R3’s dependency read claims are held.
- **Consumption:** preserve the claims for the actual consumer lifetime, not merely the validation call.

Do not resolve typed inputs during a `verify_outputs=False` preflight. Propagate this mode through recursive typed-reference validation. Otherwise R4 would reopen the check/use race R3 closes.

A valid typed envelope cannot replace existing authored-output, producer lifecycle, candidate, requirement, graph revision or completion checks. Current authored-but-unverified dependency readiness remains permitted where existing policy allows it.

### Use the existing dispatch attempt

Current dispatch executes:

`prepare(...) → policy.context.scope(attempt) → _dispatch(...) → owner_finished(...)`

Typed output validation and consumption belong inside that existing attempt context. They must not create a second `Attempt`, open a nested policy transaction or independently settle its claims.

`admitted()` currently uses `CURRENT.get()` to recognize the exact active attempt. Preserve that exact-attempt exclusion; do not introduce task/principal-wide exemptions.

Pure validators should accept already resolved context. Any helper requiring database checks receives the current connection rather than calling `connect()` or `admitted()` recursively from inside an admission transaction.

### Assignment handoff

`assignment_handoff._selected()` already calls `operation.admitted()` before reading the generated handoff.

Extend its existing binding with the typed receipt identity. Preserve writer/phase/graph/evidence-plan validation and pagination invalidation.

Before implementation, confirm that the finalized R3 resource derivation for `harness:assignment-handoff` reserves all dependency claims required by its subsequent reads. If it does not, add that exact resource mapping; do not treat a generic `read_file` label as sufficient.

The descriptor/page request remains a presentation read, not new writer admission or completion.

### Output submission and artifact locking

Current `ArtifactStore.submit_output()` accepts free-form content, validates registered role and graph revision, then publishes under its artifact-index lock. MCP currently declares only `name`, `content` and `expected_graph_revision`.

R4 should add explicit optional contract metadata to the public submission surface:

- Absent metadata preserves legacy supporting-output behavior.
- Declared typed output validates before accepted publication.
- An assignment requiring a typed result cannot be satisfied by a legacy output.
- Generic JSON parsing is not contract validation.

Resolve authority/context and required external references **before acquiring the artifact-index lock**. Under that lock, perform only local index/replacement/publication checks. Do not acquire policy/epoch/database locks while holding the artifact lock.

For report dependency consumption, extend `require_authored_report()` without weakening its existing completed-observed-producer provenance checks.

### Both AL platforms

Retain the baseline R4 changes to:

- `al_writer_handoff.py`: typed receipt accompanies the existing generated body/evidence block.
- `al_artifact_cli.py`: explicit typed publication; generic supporting artifacts remain valid.
- `al_execution_tasks.py`: typed exchange references do not themselves authorize task/writer admission.
- Specialized planning/review/evidence/decision adapters: existing validators remain authoritative.

Do not route these operations through a new helper subprocess or arbitrary RPC. R3’s private origin and sealed retry qualification must remain intact. Adding modules requires updating the actual trusted-source/package closure and parity inventory.

### Ownership additions

Engineer additionally owns the **minimal finalized R3 mapping changes** needed for typed assignment/input resources and dependency claims. No redesign of enrollment, budgets, settlement or retry qualification belongs to R4.

Builder remains deferred until these interfaces stabilize. Both platform copies, MCP schemas/help, private snapshot membership and artifact/dependency tests belong in the verification scope.

### Required evidence additions

- A declared typed dependency is read only after its resource reservation.
- Concurrent overlapping mutation cannot begin between typed validation and use.
- `verify_outputs=False` performs no dependency byte/hash reads.
- Invalid typed submission changes no accepted output/index state; any existing claim lifecycle follows R3’s normal failure path.
- No nested SQLite transaction or artifact-lock/policy-lock inversion.
- No-policy/unmatched-role consumers still respect known liabilities and sticky enrollment.
- Legacy outputs remain readable; typed-required assignments reject them as insufficient.
- Authored-unverified dependencies retain their existing allowed behavior.
- Assignment pagination becomes stale when the typed receipt or governing context changes.
- Both-platform package/private-origin checks include the new modules without changing old pins.

R3’s `prepare`, resource derivation, claim reservation and sealed-helper closure are still moving. Reinspect their final signatures and lock order before R4 admission. This addendum reports source integration requirements, not verification or approval.