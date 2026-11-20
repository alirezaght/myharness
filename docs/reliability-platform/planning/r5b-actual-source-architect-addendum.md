SPEC_STATUS: SPEC_READY  
TASK_CLASSIFICATION: COMPLEX  
DESIGNER_REQUIRED: YES

The retained R5b protocol needs a concrete schema/API revision before implementation. Current R5a cannot accept standalone records: its canonical storage and consumers deliberately require actual Harness identities. This is an integration correction within the approved mission, not a new user decision.

**Actual mismatch**

- `0023_incident_catalog.sql` makes `task_id` and `execution_id` non-null foreign keys, requires an actual `(execution_id, epoch_id)`, and enforces task/execution binding by trigger. Its generated event relation requires `incident_event_sources`, whose foreign key targets real `execution_events`.
- `catalog._insert()` obtains a real execution and calls existing Harness scope validation. `scope_filter()` accepts task scope or exact task/execution/epoch scope.
- `source_queries.source_relation()` resolves AL test/review sources through `execution_events`.
- `incident_scope.repository_scope()` and dashboard views validate repository ownership through task/execution records.
- Investigation heads/revisions reference the existing catalog. Group suggestions require a Harness task, and grouping scope/metadata assume Harness identifiers.

Therefore adding `domain="standalone_al"` to an API document, passing null IDs into current constructors, or creating synthetic executions would be incorrect. Reusing `event_sources.prepare()` is also incorrect: it explicitly validates a launched Harness handoff, integration binding and outbox event.

**Minimal integration direction**

Preserve the existing Harness tables, insert shapes, foreign keys, triggers and authority paths. Introduce an **additive standalone domain partition behind the same catalog service**, rather than rebuilding the existing catalog during startup:

1. Add standalone source-run, retained-export, request/receipt and incident-source relations. Standalone facts have real repository identity, platform, session, AL run, pin and source-record identity; they have no Harness task/execution/epoch/approval.
2. Extend the existing catalog facade with explicit domain dispatch. Keep old task/execution selectors unchanged. A standalone selector uses a server-enrolled source-run ID scoped to the configured repository. Repository-wide mixed listing is a separate explicit selector, not an omission of existing task checks.
3. Present both partitions through one normalized catalog projection, with explicit domain and nullable *presentation* fields for inapplicable Harness identity. Preserve existing R1 IDs and old API response contracts. Reserve a disjoint namespace for new standalone incident IDs and reject collisions.
4. Keep existing Harness investigation/group storage unchanged. Add domain-specific persistence relations for standalone interpretations/groups, reusing the existing validation, revision, request-replay and presentation logic. All entry points must resolve the domain first. Do not duplicate business classification logic or introduce another memory system.
5. Existing Harness source constructors must never accept standalone selectors. Standalone insertion is reachable only from the trusted retained-export collector; there is no generic catalog insert/import endpoint.

This additive partition avoids an unbounded startup table rebuild, preserves immutable old bridges and supplies the same catalog to future R6/R9 consumers. It supersedes the retained Designer paragraph that assumed the existing catalog could simply acquire nullable Harness bindings. Designer must specify the exact partition relations, domain-dispatched interpretation interfaces and database integrity constraints—not merely add a discriminator.

**Actual receipt compatibility**

`al_incident_sources.py` presently implements **Harness-bound** receipts:

- `prepare_results()` returns immediately without `integration`.
- It stores references in `integration.incident_results`.
- `accepted-ledger-result-v1` contains exactly `schema_version`, `source_kind`, Harness `binding`, `result_id`, `status`, sanitized `counts`, `purpose`, and `validity_at_capture`.
- It excludes running/nonterminal entries and empty-scope preflights; existing references are retained rather than regenerated.
- `review_accepted()` also returns without integration. For bound runs it retains the accepted report reference and queues `review.accepted` with `review_id`, `stage` and its closed reference.

Consequently, a genuine standalone run does **not** already possess these integration receipts. R5b must not invent their Harness binding.

The standalone exporter should produce its own versioned, domain-specific sanitized receipt from an authenticated existing standalone snapshot and accepted source artifacts. It may reuse safe projection rules, but must distinguish:

