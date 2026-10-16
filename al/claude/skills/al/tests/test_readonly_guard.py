"""Exercise the real hook boundary, including malformed and allowed input."""
import json
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "al_readonly_guard.py"


def run_hook(value):
    raw = value if isinstance(value, str) else json.dumps(value)
    return subprocess.run([sys.executable, str(GUARD)], input=raw, text=True,
                          capture_output=True, check=False)


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
