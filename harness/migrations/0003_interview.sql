ALTER TABLE questions ADD COLUMN kind TEXT NOT NULL DEFAULT 'question'
    CHECK (kind IN ('question', 'decision'));
ALTER TABLE questions ADD COLUMN asked_at TEXT;
ALTER TABLE questions ADD COLUMN skip_source TEXT
    CHECK (skip_source IN ('decision', 'codebase', 'not_relevant'));
ALTER TABLE questions ADD COLUMN catalogue_key TEXT;
