# Knowledge platform investigation — 16 November 2026

Status: source-backed investigation, not architecture or implementation approval.
Harness task: `t-ab980907`. Protected AL session: `knowledge-platform-20261116`.
Source revision: `bab69a7a1a008a155df7c0b238cf507af9c3091e`.
The original developer request is preserved in [mission.md](mission.md).

## Existing capabilities and reuse

| Existing capability | Verified implementation | Implication for this mission |
| --- | --- | --- |
| Shared project memory and local execution state | [SPEC §6](../SPEC.md), [DEC-0003](../decisions/DEC-0003-sqlite-access.md), `harness/db.py` | Git Markdown remains the authority for shared specifications/decisions. Existing stdlib SQLite and numbered migrations serve local state. A retrieval index must be a derived view, not a replacement authority. |
| Versioned decisions with rationale and impact | `harness/decisions.py`, `harness/registry.py`, `harness/decision_workflow.py` | Adapt current/history records and supersession chains. Do not invent a second decision registry. |
| Cross-project suggestions | `harness/decision_suggest.py` | Sources currently require explicitly named repositories and yield suggestions only. Preserve that boundary while improving relevance and relationships. |
| Repository investigation | `harness/analyzer.py`, `harness/repo_scan.py`, `harness/repo_manifests.py`, `harness/repo_probes.py`, `harness/repository_audit.py` | Reuse bounded safe inventory, exclusions, manifest evidence and ownership investigation. Keyword matches are leads, not proof. |
| Current relevance baseline | `harness/repo_relevance.py` | Ranks files by request-token presence in paths/text; this is a concrete baseline to measure against. |
| Source-bound context import | `harness/context_import.py` | Already validates source hashes and exact supporting quotes; imports answers/design/tests as proposals rather than approvals. Extend the provenance path. |
| Batch review and approval separation | `harness/review_package.py`, `tests/test_context_review_package.py` | Existing review packages bind exact proposed content. Batch acceptance explicitly does not authorize implementation; final approval remains separate. |
| Requirement identities | `harness/requirement_registry.py` | Preserve criterion/invariant identities and accepted-source lineage instead of assigning disconnected knowledge requirements. |
| Existing graph | `harness/execution_projection.py`, migrations `0011_executions.sql` and `0014_execution_epochs.sql` | Execution events already project to epoch-scoped entities/edges. Reuse their identities and transactional ingestion instead of replacing the graph/event store. |
| Persistent task history | `harness/state.py` and its state mixins | Adapt existing events and checkpoints. Do not infer task completion from an agent report or a process exit. |
| Architecture relationships and UI | `harness/diagram_model.py`, `harness/change_diagrams.py`, `harness/dashboard/diagrams_view.py` | Reuse effective design notation and component/flow rendering. Proposed, accepted and illustrative diagrams have distinct meanings. |
| Native agent boundary | `harness/execution_operations.py`, `harness/execution_mcp.py`, `harness/execution_authority.py`, `harness/execution_principals.py`, `harness/native_tool_policy.py` | Add context through the existing guarded bridge and actual principal/epoch. Generic host memory tools are disabled; do not enable a competing memory channel. |
| Isolated AL reviews | `al/codex/skills/al/reference/review.md` and the corresponding Claude contract | Fresh review excludes plan, pseudocode and implementer summaries. Knowledge retrieval must enforce that restriction through provenance, aliases, traversal and caches. |

## Gaps established so far

Existing components retain useful information, but do not collectively provide a
versioned repository/requirement/decision/assertion graph with one policy-aware
retrieval interface. Current keyword relevance does not resolve entities, follow
dependency paths, explain source-backed impact, or distinguish conflicting claims
at retrieval time. Source freshness and project/reviewer visibility require
explicit design; durable storage alone does not establish current truth.

The built-in Harness investigation reported 19 approved decisions, 132 facts, one
language assumption and two ownership questions. Its fixture-package ownership
questions are investigation leads to resolve from source, not questions requiring
the developer to select the root package. Counts and keyword matches do not prove
complete discovery.

## Technology research

The installed requested Python interpreter exposes SQLite 3.50.4 and supports
FTS5, verified by a transient in-memory capability probe. This does not verify the
future platform or establish that every CI/runtime SQLite build supports FTS5.

- [SQLite FTS5](https://www.sqlite.org/fts5.html) supplies indexed text search,
  snippets and BM25. Its external-content index must be kept consistent with the
  underlying rows; merely creating triggers does not backfill an existing index.
- [SQLite recursive queries](https://www.sqlite.org/lang_with.html) support graph
  walks. Traversal still needs explicit cycle, work, depth and visibility bounds.
- [SQLite WAL documentation](https://www.sqlite.org/wal.html#walreset) documents a
  concurrent write/checkpoint race fixed in 3.51.3, with backports including
  3.50.7. The locally observed 3.50.4 build needs a supported-version or correctly
  bounded concurrency strategy before adding concurrent index writers. No runtime
  or database mode was changed during investigation.
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/) and its
  [research paper](https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/)
  demonstrate entity graphs and community summaries for corpus-level questions.
  They inform the alternatives assessment, not evidence that AL needs that whole
  pipeline or would reproduce its benchmark results.

## Verification and boundaries

This phase inspected source, tests, local Git state and primary documentation.
It did not ingest other projects, read secrets, contact production databases,
install dependencies, change product code or claim product tests passed.
The read-only AL-Architect investigates the actual repository; its full proposal
will be presented through the existing Harness review/approval workflow.
