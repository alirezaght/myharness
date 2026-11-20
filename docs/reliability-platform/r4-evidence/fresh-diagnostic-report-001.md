Independent diagnostic review of frozen candidate `/private/tmp/al-r4-frozen-review-005`: **three functional findings and one documentation finding**. These are source-traced counterexamples, not newly executed reproductions. This report is not formal AL approval.

**Scope and artifact integrity**

I read the complete accepted task package and review instructions; inspected the selected R4 changes, their producer/consumer paths, adjacent recovery and authority code, tests, approval context, CI definitions, factual ledger, evidence status, and XML results. Mirrored AL changes were compared across Codex and Claude; differing platform startup/store behavior was inspected separately.

All 1,653 manifest source hashes, all 79 delta after-hashes, and all 55 XML hashes matched. Hash verification establishes snapshot integrity, not correctness or exhaustive semantic review of all 1,653 files.

| Artifact | Verified SHA-256 |
|---|---|
| Accepted task package | `7bb19982f341a59fb44259b290783795e43e72c03d78e88718a2dc0161b0520c` |
| Review instructions | `6dc642a0b47c51df8b4f84af962c3cb71c8e73ebe66be4430ac355b042fa8352` |
| Source manifest | `bd0b9c0ee96665078fee5e58626984abe7908fee8a6f6cab9259349a70d52a04` |
| Selected diff | `ff9dfe76b7804ca398e445382b924180c8fd3ca5c298c10672eecfbac21b09a0` |
| Delta attribution | `ffa40f8cf87c1a9cfb3affa0d8c7e479bb2af26162d83cd9d7d65fd1de52242d` |
| Factual ledger | `a4350060353ed4eea40ee43d469f86ed9b2341e8bbb6303295d096cdfb5adbc8` |
| Evidence status | `d8ba1bf35155e3786c6fb53b7c2ed77d3ed45e83740a5291a078a0a2a0cb314c` |
| XML manifest | `03cab4bc042573d1fd3c785da629e01564cb8418408621ab99f93c327919ab83` |

No actual `AGENTS.md` or `CLAUDE.md` was included in the source manifest; only fixture files exist. I followed the supplied repository instructions. I did not inspect live source, excluded narratives, or prior reviews; run tests or services; or modify files.

All source locations below are relative to the frozen candidate. Findings in mirrored AL files apply to both platforms.

**R4-FRESH-001 — HIGH: interrupted-check reconciliation invalidates the verification companion**

Criteria: R4-01; original mission requirements for safe resumability and preservation of existing behavior.

Locations:

- `al/codex/skills/al/scripts/al_store.py:150`
- `al/codex/skills/al/scripts/al_ledger.py:421`
- `al/codex/skills/al/scripts/al_agent_exchange.py:98`
- `al/codex/skills/al/scripts/al_evidence.py:209`
- `al/codex/skills/al/scripts/al_recovery.py:83`
- `al/codex/skills/al/scripts/al_events.py:77`

An interrupted check is persisted as terminal `INFRA`, with `interrupted=True` and `ended_at`. `save()` creates a verification companion hashing that complete entry. The supported `check-reconcile` command subsequently changes its note, end time, and reconciliation field. `save()` only generates companions when their key is absent, so the companion retains the previous digest.

Concrete counterexample: interrupt a recorded check, allow its processes to settle, then reconcile it through `check-reconcile`. In a Harness-bound run, `prepare_commit()` calls `entry_verdict()` before saving; the old companion fails `/payload`, preventing reconciliation. In standalone AL, reconciliation can be saved, but subsequent `evidence status` fails on the same digest mismatch instead of showing the reconciled `INFRA` record.

Minimal correction: explicitly version or otherwise authenticate the legitimate reconciliation transition while preserving the original observation. Update the companion through that authorized transition before ledger/outbox validation; do not generally regenerate mismatching companions during arbitrary saves. Add public interrupted-check/reconciliation coverage for both platforms and Harness-bound execution.

**R4-FRESH-002 — MEDIUM: task-graph growth prevents operational-issue updates**

