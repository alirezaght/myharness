"""The actual native Agent guard closes E49/E50/E52 before any transient effect."""

import json
import os
import subprocess
import sys
import threading
import uuid

import pytest

from harness.claude_agent_guard import required_hooks
from harness.execution_bootstrap import authority_snapshot
from harness.execution_principals import PrincipalRegistry
from tests.native_model_fixture import anthropic_fixture, native_executable

FORBIDDEN = [
    {"isolation": "worktree"},
    {"isolation": "remote"},
    {"model": None},
    {"effort": False},
    {"agent_type": "al-scout"},
    {"resume": "foreign-agent"},
    {"run_in_background": False},
    {"unknown": ""},
    {"subagent_type": "general-purpose"},
]


pytestmark = pytest.mark.native


def native_guard_case(sample_repo, tmp_path, *, extra=None, fault=None, worktree_backup=False):
    before = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=sample_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    refs_before = subprocess.run(
        ["git", "show-ref"], cwd=sample_repo, capture_output=True, text=True, check=True
    ).stdout
    session = str(uuid.uuid4())
    registry = PrincipalRegistry(
        tmp_path / "principals", tmp_path / "unused", {"execution": "fixture"}, "policy"
    )
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps(
            {
                "session_id": session,
                "registry": str(registry.directory),
                "policy_digest": "policy",
                "channels": {},
            }
        )
    )
    trusted = authority_snapshot(tmp_path / "protected")
    program = (
        "import sys,runpy;sys.path.insert(0,"
        + repr(trusted)
        + ");runpy.run_module('harness.claude_identity_hook',run_name='__main__')"
    )
    hooks = required_hooks(sys.executable, ["-I", "-B", "-c", program, "--context", str(context)])
    target = (
        hooks["WorktreeCreate"][0]["hooks"][0]
        if worktree_backup
        else hooks["PreToolUse"][0]["hooks"][0]
    )
    if worktree_backup:
        hooks["PreToolUse"] = hooks["PreToolUse"][1:]
    fault_programs = {
        "exit1": "raise SystemExit(1)",
        "signal": "import os,signal;os.kill(os.getpid(),signal.SIGTERM)",
        "timeout": "import time;time.sleep(3)",
        "invalid-json": "print('{')",
        "invalid-schema": "print('{\"hookSpecificOutput\":[]}')",
        "empty-path": "pass",
    }
    if fault == "missing":
        target.update(command="/nonexistent-harness-guard", args=[])
    elif fault:
        target.update(command=sys.executable, args=["-I", "-c", fault_programs[fault]], timeout=0.1)
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"disableAllHooks": False, "enabledPlugins": {}, "hooks": hooks})
    )
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"mcpServers":{}}')
    agents = tmp_path / "agents.json"
    agents.write_text(
        json.dumps(
            {
                "al-scout": {
                    "description": "AL Scout",
                    "prompt": "Return findings.",
                    "model": "haiku",
                    "tools": [],
                }
            }
        )
    )
    effects = []

    def plan(request, index):
        current = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=sample_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        refs = subprocess.run(
            ["git", "show-ref"], cwd=sample_repo, capture_output=True, text=True, check=True
        ).stdout
        effects.append(current != before or refs != refs_before)
        if index == 0:
            value = {
                "subagent_type": "al-scout",
                "description": "Guarded native probe",
                "prompt": "Return a short result. Do not edit files.",
            }
            value.update(extra or {})
            return ("Agent", value)
        return None

    with anthropic_fixture(plan) as (url, observed):
        argv = [
            native_executable("claude"),
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--restricted",
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp),
            "--settings",
            str(settings),
            "--agents",
            str(agents),
            "--tools",
            "Agent",
            "--allowedTools",
            "Agent(al-scout)",
            "--disable-slash-commands",
            "--session-id",
            session,
            "--model",
            "sonnet",
            "Native guarded lifecycle probe",
        ]
        proc = subprocess.Popen(
            argv,
            cwd=sample_repo,
            env=dict(
                os.environ,
                CLAUDE_CONFIG_DIR=str(tmp_path / "native"),
                ANTHROPIC_BASE_URL=url,
                ANTHROPIC_API_KEY="synthetic-native-fixture",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        output, observer_errors = [], []

        def observe():
            for line in proc.stdout:
                output.append(line)
                value = json.loads(line)
                if value.get("type") == "system" and value.get("subtype") == "init":
                    try:
                        registry.observe_claude_root(value["session_id"])
                    except ValueError as error:
                        observer_errors.append(str(error))

        thread = threading.Thread(target=observe, daemon=True)
        thread.start()
        proc.wait(timeout=30)
        thread.join(timeout=2)
        stderr = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        assert not observer_errors, observer_errors
        assert proc.returncode == 0, (output[-5:], stderr)
        assert observed, (output[-5:], stderr)
        after = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=sample_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        refs_after = subprocess.run(
            ["git", "show-ref"], cwd=sample_repo, capture_output=True, text=True, check=True
        ).stdout
        assert not any(effects) and before == after and refs_before == refs_after, (
            effects,
            output[-5:],
        )
        print(
            "actual native Agent input keys",
            [
                json.loads(p.read_bytes())
                for p in (registry.directory / "observations").glob("agent-input-*.json")
            ],
        )
        print(
            "actual native hook fault result",
            " ".join(str(v) for v in observed[-1].get("messages", []) if isinstance(v, dict))[
                -1800:
            ],
        )
        children = list(registry.directory.glob("claude-agent-*.json"))
        # Native schema parsing drops unknown keys before hooks. These ignored
        # fields cannot select another agent, resume a session, or change scope.
        ignored = extra and set(extra) <= {"agent_type", "resume", "unknown"}
        if ignored:
            inputs = [
                json.loads(p.read_bytes())["keys"]
                for p in (registry.directory / "observations").glob("agent-input-*.json")
            ]
            assert inputs and all(not set(extra) & set(keys) for keys in inputs)
            assert len(children) == 1 and json.loads(children[0].read_bytes())["role"] == "al-scout"
        else:
            assert not children, "forbidden/faulted Agent spawned a child"
            assert len(observed) == 2, "forbidden/faulted Agent executed model work"
        return "".join(output)


@pytest.mark.parametrize("extra", FORBIDDEN)
def test_restricted_claude_agent_forbidden_fields_have_no_effect(sample_repo, tmp_path, extra):
    native_guard_case(sample_repo, tmp_path, extra=extra)


@pytest.mark.parametrize(
    "fault", ["missing", "exit1", "signal", "timeout", "invalid-json", "invalid-schema"]
)
def test_native_agent_hook_fault_is_fail_closed(sample_repo, tmp_path, fault):
    # Malformed successful hook output is ignored by this native version. The
    # independent worktree replacement guard must still stop filesystem effects;
    # production admission also fingerprints the fixed hook and profile files.
    extra = {"isolation": "worktree"} if fault == "invalid-json" else None
    native_guard_case(sample_repo, tmp_path, fault=fault, extra=extra)


@pytest.mark.parametrize("fault", [None, "missing", "exit1", "timeout", "empty-path"])
def test_worktree_replacement_hook_never_falls_back(sample_repo, tmp_path, fault):
    native_guard_case(
        sample_repo, tmp_path, extra={"isolation": "worktree"}, fault=fault, worktree_backup=True
    )
