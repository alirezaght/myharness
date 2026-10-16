CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    complexity TEXT NOT NULL,
    complexity_source TEXT NOT NULL CHECK (complexity_source IN ('developer', 'proposed')),
    phase TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE phase_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    from_phase TEXT,
    to_phase TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_phase_transitions_task ON phase_transitions(task_id, id);