Criteria: R4-01; original mission requirements for durable failure reporting and recovery.

Locations:

- `al/codex/skills/al/scripts/al_operational_issues.py:24`
- `al/codex/skills/al/scripts/al_agent_exchange.py:17`
- `al/codex/skills/al/scripts/al_agent_exchange.py:85`
- `al/codex/skills/al/scripts/al_execution_tasks.py:267`

Issue creation captures the current graph revision in its failure-report companion. Every subsequent issue update consumes the previous companion with `current=True`, comparing that historical context against the new graph.

Concrete counterexample: register task A, record a repairing operational issue for A, then register independent task B after producers settle. Task registration increments `graph_revision`. A legitimate next revision of the issue—including an otherwise valid resolution—now fails `/context` before reaching the update or resolution checks. Even an identical retry of the original issue fails. The suggested regeneration cannot occur through this API because consumption precedes production.

Minimal correction: authenticate the prior issue against its recorded observation context, then independently enforce current update authority, immutable issue scope, revision sequencing, and resolution evidence. Publish the next companion with current context. Test unrelated graph growth between issue creation and update.

**R4-FRESH-003 — MEDIUM: standalone typed output accepts invented acceptance criteria**

Criteria: R4-02, R4-04, and consistent behavior required by R4-09.

Locations:

- `al/codex/skills/al/scripts/al_artifact_cli.py:66`
- `al/codex/skills/al/scripts/al_artifact_cli.py:104`
- `al/codex/skills/al/scripts/al_agent_contracts.py:163`
- Contrast: `harness/agent_exchange.py:125`

The standalone submission adapter validates context, specification references, declared inputs/output kind, and content digest, but never compares `acceptance_criteria` with the actual task/package criteria. The common validator checks only bounded strings. Harness performs the missing comparison.

Concrete counterexample: take a valid generated assignment and otherwise valid implementation-result contract, replace its nonempty criteria list with `["invented criterion"]`, and submit through `artifact output --contract-file … --writer …`. All standalone checks still pass and the contract is persisted. `artifact show` also accepts it. The analogous Harness submission refuses `/acceptance_criteria`.

Minimal correction: derive the authoritative criteria from the current task or accepted package and require an exact match before standalone publication, using the same semantics as Harness. Add mirrored public-path tests proving rejection leaves the output index unchanged.

This finding concerns accepted false contract metadata; it does not establish a bypass of protected completion or approval gates.

**R4-FRESH-004 — LOW: operator documentation names a nonexistent CLI subcommand**

Criterion: R4-09 public-flow usability; original mission documentation requirement.

Locations:

- `docs/reliability-platform/user-guide.md:74`
- `docs/reliability-platform/r4-api-contract.md:9`
- `al/codex/skills/al/scripts/al_artifact_cli.py:113`

Both documents instruct users to run `al artifact submit --contract-file PATH`. The registered actions are `record`, `output`, and `show`; no `submit` alias exists.

Minimal correction: document `artifact output` with its required name/input and active-writer arguments. The public tests already use `output`.

**Criterion assessment**

“SATISFIED” below describes the inspected increment and cited evidence only, not formal approval.

