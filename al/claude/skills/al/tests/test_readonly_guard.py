"""Exercise the real hook boundary, including malformed and allowed input."""
import json
import subprocess
import sys

import pytest
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "al_readonly_guard.py"


def run_hook(value):
    raw = value if isinstance(value, str) else json.dumps(value)
    return subprocess.run([sys.executable, str(GUARD)], input=raw, text=True,
                          capture_output=True, check=False, timeout=5)


def decision(result):
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]["permissionDecision"]


def test_hook_allows_a_quoted_find_search_and_denies_real_actions():
    safe = run_hook({"tool_name": "Bash", "tool_input": {"command": "find . -name '-delete'"}})
    assert safe.returncode == 0 and safe.stdout == ""
    mutation = run_hook({"tool_name": "Bash", "tool_input": {"command": "find . -type f -delete"}})
    assert mutation.returncode == 2 and decision(mutation) == "deny"


def test_hook_fails_closed_for_malformed_bash_input_and_ignores_other_tools():
    for raw in ("{", {"tool_name": "Bash", "tool_input": {"command": 4}}):
        result = run_hook(raw)
        assert result.returncode == 2 and decision(result) == "deny"
    other = run_hook({"tool_name": "Read", "tool_input": {}})
    assert other.returncode == 0 and other.stdout == ""


@pytest.mark.parametrize('background', [True, None, 0, 1, 'false', 'true', [], {}])
def test_background_metadata_fails_closed(background):
    result = run_hook({'tool_name': 'Bash', 'tool_input':
                       {'command': 'git status', 'run_in_background': background}})
    assert result.returncode == 2 and decision(result) == 'deny'
    assert 'background' in result.stdout


@pytest.mark.parametrize('command,allowed', [('git status', True), ('echo "a & b"', True),
                                            ('git status &', False), ("sh -c 'git status &'", False),
                                            ('touch forbidden', False)])
def test_false_metadata_still_uses_command_restrictions(command, allowed):
    result = run_hook({'tool_name': 'Bash', 'tool_input':
                       {'command': command, 'run_in_background': False}})
    assert result.returncode == (0 if allowed else 2)


def test_readonly_role_configuration_preserves_guarded_bash_boundary():
    import re
    root = Path(__file__).resolve().parents[4]
    for role in ('architect', 'architect-fallback', 'designer', 'designer-fallback', 'scout'):
        text = (root / 'claude/agents' / ('al-' + role + '.md')).read_text()
        tools = re.search(r'^tools: (.+)$', text, re.M).group(1)
        assert 'background tool execution' in text and 'database proxies' in text
        if 'Bash' in tools:
            assert role in {'architect', 'architect-fallback'}
            assert 'matcher: "Bash"' in text and 'al_readonly_guard.py' in text
        else:
            assert role in {'designer', 'designer-fallback', 'scout'}
        codex = (root / 'codex/agents' / ('al-' + role + '.toml')).read_text()
        assert 'sandbox_mode = "read-only"' in codex
        assert 'background tool execution' in codex
