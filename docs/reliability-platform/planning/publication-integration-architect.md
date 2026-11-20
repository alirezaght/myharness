SPEC_STATUS: SPEC_READY  
TASK_CLASSIFICATION: COMPLEX  
IMPLEMENTER: both, sequentially  
DESIGNER_REQUIRED: YES

The publication requires an integrated candidate based on current main, not a direct push of the existing checkpoint. Inspected identities are:

- Checkpoint HEAD: `2ad8bed9bdcaf82212f5f5ab3c8585b79943ca9d`
- Fetched `origin/main`: `6b1da293329c58c2dec1e548c599694abc9a80cf`

No new product decision or user permission is needed. The concrete hard parts are migration-history reconciliation and combining changed process/publication contracts without weakening either stream’s authority.

### Migration collision and required resolution

`harness/db.py:_migrations()` rejects duplicate numeric prefixes. `apply_migrations()` considers a migration applied solely by numeric `version`; it does not compare the stored filename.

Main owns:

- `0019_investigation_sources.sql`
- `0020_planning_sessions.sql`

The checkpoint owns:

- `0019_reliability_observations.sql`
- `0020_reliability_timeline.sql`
- `0021_runtime_policy.sql`
- `0022_dependency_launch_fencing.sql`
- `0023_incident_catalog.sql`

Keeping both filenames fails discovery. Merely renumbering files also fails existing checkpoint databases: their applied versions would cause unrelated migrations to be skipped or existing tables to be recreated.

Use main’s published 0019/0020 unchanged. Assign the checkpoint additions consecutive unused versions, provisionally 0021–0025. Designer must specify a narrow, transactional compatibility bridge for **known checkpoint histories**, including partial prefixes:

1. Read the existing migration ledger and validate the recognized filenames and supporting schema signatures.
2. Preserve main’s applied records exactly.
3. For a recognized checkpoint-only history, move its ledger identities to the assigned canonical versions atomically, without recreating or deleting its data.
4. Apply genuinely missing migrations through the normal runner.
5. Refuse unknown, conflicting or inconsistent histories with an actionable diagnostic; never infer identity from version alone or mark absent schemas applied.
6. Make interrupted/repeated compatibility handling idempotent.

This is metadata reconciliation, not historical incident backfill. It must preserve all existing source facts, observations, investigation records and approval/session state. Do not edit an already-applied main migration to absorb the checkpoint schema.

Main’s `connect()` additionally registers planning-session SQL functions **before** applying migrations. Its `transaction()` invokes `planning_session_events.guard_legacy_transaction()`. Migration 0020 installs triggers using those functions and attached-session write authority. These additions must survive the merge; otherwise ordinary writes may fail or attached-session restrictions may be bypassed.

### Other material integration seams

| Seam | Required reconciliation |
|---|---|
| `execution_operations.py`, `task_environment.py`, `task_tooling.py` | Main adds completion-time task validation and prepared-check planning. Preserve checkpoint exact setup-attempt ownership, dependency claims, budgets, current authority and incident capture around those paths. No direct preparation fallback outside admission. |
| `execution_check_channel.py`, `execution_check_process.py`, `storage.py` | Main introduces retained check handles, completion/publication acknowledgement and storage accounting. Preserve checkpoint process reservations, deadlines and unresolved liabilities. Main’s group settlement explicitly leaves `descendants_settled=None`; it cannot satisfy a qualified whole-tree closure requirement. |
| `execution_worker.py`, `execution_mcp.py`, initialization/bootstrap | Preserve main lifecycle/storage additions together with checkpoint epoch fencing, native presence, safe diagnostics, event preparation and incident transactions. |
| Both AL `al_planning.py`, recovery, ledger, evidence and writer modules | Main adds readiness classification and supported planning-rebind/retained-phase handling. Checkpoint adds immutable typed plan/escalation/reconciliation observations and accepted incident receipts. Retain both validation chains; neither a rebind receipt nor a typed companion substitutes for the other. |
| Installer manifest/bootstrap/import closure | Main adds historical release compatibility and source materialization. Preserve these alongside checkpoint source-qualified control methods and exact helper/import closures. Regenerate only new candidate closure records after source settles; do not alter old pins or broaden historical minimums. |
| Dashboard server, authoring/interview/spec views and styles | Compose main planning-session/investigation actions with checkpoint incident/timeline/policy routes. Preserve Host/Origin/body limits and source-selection fencing. |
| Knowledge contributions/query/integration | Main has new contribution contracts and provenance handling. Revalidate future reliability adapters against these actual interfaces rather than restoring the baseline implementations. |
| `pyproject.toml` | Preserve main’s packaging of both `harness` and `al.installer`, plus skill/migration resources. Verify the built distribution contains all new reliability modules and migrations. |

