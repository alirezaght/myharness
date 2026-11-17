# Implementation, verification and developer decisions

AL helps the developer get a clear specification, useful implementation, relevant
tests and independent code review. Its workflow must remain proportionate to the
change. Internal process failures must not become product approval questions.

## What can pause implementation

Pause only the affected work when a material specification is contradictory or
missing, or a consequential engineering choice actually requires the developer.
Explain the behavior at stake, useful alternatives and a recommendation. Inspect
the code before asking about details it already answers. Continue independently
defined authorized work.

Execution still needs real authority and capability: authenticated invocations,
current task and epoch identity, explicit ownership, settled conflicting processes,
safe paths, secret protection and containment. A physically unavailable operation
gets a technical diagnostic and repair action. Never turn that failure into a
developer specification decision or grant privileges inside a native agent.

## What stays as verification work

Missing tools, incomplete service or check discovery, unsupported adapters,
unavailable providers, failed checks and pending planning proof are operational
issues or verification obligations. Preserve diagnostics and failed evidence,
repair automatically within existing authorization, and continue defined scoped
implementation. Use `integration issue-update --file FILE` for typed operational
issues; use `integration blocker --file FILE` only with `kind: specification` or
`kind: developer_engineering_decision` for an actual developer choice.

Implementation readiness is separate from final proof. Ordinary scoped writers
can start with pending proof or incomplete coverage. Never erase recovery debt
when a writer starts. Required checks, complete relevant coverage and current
evidence remain necessary before verified completion and final approval. Unknown,
unavailable, stale, empty and failed results never count as passes.

Released, current authored output can satisfy an implementation dependency without
claiming that its producer is verified Completed. Use `integration task-authored
--task TASK --file FILE`; the runtime computes the receipt and checks the actual
producer, outputs, revision and dependency lineage. Changed outputs invalidate
dependent readiness. Reusing a terminal agent is never a recovery mechanism:
register a fresh observed invocation and preserve assignment history.

## Keep the workflow fast

- Clarify material behavior and scope; settle routine implementation details from
  source. Do not repeatedly interview an already approved specification.
- Route by remaining design risk. Small established-pattern changes need focused
  implementation, tests and independent review. Add planning or pseudocode only
  for a named problem where it reduces risk; file count and tooling trouble are
  insufficient reasons.
- Reuse agent context and existing artifacts. Generate identities, evidence and
  handoff metadata through supported interfaces; do not ask agents to manually
  reconstruct bookkeeping or request developer waivers for missing tooling.
- Keep internal repair/reassessment history without using its budget or pending
  status as an implementation gate. Escalate an actual unresolved engineering
  choice, rather than the fact that an internal round limit was reached.
- Discover task-relevant obligations alongside implementation. Do not pull an
  unrelated monorepo gap into a product decision or claim it is verified.

## Existing pinned runs

A new installation does not change an existing immutable runtime. Supported
recovery must preserve the original package, accepted amendments, decisions and
failure history, while creating fresh execution authority under repaired code.
Preparation alone grants no product authority. Activation must reject stale
epochs, principals, events and delayed decisions. Never hand-edit an old pin,
pretend a tooling blocker was resolved, or import old evidence as a new pass.

### Inspect a pin and choose the supported route

`alctl runtime-successor inputs` requires a Harness-bound run with its real
integration binding and handoff. A standalone run has no supported in-place
pin migration; it reports `UNSUPPORTED_RUNTIME_SUCCESSOR_MODE`. Never construct
a handoff, authority reference or coordinator descriptor to make it appear
Harness-bound. The trusted operator Harness route is documented in
[distribution.md](distribution.md#existing-runtime-pins).

Use the actual protected launcher and the original run's repository, platform
(`codex` or `claude`) and session. Origin inspection identifies the protected
transaction, pin and state location; it does not select a new runtime or migrate
the run:

```sh
ALCTL=("$HOME/.local/bin/alctl")
"${ALCTL[@]}" runtime-successor origin --repository "$REPOSITORY" \
  --platform "$PLATFORM" --session "$OLD_SESSION"
```

For standalone AL, after existing work and its owned processes/checks are
settled, a genuinely new run can use the preserved accepted package through
the repaired managed launcher:

```sh
"${ALCTL[@]}" run --repository "$REPOSITORY" --platform "$PLATFORM" \
  --session "$NEW_SESSION" -- start --package-file "$ACCEPTED_PACKAGE"
```

Set `NEW_SESSION` to an unused run identity. `ACCEPTED_PACKAGE` is the preserved
accepted task-package JSON with its original request, acceptance provenance,
criteria and constraints. Start does not copy the predecessor amendment ledger.
Preserve accepted amendments and decisions as requirements; use exact retained
user text and source through the fresh standalone run's supported amendment
command before its specification gate, once per applicable accepted amendment:

```sh
"${ALCTL[@]}" run --repository "$REPOSITORY" --platform "$PLATFORM" \
  --session "$NEW_SESSION" --existing-run -- task amend --file "$ACCEPTED_AMENDMENT"
```

Do not reconstruct missing acceptance from a summary or invent approval. If the
exact accepted requirements cannot be recovered, retain that technical gap
before starting implementation.

This creates a new baseline, session, run and proof ledger. The predecessor
state, immutable pin and artifacts remain historical references. Re-run the
specification gate, classification and relevant checks against the current
source. Earlier PASS results, agent identities, writer contributions, planning
admission and approvals do not become fresh credit. A standalone start does
not acquire a Harness execution's task or epoch authority.
