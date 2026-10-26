"""Contained launcher mode retains protected bytes while AL state stays writable."""

import json
import subprocess

from al.tests.runtime_run_helpers import invoke, launched, run_record, start  # noqa: F401


def existing(launched, *arguments):
    _, _, repo, launcher, env = launched
    return invoke([launcher, 'run', '--repository', repo, '--platform', 'codex',
                   '--session', 'pin-flow', '--existing-run', '--', *arguments], repo, env)


def test_existing_run_requires_active_and_preserves_origin(launched):
    missing = existing(launched, 'show', '--line')
    assert missing.returncode != 0
    state_path = start(launched)
    path, record = run_record(launched)
    before = path.read_bytes()
    initial = __import__('pathlib').Path(record['initialization']['stored_path'])
    initialization = initial.read_bytes()
    result = existing(launched, 'show', '--line')
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before and initial.read_bytes() == initialization
    assert existing(launched, 'start', '--spec', 'forbidden').returncode != 0
    assert state_path.exists()
    # A RESERVED transaction cannot be repaired from the contained mode.
    record['status'] = 'RESERVED'
    path.write_text(json.dumps(record))
    assert existing(launched, 'show', '--line').returncode != 0