Main’s `investigation_*` modules represent planning/source investigation and acceptance; checkpoint `reliability/investigations.py` represents incident interpretation. They are related but not interchangeable. Preserve both and link them through explicit provenance where later work requires it.

### Minimal implementation and publication sequence

1. Finish current owned checks and capture the complete checkpoint contribution, including untracked production resources. Preserve its evidence as checkpoint evidence.
2. Create a separate integration candidate from fetched main and apply the captured contribution. Do not reset the working checkpoint, overwrite main wholesale, or force-push.
3. Designer supplies bounded pseudocode for migration-history reconciliation and the changed check-handle/resource-liability/publication interaction.
4. Engineer owns migration/runner changes, process/policy/typed-publication integration, both-platform AL merges and regenerated candidate closures.
5. Builder follows for packaging, documentation, dashboard integration and the required merged-candidate checks.
6. Recheck remote main immediately before publication. If it advanced, integrate that delta and refresh affected checks. Publish only a candidate containing the remote history; retain the original checkpoint and failed attempts.

No parallel writers are justified across these shared interfaces.

### Required checks on the merged candidate

- Disposable migration fixtures: fresh database, main through 0019/0020, every supported checkpoint migration prefix, repeat execution, interrupted reconciliation and unknown/mismatched histories. Preserve actual data, foreign keys and main session triggers.
- Main planning-session CLI/jobs/clock/distribution tests and investigation-source/review tests.
- Checkpoint policy budgets, dependency claims, setup continuation, process settlement, typed contracts, ledger reconciliation and incident publication tests.
- Main retained-check/storage/tooling tests combined with checkpoint cancellation/deadline/unknown-liability cases. A passed check must not clear an unrelated unresolved resource claim.
- Both AL distributions’ planning-rebind, recovery, evidence, typed-output and incident-source paths; installer historical compatibility, protected membership, parity and source/import closure.
- Existing CI recipes: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -m "not browser"` and `uv build`, using the locked setup. Main and checkpoint CI target Linux and macOS; no job-level timeout override was present in inspected workflows. Hosted runner limits remain external verification context.
- Browser checks use the existing sandboxed Chrome wrapper and `uv run pytest -m browser`. Inspect planning-session/incident navigation, delayed responses, selection changes, focus, clipping and desktop/narrow alignment.
- Retain Linux containment checks separately. Local results do not establish hosted CI, provider authentication or native qualification.

### Remaining-feature handoff implications

R5b–R11 remain approved work, but their retained plans need consumer revalidation against the merged source:

- **R5b:** use the actual domain-partition correction and main’s protected launcher/materialization/storage contracts; no fabricated Harness binding.
- **R6:** consume the implemented canonical incident catalog and real correction history; avoid duplicating main’s proposal/decision mechanisms.
- **R7:** reuse merged evidence/storage/runner provenance and retained-check lifecycle; simulation remains distinct from faithful reproduction.
- **R8:** revalidate actual planning-session, approval and configuration lineage before activation/canary/rollback design.
- **R9:** extend main’s current Knowledge contribution/provenance contracts; no second memory store.
- **R10:** compose main recovery/rebind improvements with checkpoint budgets, epochs and unresolved liabilities; no generic replay shortcut.
- **R11:** preserve primary Claude and fixed AL role routing; qualify exact installed versions separately and compose all existing dashboard areas.

Current inspected checkpoint hashes include `harness/db.py` `7e8d2ce679a18cefd0b31cb45ad559af39afa371bf659210ea85e0cb0843d302`, `execution_operations.py` `0ea1ffe46e182c8b7ffd88222fcf8fb8229d601e0c04c5f3d16161ca0a3247b3`, and `execution_worker.py` `785f15ab0fd22d7a820a665d08c8ca8389e7d22cfb4c45fa52de614668e34c65`. These bind this inspection, not a merged release.

No source/ref/pin/state mutation, merge, test, service, database connection or publication was performed in this turn.
