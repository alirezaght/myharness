CREATE TABLE reliability_traces (
 trace_id TEXT PRIMARY KEY NOT NULL, execution_id TEXT NOT NULL UNIQUE REFERENCES executions(id),
 created_at TEXT NOT NULL
);
CREATE TABLE reliability_process_spans (
 span_id TEXT PRIMARY KEY NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
 trace_id TEXT NOT NULL REFERENCES reliability_traces(trace_id),
 execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 authority_generation INTEGER NOT NULL CHECK(authority_generation>=0),
 principal_id TEXT NOT NULL, role TEXT NOT NULL,
 parent_observation_id TEXT NOT NULL REFERENCES reliability_observations(observation_id),
 task_id TEXT, revision TEXT,
 launch_state TEXT NOT NULL CHECK(launch_state IN ('pending','started','not_started')),
 terminal_state TEXT CHECK(terminal_state IN ('exited','observation_failed')),
 started_at TEXT NOT NULL, spawned_at TEXT, finished_at TEXT,
 duration_ms REAL CHECK(duration_ms>=0), returncode INTEGER,
 stop_reason TEXT CHECK(stop_reason IN ('cancel_requested','deadline_exceeded','output_limit','io_error','internal_error')),
 cleanup_status TEXT NOT NULL CHECK(cleanup_status IN ('unobserved','observed','unconfirmed')),
 CHECK((launch_state='pending' AND spawned_at IS NULL) OR launch_state!='pending'),
 CHECK(terminal_state IS NULL OR finished_at IS NOT NULL)
);
CREATE TABLE reliability_timeline_links (
 link_id TEXT PRIMARY KEY NOT NULL, trace_id TEXT NOT NULL REFERENCES reliability_traces(trace_id),
 execution_id TEXT NOT NULL REFERENCES executions(id), epoch_id TEXT NOT NULL,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('process','semantic','retrieval')),
 source_id TEXT NOT NULL, event_kind TEXT NOT NULL CHECK(event_kind IN (
 'process.launch_pending','process.started','process.launch_failed','process.cancel_requested',
 'process.timeout_observed','process.exited','process.cleanup_unconfirmed',
 'semantic.agent','semantic.task','semantic.check','semantic.decision','semantic.output','semantic.run',
 'retrieval.bundle')),
 provenance TEXT NOT NULL CHECK(provenance IN ('runtime_observed','validated_semantic_report','historical_reference')),
 created_at TEXT NOT NULL, principal_id TEXT,
 parent_observation_id TEXT REFERENCES reliability_observations(observation_id),
 parent_span_id TEXT REFERENCES reliability_process_spans(span_id),
 reference_id TEXT, bundle_hash TEXT, snapshot_id TEXT, receipt_id TEXT,
 UNIQUE(execution_id,epoch_id,source_kind,source_id,event_kind)
);
CREATE INDEX idx_reliability_timeline ON reliability_timeline_links(execution_id,epoch_id,created_at,link_id);
CREATE INDEX idx_reliability_process_scope ON reliability_process_spans(execution_id,epoch_id,span_id);
