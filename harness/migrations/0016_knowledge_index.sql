CREATE VIRTUAL TABLE knowledge_assertion_fts USING fts5(text, subject, object, tokenize='unicode61');
CREATE TABLE knowledge_extraction_cache (
 version_id TEXT PRIMARY KEY REFERENCES knowledge_source_versions(version_id),
 extractor_version TEXT NOT NULL, coverage_json TEXT NOT NULL,
 assertion_count INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE knowledge_extraction_members (
 version_id TEXT NOT NULL REFERENCES knowledge_source_versions(version_id),
 assertion_id TEXT NOT NULL REFERENCES knowledge_assertions(assertion_id),
 PRIMARY KEY(version_id, assertion_id)
);
CREATE TABLE knowledge_source_class (
 source_id TEXT PRIMARY KEY REFERENCES knowledge_sources(source_id),
 material_class TEXT NOT NULL CHECK(material_class IN
 ('repository','harness-record','runtime-record','contribution','lesson-import'))
);
CREATE TABLE knowledge_snapshot_source_state (
 snapshot_id TEXT NOT NULL REFERENCES knowledge_snapshots(snapshot_id),
 source_id TEXT NOT NULL REFERENCES knowledge_sources(source_id),
 reason TEXT NOT NULL, detail_json TEXT NOT NULL, PRIMARY KEY(snapshot_id, source_id)
);
CREATE TABLE knowledge_snapshot_cursor (
 snapshot_id TEXT PRIMARY KEY REFERENCES knowledge_snapshots(snapshot_id),
 mode TEXT NOT NULL CHECK(mode IN ('working-tree','commit')), branch TEXT, head TEXT, parent_head TEXT,
 ancestry TEXT NOT NULL CHECK(ancestry IN ('initial','same','descendant','diverged','unknown')),
 inventory_sha256 TEXT, names_complete INTEGER NOT NULL, unknown_count INTEGER NOT NULL,
 deleted_count INTEGER NOT NULL
);
CREATE TABLE knowledge_source_cursor (
 project_id TEXT PRIMARY KEY REFERENCES knowledge_projects(project_id),
 snapshot_id TEXT NOT NULL REFERENCES knowledge_snapshots(snapshot_id), head TEXT, branch TEXT,
 inventory_sha256 TEXT, extractor_version TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE knowledge_contributions (
 contribution_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_id TEXT NOT NULL,
 version_id TEXT NOT NULL, execution_id TEXT, task_id TEXT, epoch_id TEXT,
 producer_principal TEXT NOT NULL, producer_role TEXT NOT NULL,
 report_kind TEXT NOT NULL CHECK(report_kind IN
 ('plan','pseudocode','implementer-report','finding','scout-report','other')),
 artifact_sha256 TEXT NOT NULL, graph_revision INTEGER, created_at TEXT NOT NULL,
 UNIQUE(project_id, artifact_sha256)
);
CREATE TABLE knowledge_review_projections (
 project_id TEXT NOT NULL, packet_hash TEXT NOT NULL, execution_id TEXT NOT NULL,
 review_id TEXT NOT NULL, stage TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 policy_generation INTEGER NOT NULL, permitted_digest TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(project_id, packet_hash)
);
CREATE TABLE knowledge_review_projection_sources (
 project_id TEXT NOT NULL, packet_hash TEXT NOT NULL, version_id TEXT NOT NULL,
 PRIMARY KEY(project_id, packet_hash, version_id),
 FOREIGN KEY(project_id, packet_hash) REFERENCES knowledge_review_projections(project_id, packet_hash)
);
CREATE TABLE knowledge_scope_issues (
 execution_id TEXT NOT NULL, principal_id TEXT NOT NULL, purpose TEXT NOT NULL,
 packet_hash TEXT NOT NULL DEFAULT '', first_issued_at TEXT NOT NULL,
 PRIMARY KEY(execution_id, principal_id, purpose, packet_hash)
);
CREATE TABLE knowledge_bundle_scope (
 bundle_hash TEXT PRIMARY KEY REFERENCES knowledge_bundles(bundle_hash),
 principal_id TEXT NOT NULL, purpose TEXT NOT NULL, packet_hash TEXT,
 policy_generation INTEGER NOT NULL, execution_id TEXT
);
CREATE TABLE knowledge_bundle_state (
 bundle_hash TEXT PRIMARY KEY REFERENCES knowledge_bundles(bundle_hash),
 reason TEXT NOT NULL CHECK(reason IN ('grant-revoked','purged','expired','source-policy')),
 cause_ref TEXT, at TEXT NOT NULL
);
CREATE TABLE knowledge_query_cache (
 cache_key TEXT PRIMARY KEY, project_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 policy_digest TEXT NOT NULL, bundle_hash TEXT NOT NULL REFERENCES knowledge_bundles(bundle_hash),
 created_at TEXT NOT NULL
);
CREATE TABLE knowledge_import_sources (
 version_id TEXT PRIMARY KEY REFERENCES knowledge_source_versions(version_id),
 grant_id TEXT NOT NULL REFERENCES knowledge_grants(grant_id), lesson_id TEXT NOT NULL,
 version INTEGER NOT NULL
);
CREATE TABLE knowledge_purges (
 purge_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, target_kind TEXT NOT NULL,
 target_sha256 TEXT NOT NULL, reason_code TEXT NOT NULL, affected_versions INTEGER NOT NULL,
 affected_assertions INTEGER NOT NULL, affected_bundles INTEGER NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE knowledge_assertion_state (
 assertion_id TEXT PRIMARY KEY REFERENCES knowledge_assertions(assertion_id),
 state TEXT NOT NULL CHECK(state IN ('purged')), purge_id TEXT REFERENCES knowledge_purges(purge_id)
);
CREATE TABLE knowledge_receipts (
 receipt_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, bundle_hash TEXT,
 snapshot_id TEXT, policy_digest TEXT NOT NULL, purpose TEXT NOT NULL,
 selected_sources_json TEXT NOT NULL, limits_json TEXT NOT NULL, source_reads INTEGER NOT NULL,
 elapsed_ms REAL NOT NULL, integrity_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE knowledge_source_runtime (
 version_id TEXT PRIMARY KEY REFERENCES knowledge_source_versions(version_id),
 task_id TEXT, execution_id TEXT, epoch_id TEXT, event_id TEXT, evidence_id TEXT
);
