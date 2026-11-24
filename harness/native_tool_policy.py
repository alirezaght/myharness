"""Version-bound native tool profiles; native authentication remains untouched."""

import gzip
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path

from harness.execution_contract import canonical, digest

TOOLS = (
    "read_file",
    "knowledge_query",
    "knowledge_explain",
    "knowledge_consumers",
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
# Tested native CLI release lines as [lowest tested, exclusive bound). Native CLIs
# auto-update patch releases constantly; a patch inside a tested line keeps that
# line's launch restrictions, which are still verified when the profile is built.
SUPPORTED_NATIVE_RANGES = {
    "codex": (("0.164.0", "0.166.0"), ("0.170.0", "0.171.0")),
    "claude": (("2.1.310", "2.2.0"),),
}
UNVERIFIED_VERSION_FLAG = "--accept-unverified-host-version"
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


_ASSIGNMENT_HANDOFF_INSTRUCTIONS = (
    "\nWhen Harness tools are available, first call await_assignment. "
    "For a writable assignment, read every page of its authenticated handoff.resource "
    "with read_file before work. Use the returned resource and next_offset until truncated "
    "is false; retain the complete managed evidence block. A changed binding requires a new "
    "await_assignment. An unavailable handoff grants no new authority.\n"
)


def _observe_executable_identity(executable, host):
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
    pattern = (r"codex-cli " if host == "codex" else "") + r"(\d+\.\d+\.\d+)(?: \([^()\r\n]+\))?"
    match = re.fullmatch(pattern, result.stdout.strip())
    if result.returncode or match is None:
        raise ValueError(
            "unsupported native executable version: " + host + " " + result.stdout.strip()
        )
    version = match[1]
    return {
        "path": str(path),
        "version": version,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def version_key(version):
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("invalid native version: " + repr(version))
    return tuple(int(part) for part in version.split("."))


def supported_range(host):
    return " or ".join(">=" + low + ",<" + high for low, high in SUPPORTED_NATIVE_RANGES[host])


def version_supported(host, version):
    try:
        value = version_key(version)
    except ValueError:
        return False
    return any(
        version_key(low) <= value < version_key(high)
        for low, high in SUPPORTED_NATIVE_RANGES.get(host, ())
    )


def unverified_record(host, identity):
    return {
        "status": "UNVERIFIED",
        "version": identity["version"],
        "sha256": identity["sha256"],
        "supported": supported_range(host),
        "accepted_by": "explicit developer flag " + UNVERIFIED_VERSION_FLAG,
    }


OVERRIDE_FILE = "host-version-override.json"


def recorded_override(directory):
    """The developer override recorded beside an execution handoff, or False."""
    path = Path(directory) / OVERRIDE_FILE
    if path.is_symlink() or not path.is_file():
        return False
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("status") != "UNVERIFIED":
        raise ValueError("recorded native host version override is malformed")
    return value


def record_override(directory, record):
    # Each explicit developer decision replaces the previous one for this execution;
    # the record binds one exact version and binary digest.
    path = Path(directory) / OVERRIDE_FILE
    path.write_text(canonical(record))
    path.chmod(0o600)
    return record


def executable_identity(executable, host, *, accept_unverified=False):
    """Admit a tested version line, or an explicitly recorded developer override.

    ``accept_unverified`` is ``True`` for a fresh developer override, or the recorded
    override record, which then admits only that exact version and binary.
    """
    identity = _observe_executable_identity(executable, host)
    if version_supported(host, identity["version"]):
        return identity
    record = unverified_record(host, identity)
    if accept_unverified is True or (
        isinstance(accept_unverified, dict) and accept_unverified == record
    ):
        return identity | {"qualification": record}
    raise ValueError(
        "unsupported native executable version: "
        + host
        + " "
        + identity["version"]
        + " (supported: "
        + supported_range(host)
        + "); install a supported version, or start with "
        + UNVERIFIED_VERSION_FLAG
        + " to record an explicit developer override in the execution evidence"
    )


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


def _execution_features(version):
    """Feature switches and config for the tested codex line at or below ``version``."""
    if version_key(version) < (0, 170, 0):
        return dict.fromkeys(DISABLED, False), {}
    from harness.authoring_policy import AUTHORING_CONFIG, feature_values

    # 0.170 adds tool-exposing features; reuse its qualified no-tool inventory and keep
    # only the multi-agent switches execution sets itself (v1 stays at its default).
    features = {
        key: value
        for key, value in feature_values("0.170.0").items()
        if key not in {"multi_agent", "multi_agent_v2"}
    }
    return features, AUTHORING_CONFIG["0.170.0"]


def build_native_policy(
    execution, package, auth_selection, *, mcp_url, token, directory, accept_unverified=False
):
    host = execution["host"]
    identity = executable_identity(package["executable"], host, accept_unverified=accept_unverified)
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
        features, extra = _execution_features(identity["version"])
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
            **extra,
        }
        for key, value in config.items():
            options.extend(["-c", key + "=" + _toml(value)])
        for feature, value in features.items():
            options.extend(["-c", "features." + feature + "=" + _toml(value)])
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
            sanitized["developer_instructions"] = (
                sanitized.get("developer_instructions", "") + _ASSIGNMENT_HANDOFF_INSTRUCTIONS
            )
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
        if version_key(identity["version"]) >= (0, 170, 0):
            from harness.authoring_policy import probe_feature_values

            # Offline launch probe: every restriction must take effect in this binary.
            probe_feature_values(
                identity["path"],
                options,
                {**features, "multi_agent_v2": True},
                label="execution",
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


def build_claude_policy(package, directory, channels, hook, session_id, *, accept_unverified=False):
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
            "Native identity is supplied by the runtime. Never self-assign another task."
            + _ASSIGNMENT_HANDOFF_INSTRUCTIONS,
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
            "hooks": required_hooks(hook[0], hook[1:], supervise_stop=True),
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
        "executable": executable_identity(
            package["executable"], "claude", accept_unverified=accept_unverified
        ),
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
            ",".join(
                [
                    "Agent",
                    "SendMessage",
                    "TaskStop",
                    *("mcp__harness__" + name for name in TOOLS),
                    *(name for profile in roles.values() for name in profile["tools"]),
                ]
            ),
            "--disable-slash-commands",
        ],
        "session_id": session_id,
    }


def verify_native_policy(policy):
    expected = policy["executable"]
    recorded = expected.get("qualification") or False
    if (
        executable_identity(expected["path"], policy["host"], accept_unverified=recorded)
        != expected
    ):
        raise ValueError("native executable changed since admission")
    for name, sha in policy.get("files", {}).items():
        path = Path(name)
        if path.is_symlink() or digest(path.read_bytes()) != sha:
            raise ValueError("restricted native profile changed since admission")
