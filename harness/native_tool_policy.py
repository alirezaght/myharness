"""Version-bound native tool profiles; native authentication remains untouched."""

import gzip
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

from harness.execution_contract import canonical, digest

TOOLS = (
    "read_file",
    "apply_patch",
    "run_command",
    "poll_process",
    "cancel_process",
    "al_command",
    "diagnostic",
    "delivery_operation",
    "environment_operation",
    "diagram_operation",
    "write_artifact",
    "await_assignment",
    "submit_output",
)
ROLES = (
    "al-architect",
    "al-architect-fallback",
    "al-designer",
    "al-designer-fallback",
    "al-engineer",
    "al-builder",
    "al-scout",
)
DISABLED = (
    "shell_tool shell_snapshot shell_snapshot_v2 unified_exec apply_patch_freeform "
    "code_mode code_mode_only code_mode_host js_repl js_repl_tools_only apps connectors "
    "plugins remote_plugin recommended_plugins tool_suggest hooks codex_hooks plugin_hooks "
    "browser_use browser_use_external computer_use in_app_browser image_generation imagegenext "
    "view_image memory_tool memories skill_search skill_mcp_dependency_install "
    "standalone_web_search "
    "web_search web_search_cached web_search_request search_tool request_permissions_tool "
    "remote_models responses_websockets responses_websockets_v2 daemon_auto_start "
    "guardian_ext guardianv2"
).split()


