CREATE TABLE investigation_bindings (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), project TEXT NOT NULL,
 selection_json TEXT NOT NULL, active_bundle TEXT, validation_generation INTEGER NOT NULL DEFAULT 0,
 recovery_json TEXT, FOREIGN KEY(task_id,active_bundle) REFERENCES investigation_bundles(task_id,id)
);
CREATE TABLE investigation_bundles (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), project TEXT NOT NULL,
 parent_id TEXT, job_id TEXT NOT NULL, attempt INTEGER NOT NULL, policy TEXT NOT NULL,
 content_json TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,id), FOREIGN KEY(task_id,parent_id) REFERENCES investigation_bundles(task_id,id),
 FOREIGN KEY(job_id,attempt) REFERENCES authoring_attempts(job_id,attempt)
);
CREATE TABLE investigation_sources (
 task_id TEXT NOT NULL, bundle_id TEXT NOT NULL, id TEXT NOT NULL,
 content_json TEXT NOT NULL, PRIMARY KEY(task_id,bundle_id,id),
 FOREIGN KEY(task_id,bundle_id) REFERENCES investigation_bundles(task_id,id)
);
CREATE TABLE investigation_acceptances (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, bundle_id TEXT NOT NULL,
 coverage_hash TEXT NOT NULL, basis_json TEXT NOT NULL, package_hash TEXT NOT NULL,
 provenance TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(task_id,bundle_id) REFERENCES investigation_bundles(task_id,id)
);
CREATE TABLE investigation_decisions (
 task_id TEXT NOT NULL REFERENCES tasks(id), concern TEXT NOT NULL, revision INTEGER NOT NULL,
 question_id INTEGER NOT NULL REFERENCES questions(id), premise_json TEXT NOT NULL,
 supersedes INTEGER REFERENCES questions(id), PRIMARY KEY(task_id,concern,revision)
);
CREATE TABLE investigation_progress (
 job_id TEXT NOT NULL, attempt INTEGER NOT NULL, stage TEXT NOT NULL,
 completed_json TEXT NOT NULL, diagnostic_json TEXT, observations_json TEXT,
 PRIMARY KEY(job_id,attempt), FOREIGN KEY(job_id,attempt) REFERENCES authoring_attempts(job_id,attempt)
);
