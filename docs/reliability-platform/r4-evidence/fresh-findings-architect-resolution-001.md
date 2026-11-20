PLAN_STATUS: UNCHANGED

RESOLUTION

The four findings require bounded repairs within the accepted R4 specification. No new product decision, Designer stage, broader lifecycle gate, or approval is needed. Core changes remain AL-Engineer responsibility. The public rework command’s outcome is not yet supplied; this resolution makes no claim about its completion or phase transition.

Before production changes, record failing public-path regression tests:

- **001:** Produce an interrupted terminal check through the actual evidence producer, settle its processes, and invoke `check-reconcile`. Assert successful reconciliation, retained original observation, and readable `INFRA` evidence status. Exercise both vendored platforms and a real Harness-bound run.
- **002:** Create an issue, register an independent task through the public integration command, then update and resolve the original issue using legitimate fresh evidence. Assert preserved identity/scope and successful revision progression.
- **003:** Generate a real writer handoff, then submit an otherwise valid typed result whose criteria alone are invented. Assert rejection and unchanged accepted output state on both platforms.
- **004:** Verify the documented complete submission example against the registered CLI.

Use focused tests added to each platform’s existing `tests/test_agent_contracts.py`, selected by `-k r4_repair`, running separately from each platform skill directory:

`/private/tmp/al-reliability-verified-venv/bin/python -m pytest -q tests/test_agent_contracts.py -k r4_repair`

Record the Harness-bound regression separately through its public operation fixture. Repeat each exact recorded command after repair; do not substitute schema-only tests for these causal paths.

**001 — reconciliation transition**

The demonstrated cause is the full-entry digest retained at `al_store.py:150`, followed by legitimate mutation at `al_recovery.py:89` and consumption during `al_events.py:86`.

Authenticate the existing observation before mutation. Retain its original entry and companion immutably, then create a versioned reconciliation observation/companion bound to the original and the actual reconciliation artifact. Prepare the authorized transition before outbox verdict calculation. Keep existing process-settlement guards and `INFRA` semantics.

Do not make `save()` regenerate contracts whenever fingerprints differ. Add refusal companions for altered original observations, invalid reconciliation artifacts, and attempts to reconcile a living check. If implementation uses an existing history structure, confirm its validation and consumer semantics first; a history label alone is insufficient.

**002 — historical issue context**

At `al_operational_issues.py:27`, authenticate the prior issue against its retained observation context and exact payload/reference provenance. Separately enforce the current caller’s authority, current task existence, immutable issue scope/binding, next issue revision, and fresh resolution evidence.

An unrelated graph revision must not rewrite the historical context or prevent the authorized next observation. Preserve refusal for foreign bindings, changed scope, stale issue revisions, and inadequate resolution evidence. Merely disabling all context checks with `current=False` is insufficient.

**003 — authoritative criteria**

In both `al_artifact_cli.py` copies, derive expected criteria using the same task/package selection semantics as `harness/agent_exchange.py:126–129`. Compare before snapshots, output-index changes, or idempotent-return handling. Reject mismatches with the fixed `/acceptance_criteria` error. Include valid submission, invented criteria, and empty-list/applicability evasion cases.

**004 — documentation**

Replace the nonexistent `artifact submit` instruction with the actual command:

`al artifact output --name result.md --file result.md --contract-file contract.json --writer WRITER_ID`

Explain that `WRITER_ID` identifies the current active writer and `contract.json` binds the actual assignment and result content.

The immutable-observation and independent-authority invariants remain unchanged. The only implementation detail requiring source confirmation is the appropriate existing storage/validation mechanism for reconciliation history; it is an engineering task, not an unresolved user decision.