def executable_identity(executable, host):
    path = Path(executable).resolve(strict=True)
    if host == "codex" and path.suffix == ".js":
        import platform

        triples = {
            ("Darwin", "arm64"): ("darwin-arm64", "aarch64-apple-darwin"),
            ("Darwin", "x86_64"): ("darwin-x64", "x86_64-apple-darwin"),
            ("Linux", "x86_64"): ("linux-x64", "x86_64-unknown-linux-musl"),
            ("Linux", "aarch64"): ("linux-arm64", "aarch64-unknown-linux-musl"),
        }
        package, triple = triples[(platform.system(), platform.machine())]
        path = (
            path.parent.parent
            / "node_modules/@openai"
            / ("codex-" + package)
            / "vendor"
            / triple
            / "bin/codex"
        ).resolve(strict=True)
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10)
    version = {"codex": "0.164.0", "claude": "2.1.310"}[host]
    if result.returncode or version not in result.stdout:
        raise ValueError("unsupported native executable version: " + host)
    return {
        "path": str(path),
        "version": version,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def restricted_catalog():
    value = json.loads(
        gzip.decompress(Path(__file__).with_name("native_catalog_0_164_0.json.gz").read_bytes())
    )
    for model in value["models"]:
        model.update(
            shell_type="disabled",
            apply_patch_tool_type=None,
            tool_mode="direct",
            experimental_supported_tools=[],
            node_repl_disabled=True,
            supports_search_tool=False,
        )
    return value


def build_native_policy(execution, package, auth_selection, *, mcp_url, token, directory):
    host = execution["host"]
    identity = executable_identity(package["executable"], host)
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if set(auth_selection) - {
        "forced_login_method",
        "forced_chatgpt_workspace_id",
        "model_provider",
        "cli_auth_credentials_store",
    }:
        raise ValueError("unsupported native auth-selection constraints")
    options = []
    if host == "codex":
        catalog = directory / "models.json"
        catalog.write_text(canonical(restricted_catalog()))
        config = {
            "model_catalog_json": str(catalog),
            "web_search": "disabled",
            "approval_policy": "never",
            "sandbox_mode": "read-only",
            "mcp_servers": {
                "harness": {
                    "url": mcp_url,
                    "http_headers": {"Authorization": "Bearer " + token},
                    "enabled_tools": list(TOOLS),
                    "default_tools_approval_mode": "approve",
                    "required": True,
                }
            },
            **auth_selection,
        }
        for key, value in config.items():
            options.extend(["-c", key + "=" + _toml(value)])
        for feature in DISABLED:
            options.extend(["-c", "features." + feature + "=false"])
        options.extend(["-c", "agents.enabled=true", "-c", "features.multi_agent_v2=true"])
        for role in ROLES:
            value = tomllib.loads(
                (Path(package["package"]).parents[1] / "agents" / (role + ".toml")).read_text()
            )
            sanitized = {
                "name": role,
                "description": role,
                **{
                    k: value[k]
                    for k in ("model", "model_reasoning_effort", "developer_instructions")
                    if k in value
                },
            }
            role_path = directory / (role + ".toml")
            role_path.write_text("\n".join(k + "=" + _toml(v) for k, v in sanitized.items()))
            options.extend(
                [
                    "-c",
                    "agents." + role + ".config_file=" + _toml(str(role_path)),
                    "-c",
                    "agents." + role + ".description=" + _toml(role),
                ]
            )
    else:
        raise ValueError("Claude restricted role principal profile not yet verified")
    policy = {
        "host": host,
        "executable": identity,
        "options": options,
        "directory": str(directory),
        "allowed_tools": list(TOOLS),
    }
    policy["digest"] = digest(policy)
    path = directory / "policy.json"
    path.write_text(canonical(policy))
    return policy


def _toml(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + _toml(v) for k, v in value.items()) + "}"
    return json.dumps(value)


def build_claude_policy(package, directory, channels, hook, session_id):
    """Explicit settings and inline roles leave native auth storage in its normal location."""
    from harness.claude_agent_guard import required_hooks

    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    roles = {}
    for role in ROLES:
        source = Path(package["package"]).parents[1] / "agents" / (role + ".md")
        text = source.read_text()
        _, front, prompt = text.split("---", 2)
        metadata = dict(line.split(":", 1) for line in front.splitlines() if ":" in line)
        server = "harness_" + role.replace("-", "_")
        tools = ["mcp__" + server + "__" + name for name in TOOLS]
        roles[role] = {
            "description": metadata["description"].strip().strip('"'),
            "prompt": prompt + "\nUse only Harness controller tools. First call await_assignment. "
            "Native identity is supplied by the runtime. Never self-assign another task.",
            "model": metadata["model"].strip(),
            "effort": metadata["effort"].strip(),
            "tools": tools,
            "disallowedTools": ["Agent", "Task", "Bash", "Read", "Write", "Edit", "WebFetch"],
            "mcpServers": [
                {
                    server: {
                        "type": "http",
                        "url": channels[role]["url"],
                        "headers": {"Authorization": "Bearer " + channels[role]["token"]},
                    }
                }
            ],
        }
    root = channels["orchestrator"]
    files = {
        "settings.json": {
            "disableAllHooks": False,
            "enabledPlugins": {},
            "hooks": required_hooks(hook[0], hook[1:]),
        },
        "agents.json": roles,
        "mcp.json": {
            "mcpServers": {
                "harness": {
                    "type": "http",
                    "url": root["url"],
                    "headers": {"Authorization": "Bearer " + root["token"]},
                }
            }
        },
    }
    for name, value in files.items():
        path = directory / name
        path.write_text(canonical(value))
        path.chmod(0o600)
    return {
        "host": "claude",
        "executable": executable_identity(package["executable"], "claude"),
        "options": [
            "--restricted",
            "--strict-mcp-config",
            "--mcp-config",
            str(directory / "mcp.json"),
            "--settings",
            str(directory / "settings.json"),
            "--agents",
            str(directory / "agents.json"),
            "--tools",
            "Agent,SendMessage,TaskStop",
            "--allowedTools",
            "Agent,SendMessage,TaskStop," + ",".join("mcp__harness__" + n for n in TOOLS),
            "--disable-slash-commands",
        ],
        "session_id": session_id,
    }


def verify_native_policy(policy):
    expected = policy["executable"]
    if executable_identity(expected["path"], policy["host"]) != expected:
        raise ValueError("native executable changed since admission")
    for name, sha in policy.get("files", {}).items():
        path = Path(name)
        if path.is_symlink() or digest(path.read_bytes()) != sha:
            raise ValueError("restricted native profile changed since admission")
