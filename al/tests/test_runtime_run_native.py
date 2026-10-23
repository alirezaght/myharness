"""A Claude conversation drives pinned runs without a supervisor-chosen run id."""
import hashlib
import json

from al.tests.runtime_run_helpers import invoke, launched  # noqa: F401


def native_run(launched, platform, native, *arguments):
    _, _, repo, launcher, env = launched
    environment = {k: v for k, v in env.items() if k not in {'CLAUDE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID'}}
    if native:
        environment['CLAUDE_SESSION_ID'] = native
    return invoke([launcher, 'run', '--repository', repo, '--platform', platform, '--', *arguments], repo, environment)


def test_implicit_claude_start_mints_binds_and_resolves_the_conversation_run(launched):
    _, home, repo, _, _ = launched
    started = native_run(launched, 'claude', 'conversation-a', 'start', '--spec', 'Native conversation pinned run.')
    assert started.returncode == 0, started.stdout[-2000:] + started.stderr[-2000:]
    pointer = repo / '.claude/al-runtime/conversations' / (hashlib.sha256(b'conversation-a').hexdigest() + '.json')
    session = json.loads(pointer.read_bytes())['session_id']
    assert json.loads(pointer.read_bytes())['conversation_id'] == 'conversation-a'
    run = json.loads(next((home / 'installer/al-run-pins/runs').glob('*.json')).read_bytes())
    assert run['session_id'] == session and run['status'] == 'ACTIVE'
    state = json.loads((repo / '.claude/al-runtime' / (session + '.json')).read_bytes())
    assert state['native_conversation_id'] == 'conversation-a' and state['runtime_origin']['run_id'] == run['run_id']
    shown = native_run(launched, 'claude', 'conversation-a', 'show', '--line')
    assert shown.returncode == 0, shown.stderr[-2000:]
    other = native_run(launched, 'claude', 'conversation-b', 'show', '--line')
    assert other.returncode != 0 and 'no active AL run for this Claude conversation' in other.stdout + other.stderr


def test_implicit_run_id_is_refused_outside_a_claude_conversation(launched):
    for platform, native in (('codex', 'conversation-a'), ('claude', None)):
        result = native_run(launched, platform, native, 'start', '--spec', 'Explicit run id is required here.')
        assert result.returncode != 0 and 'requires --session outside a Claude conversation' in (result.stdout + result.stderr)