- an actual retained accepted artifact;
- a recorded terminal ledger fact authenticated at export;
- present validity/availability;
- an authorized reconciliation observation.

It must not claim an at-completion immutable receipt where none exists. Stable terminal identity and conflict detection remain mandatory; a changed terminal fact cannot become a new revision merely by hashing it. Accepted review records require the actual retained acceptance reference and stable finding identity. No dedicated standalone typed-rejection producer currently exists; generic command failure or error text cannot supply one.

**Exporter/collector boundary retained**

Keep the fixed protected `incident capabilities/export/page` methods, selected existing run and exact qualified pin. The collector supplies configured repository plus validated platform/session, never arbitrary paths, executables, uploaded reports or command text.

Exporter qualification must establish that its complete dispatch/load/artifact-read path:

- changes no AL status, approval, ledger observation, authority or original source history;
- publishes only bounded immutable derived export files;
- handles terminal runs through an explicitly supported observational method;
- refuses unsupported pins without upgrade, new-run creation or direct-state fallback;
- uses confined bounded descriptor reads and one authenticated snapshot;
- exports no raw logs, argv/environment, private transcripts, arbitrary report prose or secret-derived grouping hashes.

The existing decision-only authenticated-buffer receipt is not export authority. Existing `--existing-run` and ordinary process exit are not, by themselves, exporter qualification.

Retain the planned complete-export retention **before** SQLite ingestion. Request/export/source identity replay and conflicts must commit atomically; no database transaction spans launcher execution. Source disappearance after complete retention does not erase retained evidence. Unknown process closure cannot become a successful collection receipt.

**Required Designer revision and acceptance**

The concrete hard part remains cross-store provenance plus durable replay, now coupled to domain-partition integrity and selector fencing. Revise the pseudocode against the actual R5a interfaces for:

- standalone identity/source uniqueness and domain-specific foreign keys;
- unified catalog pagination and source navigation without weakening Harness scope checks;
- shared investigation/group validation with domain-specific persistence;
- exact export capabilities and snapshot/receipt schemas;
- retained preview/request/export/source conflict handling and every crash boundary;
- read-only startup/GET and unchanged bounded unindexed legacy projection.

Prove real protected standalone export → retention → catalog → dashboard, foreign-domain refusal, old-pin refusal, unchanged run bytes/authority, secret-canary exclusion, repeated-fact reuse, changed-fact conflict, source disappearance, and domain-correct investigation/group actions. Fixtures alone cannot qualify native execution or arbitrary process closure.

No unresolved product choice remains. The internal qualification question for Designer is the exact source-bound exporter call graph and safe completion evidence; unsupported paths must remain explicit until that protocol is implemented and tested. R5a work remains independent of this pending R5b implementation.

**Inspected bindings**

| Path | SHA-256 |
|---|---|
| `harness/migrations/0023_incident_catalog.sql` | `91819bf441e752263fda7ca3cffe62041fa428b05c7664744b17f076272a19e7` |
| `harness/reliability/catalog.py` | `9d522ba8bb88a2fa654865906dc31e31811bf2d64407b38818f70c0fce0f06e5` |
| `harness/reliability/investigations.py` | `20935038151ece0a974ffc900d88f61fc6e8839d0cee9eac55c26b083865391f` |
| `harness/reliability/grouping.py` | `c5c8b705f91ba7bbfa7ed78eb60f8d78407eca97d8cb2ffb93df862d1d18d2bb` |
| `harness/reliability/event_sources.py` | `524b03c9bc0af4ca3bff872ff7cb99e7c203d22eb0b3794be6f9bd061af181d3` |
| `harness/reliability/source_queries.py` | `c835f3c84acc13fc7a28184a7a3a536a29325ba9139b738059240e5636c02de1` |
| `harness/dashboard/incident_scope.py` | `32d3f425ebdedcdfcdc368b033fa8582267b21ffef17c3dace04fdfae0e0df5f` |
| Both `al/{codex,claude}/skills/al/scripts/al_incident_sources.py` | `72bc0f91f43a188ef6a768fbd5fdc337f77b1cf812cfc905109bdcecbb29df68` |

Repeated catalog/schema/receipt hashes matched during inspection. No edits, tests, services, database connections or native/authentication activity occurred.
