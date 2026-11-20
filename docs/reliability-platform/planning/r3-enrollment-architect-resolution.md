PLAN_STATUS: REVISED

RESOLUTION

The always-on claim-creation recommendation was too broad. On current macOS profiles, it could convert an ordinary uncapped legacy command into an indefinitely unresolved liability. That conflicts with preserving optional-policy behavior.

Use **sticky, resource-scoped coordination enrollment**, while checking existing known liabilities before every relevant admission—even when no policy matches.

### Revised implementation flow

1. **Resolve trusted resources before the early return.**

   `resources.prepare()` currently returns before deriving resources when no policy exists or matches. Move the known-liability check ahead of those returns.

   Resolve repository/resource identity from trusted assignment, parsed handler and effective isolation scope. Control labels such as `control:al` are insufficient substitutes for actual affected resources.

2. **Preserve untouched legacy behavior.**

   If the affected domain has:

   - no coordination enrollment,
   - no conflicting durable liability,
   - and no applicable configured policy,

   preserve the existing legacy operation path and best-effort timeout behavior.

   Mark its coverage as uncoordinated. Do not create a permanent strict-closure claim merely because new tables exist. Do not describe legacy completion as qualified whole-tree closure.

3. **Enroll coordination through actual authorized policy installation.**

   Add a small durable domain record keyed by authoritative repository/resource namespace. Enrollment is monotonic: removing/replacing policy or changing roles does not remove the safety fence.

   Within the same database serialization boundary used for admission:

   - Derive the policy’s actual affected domain.
   - Inspect relevant known producer/process/liability records.
   - Refuse enrollment where an overlapping known predecessor lacks the required closure proof.
   - Record enrollment and policy selection atomically.

   Upgraded legacy launch admission and enrollment must serialize through this boundary. A legacy operation admitted immediately before enrollment is a predecessor whose actual coverage must be inspected; it cannot disappear from consideration because it had no budget.

   Old immutable/uninstrumented producers are **not coordinated participants**. Where a known such producer can affect the domain, require qualified closure or refuse strict enrollment/admission. Do not rewrite its pin or infer absence from missing instrumentation.

   This is coordination among supported participants in one authoritative database—not global isolation from other databases or external actors.

4. **After enrollment, resource safety is independent of budgets.**

   Every overlapping effect and stable dependency consumer uses atomic check-and-reserve, including:

   - No matching role policy.
   - No configured cap.
   - Policy replacement/removal.
   - Replacement principals/writers.
   - Another execution using the same database and namespace.

   Use the existing short epoch-fenced `BEGIN IMMEDIATE` transaction. Pass the connection through helpers; do not nest a second connection’s transaction inside `admitted()`.

   Exempt only the exact current attempt or an explicitly bound nested operation already covered by its claim. No blanket same-principal/task exemption.

5. **Refuse unsupported coordinated process boundaries before effects.**

   For an enrolled domain, arbitrary process work requiring retained resource exclusion must have a qualified closure mechanism.

   Current macOS operation profiles allow fork/exec and do not establish whole-tree termination. Therefore refuse unsupported arbitrary-process admission **before consuming an irreversible start permit or launching**.

   Do not allow the first command and then discover that ordinary continued work is permanently blocked. A `read_only` command classification alone does not qualify process closure.

   Closed trusted handlers may qualify independently where their implementation bounds work and leaves no asynchronous effects. Independent resources and permitted cleanup/diagnosis remain usable.

### Pending and no-launch attempts

A `pending` process row is not proof that no process launched.

Release a reserved claim only when the authenticated current owner atomically disables its sole start path and verifies:

- No effect/start permit was issued.
- No launcher permit or other delegated launch authority remains usable.
- No launched/ambiguous child or external-effect obligation exists.
- The exact attempt/process-instance binding matches.

If a permit was issued and launch acknowledgment is missing, retain uncertainty. A separate qualified not-started receipt may resolve it; PID/group absence cannot.

Do not let an accidentally terminal accounting label hide an open process/resource obligation.

### Release and dependency behavior

Permission revocation, writer finish and principal replacement remain allowed. They do not erase claims or liabilities.

A conflicting new effect or dependency consumer must refuse through its own admission path. Authored-but-unverified outputs remain usable where existing policy permits them **and** no affected liability exists.

Stable dependency consumption in an enrolled domain holds read claims across its actual use lifetime. Diagnostic reads may remain available, but must not be represented as stable dependency evidence.

### Closure and clocks

The existing closure/deadline addendum remains authoritative:

- Parent reaped and initial process-group absent are observations, not whole-tree closure.
- Linux namespace flags require actual qualified lifecycle evidence.
- Remote liability remains separate.
- Settlement requires current authenticated supervisor ownership and exact attempt/process binding; writer permission is not required merely to record qualified closure.
- Retired callbacks remain refused.
- Clock continuity is independently required for strict time enforcement. Matching `CLOCK_ID` does not repair an untrusted origin.
- Cleanup remains available when new time-budgeted work is refused.

### Routing and exact implementation scope

This is a correction to the existing COMPLEX R3 protocol. Designer should update the resource addendum with this enrollment boundary; no new user approval or broad redesign is required.

Engineer scope:

- Known-liability guard before optional-policy returns.
- Sticky scoped coordination enrollment and serialized admission.
- Durable resource/dependency claims for enrolled domains.
- Pre-effect refusal for unsupported closure capabilities.
- Exact no-launch release and supervisor settlement checks.
- Explicit legacy/uninstrumented coverage.
- Existing lifecycle release remains separate from settlement.

Do not introduce a new auth system, global resource service, automatic pin upgrade or historical writer.

### Required verification

1. Untouched uncapped legacy commands retain prior behavior and do not accumulate strict permanent claims.
2. A known unresolved liability blocks overlapping no-policy/unmatched-role/replacement consumers.
3. Policy removal does not remove enrolled coordination.
4. Enrollment racing a supported legacy start cannot miss that predecessor.
5. Known unqualified predecessors prevent strict enrollment; immutable bridges are never labeled coordinated.
6. Enrolled unsupported macOS arbitrary commands refuse before effects.
7. Qualified closed handlers and disjoint independent work proceed.
8. Read-only command classification cannot manufacture process closure.
9. Reserved/no-permit cancellation releases safely; missing launch acknowledgment after permit remains unresolved.
10. Permission release succeeds while liability persists.
11. Dependency read/write races serialize; existing authored-output semantics remain intact when clear.
12. PID/group disappearance, terminal accounting labels and clock discontinuity cannot manufacture settlement.

I sent this bounded scope directly to Engineer and Designer. No edits, tests or services were performed.