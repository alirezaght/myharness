PLAN_STATUS: REVISED

RESOLUTION

The transient `typed_exchange` claim is insufficient for mutable source dependencies. My earlier guidance treated immutable artifact validation and stable source consumption too uniformly.

The observed source establishes:

- `policy/admission.py:215–275` records legacy admissions separately from enforced resource claims.
- `policy/claims.py:29–50` checks resource claims, not `policy_legacy_admissions`.
- `policy/domains.py:20` checks legacy predecessors when enrolling a domain. Enrollment is sticky; it is not a temporary read lock.
- `policy/resources.py:214–215` makes typed exchanges nonlegacy without establishing safety against those predecessors.
- `policy/resource_bindings.py` adds artifact namespace claims **and recursively adds mutable dependency scopes**.
- `agent_exchange.resolver()` currently resolves retained AL artifacts and execution artifacts, not arbitrary source paths.
- Existing `knowledge.sources.read_artifact():289–322` returns the exact bounded, hash-validated bytes. Those bytes can be consumed as an immutable snapshot without claiming the producer’s process tree or repository has settled.

Use two distinct paths.

IMPLEMENTATION_PLAN

1. **Keep typed assignment presentation and immutable output publication usable.**

   In `assignment_handoff`, `agent_exchange`, `execution_artifacts` and `policy/resources`, distinguish presentation/publication from admission to consume mutable source.

   Assignment presentation retains current principal, writer, phase, revision, receipt and dependency-*metadata* checks, using `verify_outputs=False`. It validates declared immutable references and returns their exact authenticated bytes. It must not implicitly verify mutable dependency files or declare them stable.

   Typed output publication similarly validates its own immutable inputs and authenticated context before the artifact-index lock. Publication remains authored output, not dependency readiness or completion.

2. **Validate and consume the same immutable bytes.**

   `agent_exchange.resolver()` must use server-selected issuing roots, actual indexed references and bounded ordinary-file reads. Validate the exact returned buffer and consume that buffer; do not verify a path, reopen it later and treat the second read as already verified.

   An immutable record containing source hashes is evidence about an observation. It does not establish that the corresponding current source files are unchanged or safe to consume.

   Do not exclude a genuine existing enforced claim merely because the requested object is called an artifact. Any distinction must follow authenticated namespace/provenance and the exact-byte consumption semantics—not caller-supplied kinds or resource labels.

3. **Remove the blanket typed-to-source coordination implication.**

   `resource_bindings.derive()` must not recursively attach mutable dependency scopes merely because an operation presents an assignment or stores an authored report. Preserve those scopes for operations that actually validate/use mutable dependency outputs.

   Do not invent synthetic artifact namespaces as proof that an unknown legacy process cannot affect them. Existing artifact integrity and authority checks justify snapshot consumption; they do not qualify process closure.

4. **Keep mutable source consumption strict.**

   At actual `require_dependency_outputs(..., verify_outputs=True)` and subsequent consuming effects:

   - Derive source scope from authenticated dependency receipts.
   - Establish applicable predecessor safety before source-byte reads.
   - Reserve dependency claims atomically and retain them through use.
   - Revalidate the governing receipt and authority at start.
   - Refuse required stable consumption when a relevant legacy predecessor has unqualified outstanding liability.
   - Never infer settlement from completed handoff generation, parent/group disappearance, saved AL state or an authored result.

   Do not silently invoke sticky enrollment during a presentation read, clear legacy rows, add a general AL exception or relabel the consumer as “legacy” to evade required stability.

5. **Route the remaining mutable-source integration to a narrow Designer stage.**

   That remaining work is **COMPLEX**: the current transient claim protocol does not serialize strict consumption against existing legacy launch presence, while broad enrollment makes ordinary handoff generation poison subsequent availability. Designer must specify the exact existing-enrollment/legacy-predecessor admission protocol and, where necessary for a working generated-handoff journey, the smallest separately source-qualified control producer. Qualification must be method-specific after callgraph inspection; it cannot be inferred from the existing retry/finish qualifications.

   Scope is `policy/resources.py`, `resource_bindings.py`, `claims.py`/`domains.py`/`admission.py` only as necessary, plus actual dependency consumers and any explicitly identified qualified producer. No new permissions, global coordinator or general AL qualification.

   Independent immutable-family adapters/tests can continue under the existing STANDARD guidance. This is not a new user decision or a reason to stop the remaining mission.

INVARIANTS

- Typed schema validity grants neither authority nor repository stability.
- Immutable snapshot integrity and mutable source exclusion are separate guarantees.
- Existing enforced liabilities remain effective; legacy presence is never silently promoted to qualified closure.
- Assignment presentation may succeed while execution requiring stable mutable dependencies is refused.
- Historical untyped behavior remains unchanged. Authored-unverified dependencies remain usable wherever existing authority and the actual required resource-safety conditions permit.
- Reuse the exact current Attempt and connection; no nested admission transaction, artifact-lock/policy-lock inversion or independent claim settlement.

ACCEPTANCE_CRITERIA

- A real generated typed assignment remains readable when its completed legacy handoff command has an unqualified legacy admission, **without** reading mutable dependency files, deleting that admission or claiming source stability.
- Immutable reference tampering, replacement during reading, wrong producer/root and stale receipt fail; successful validation consumes the exact checked buffer.
- Typed output publication remains available for valid immutable inputs and changes no accepted output on rejection.
- A relevant active or uncertain legacy producer prevents required stable mutable-source consumption; a future-facing transient claim alone cannot make the test pass.
- Enrollment/legacy-launch/strict-consumer races have one serialized safe outcome. Parent/group disappearance cannot resolve the losing predecessor.
- Existing enforced claims still block conflicting work across roles, policy removal and replacement principals; independent qualified work remains possible.
- Dependency claims precede source hashes and survive the consuming effect. `verify_outputs=False` performs no dependency-byte reads.
- Coverage explicitly distinguishes `immutable_snapshot_validated`, `mutable_source_not_checked`, `coordinated_source_consumption`, and `legacy_uncoordinated` semantics; labels convey no new authority.
- Both-platform public generation → assignment → output → dependency tests cover availability and refusal separately. No mock producer closure substitutes for qualification.

This resolution is based on the supplied 11-file observation and immutable R3 baseline. No edits, tests, services or approval actions were performed.