| Criterion | Status | Source/evidence assessment |
|---|---|---|
| R4-01 | UNSATISFIED | All ten families have concrete adapters, but verification and failure-report lifecycle consumption breaks in findings 001–002. Producers/consumers include `interview.py:83`, `design.py:35`, `decision_workflow.py:218`, AL `al_planning.py:265`, `al_writer_handoff.py:64`, `al_execution_outputs.py:198`, `al_store.py:150`, `al_operational_issues.py:27`, `al_execution_tasks.py:316`, and `al_publication.py:89`. E69 covers several Harness seams; complete current mirrored evidence is missing. |
| R4-02 | UNSATISFIED | Closed fields and explicit applicability exist in `harness/agent_contracts.py:89` and `agent_preexecution.py:8`; standalone actual criteria are not enforced, finding 003. |
| R4-03 | PENDING | Existing approval and decision checks remain in `execution_authority.py:18`; dependency admission remains at `execution_authority.py:163`. No concrete approval bypass found. Full current integration/CI proof remains unavailable. |
| R4-04 | UNSATISFIED | Fixed sanitized errors and no-publication rejection tests are present; E69 passes those cases. Finding 003 shows an invalid semantic contract is accepted and persisted. |
| R4-05 | SATISFIED | No authority promotion from envelope labels found. Specialized planning, ledger, publication, producer, and approval checks remain separate. See `al_planning.py:272`, `al_ledger.py:418`, `execution_authority.py:18`; E69/E72 support the inspected boundaries. |
| R4-06 | PENDING | Exact-buffer reference resolution exists at `execution_artifacts.py:215`, `agent_exchange.py:49`, and assignment selection. E69 covers tampering and buffer replacement; broader authored-report and mirrored consumer verification is stale. |
| R4-07 | PENDING | Explicit legacy fallbacks and version refusal exist; migration 0022 defaults historical attempts to `unknown`. No pin-upgrade path was added. Migration and both-platform compatibility evidence are stale; finding 001 also exposes a recovery compatibility regression for newly typed records. |
| R4-08 | PENDING | `agent_contracts.py:295` selects authored receipts when unverified; strict consumption remains separately guarded. E64 proves permitted fixture consumption, while the actual generated handoff/authored journey E34 is stale. Universal native qualification is neither established nor required by this review. |
| R4-09 | UNSATISFIED | Real public-path tests exist and E66/E70/E71 support parity and packaging. Finding 003 establishes semantic inconsistency with Harness; finding 004 breaks the documented command. Both vendored functional suites lack current VALID evidence. |
| R4-10 | SATISFIED | Free-form supporting output remains possible; `agent_results.py:8` separately requires typed results where assignments declare them. E69 includes missing-result rejection and supporting-artifact coverage. |
| R4-11 | SATISFIED | Accepted package and DEC-0048v2 retain the full mission without a blanket Knowledge gate. No automatic activation or completion claim was introduced in the inspected delta. Later mission capabilities remain separate work. |

Invariant assessment: R4-I1 and R4-I5 are supported by the inspected boundaries and authorization context. R4-I2–I4 retain important enforcement mechanisms, but complete proof remains pending; findings 001–003 must be addressed before treating the increment as behaviorally complete. No rejected-secret disclosure was found in the inspected typed validation paths.

**Counterexample search and evidence limits**

I traced all ten family adapters and searched context/specification matching, required declarations, producer provenance, immutable reference resolution, metadata presentation versus mutable consumption, claims before hashing and through use, predecessor fencing, native permit ownership, migration defaults, specialized validators, and writer release.

The launch protocol requires an owned in-memory permit and conservatively retains unknown/permitted native liability. `source_dependencies.py` checks active bridge source identity and predecessor claims; it does not infer closure from missing PIDs. E64 and E65 support those bounded paths. Their fixtures do not establish general native process-tree or provider qualification.

Current frozen status reports E62 and E64–E74 as VALID. The retained XMLs confirm E64–E73 outcomes, including **four actual skips in E67**:

- Codex and Claude signed-in start/resume smoke tests were opt-in.
- Two native integration checks skipped because installed Codex 0.164.0 was unavailable.

E63 is stale. E28/E49 mirrored AL suites, E34 handoff journey, E40 migration, E42 native startup, E45 preexecution, and E46 publication checks are stale. Earlier failures and PLAN_CHANGED entries remain historical, not current passing proof. E67 executes the consumer test files also listed in the separate NOT_RUN consumer recipe; that overlap does not change the latter recipe’s recorded status.

Raw execution logs referenced by the ledger were not included in the frozen evidence directory. I inspected the supplied XMLs and ledger/status facts without following live log paths. Hosted CI, complete service/check discovery, mandatory formal bindings, and formal review packets remain missing. CI additionally defines locked Linux/macOS suites and builds; the supplied focused results do not establish those jobs passed.

No frontend implementation changed in this selected delta, and I performed no browser testing or visual qualification. The full original mission remains authorized and incomplete; this diagnostic review neither ends that mission nor grants production readiness.
