-- Historical native attempts remain unknown until an exact fenced disposition is recorded.
ALTER TABLE execution_attempts ADD COLUMN dependency_launch_state TEXT NOT NULL DEFAULT 'unknown'
 CHECK(dependency_launch_state IN ('unknown','permitted','not_started','qualified_closed'));
ALTER TABLE execution_attempts ADD COLUMN dependency_launch_receipt TEXT;
ALTER TABLE execution_attempts ADD COLUMN dependency_launch_manifest TEXT;
