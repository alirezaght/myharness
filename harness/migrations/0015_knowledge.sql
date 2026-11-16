CREATE TABLE knowledge_projects (
 project_id TEXT PRIMARY KEY, repository_identity TEXT NOT NULL UNIQUE,
 active_snapshot_id TEXT, policy_generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE knowledge_sources (
 source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES knowledge_projects(project_id),
 locator TEXT NOT NULL, kind TEXT NOT NULL, visibility TEXT NOT NULL,
 UNIQUE(project_id, locator)
);
CREATE TABLE knowledge_source_versions (
 version_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES knowledge_sources(source_id),
 content_sha256 TEXT NOT NULL, extractor_id TEXT NOT NULL, extractor_version TEXT NOT NULL,
 observation_json TEXT NOT NULL, extraction_status TEXT NOT NULL
);
CREATE TABLE knowledge_snapshots (
 snapshot_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES knowledge_projects(project_id),
 parent_id TEXT REFERENCES knowledge_snapshots(snapshot_id), observation_json TEXT NOT NULL,
 enumeration_complete INTEGER NOT NULL, coverage_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE knowledge_snapshot_sources (
 snapshot_id TEXT NOT NULL REFERENCES knowledge_snapshots(snapshot_id),
 source_id TEXT NOT NULL REFERENCES knowledge_sources(source_id),
 version_id TEXT NOT NULL REFERENCES knowledge_source_versions(version_id),
 membership_state TEXT NOT NULL CHECK(membership_state IN ('present','deleted','unknown')),
 PRIMARY KEY(snapshot_id, source_id)
);
CREATE TABLE knowledge_entities (
 entity_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES knowledge_projects(project_id),
 kind TEXT NOT NULL, scoped_key TEXT NOT NULL, UNIQUE(project_id, scoped_key)
);
CREATE TABLE knowledge_assertions (
 assertion_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES knowledge_projects(project_id),
 subject_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id), predicate TEXT NOT NULL,
 object_id TEXT REFERENCES knowledge_entities(entity_id), value_json TEXT NOT NULL,
 applicability_json TEXT NOT NULL, origin TEXT NOT NULL, verification TEXT NOT NULL,
 confidence REAL NOT NULL, text TEXT NOT NULL
);
CREATE TABLE knowledge_evidence (
 assertion_id TEXT NOT NULL REFERENCES knowledge_assertions(assertion_id),
 version_id TEXT NOT NULL REFERENCES knowledge_source_versions(version_id),
 start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL, span_sha256 TEXT NOT NULL,
 PRIMARY KEY(assertion_id, version_id, start_byte, end_byte)
);
CREATE TABLE knowledge_derivations (
 assertion_id TEXT NOT NULL REFERENCES knowledge_assertions(assertion_id),
 parent_assertion_id TEXT NOT NULL REFERENCES knowledge_assertions(assertion_id),
 PRIMARY KEY(assertion_id, parent_assertion_id)
);
CREATE TABLE knowledge_snapshot_assertions (
 snapshot_id TEXT NOT NULL REFERENCES knowledge_snapshots(snapshot_id),
 assertion_id TEXT NOT NULL REFERENCES knowledge_assertions(assertion_id),
 PRIMARY KEY(snapshot_id, assertion_id)
);
CREATE TABLE knowledge_aliases (
 alias_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES knowledge_projects(project_id),
 alias TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES knowledge_entities(entity_id),
 assertion_id TEXT NOT NULL REFERENCES knowledge_assertions(assertion_id)
);
CREATE TABLE knowledge_bundles (
 bundle_hash TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES knowledge_projects(project_id),
 snapshot_id TEXT NOT NULL REFERENCES knowledge_snapshots(snapshot_id), policy_digest TEXT NOT NULL,
 purpose TEXT NOT NULL, payload TEXT, created_at TEXT NOT NULL, invalidated_at TEXT
);
CREATE TABLE knowledge_bundle_sources (
 bundle_hash TEXT NOT NULL REFERENCES knowledge_bundles(bundle_hash),
 version_id TEXT NOT NULL REFERENCES knowledge_source_versions(version_id),
 PRIMARY KEY(bundle_hash, version_id)
);
CREATE TABLE knowledge_bundle_pins (
 bundle_hash TEXT NOT NULL REFERENCES knowledge_bundles(bundle_hash),
 owner_ref TEXT NOT NULL, PRIMARY KEY(bundle_hash, owner_ref)
);
CREATE TABLE knowledge_refresh_attempts (
 attempt_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES knowledge_projects(project_id),
 status TEXT NOT NULL, coverage_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX knowledge_evidence_version ON knowledge_evidence(version_id);
CREATE INDEX knowledge_assertion_subject ON knowledge_assertions(project_id, subject_id);
CREATE INDEX knowledge_assertion_object ON knowledge_assertions(project_id, object_id);
CREATE TABLE knowledge_lessons (
 lesson_id TEXT NOT NULL, version INTEGER NOT NULL, project_id TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, payload TEXT NOT NULL, approval_ref TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(lesson_id, version)
);
CREATE TABLE knowledge_grants (
 grant_id TEXT PRIMARY KEY, source_project TEXT NOT NULL, destination_project TEXT NOT NULL,
 material_sha256 TEXT NOT NULL, approval_ref TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE knowledge_imports (
 project_id TEXT NOT NULL, grant_id TEXT NOT NULL REFERENCES knowledge_grants(grant_id),
 lesson_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'suggestion',
 PRIMARY KEY(project_id, grant_id, lesson_id, version)
);
