"""Version-qualified no-tool native authoring; never starts an execution runtime."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from harness.authoring_contract import canonical, digest
from harness.native_runtime import auth_selection
from harness.native_tool_policy import (
    DISABLED,
    SUPPORTED_NATIVE_VERSIONS,
    _observe_executable_identity,
    _toml,
    restricted_catalog,
)

AUTHORING_DISABLED = (
    *DISABLED,
    "goals",
    "sleep_tool",
    "workspace_dependencies",
    "in_app_chat",
    "in_app_local_automation",
    "default_mode_request_user_input",
)
AUTHORING_FLAGS = ("--ignore-user-config", "--ignore-rules", "--strict-config")


# Canonical 0.165.0 inventory: aliases are replaced by their existing canonical
# switches; retired features cannot expose tools. Web search uses its config key.
_RETIRED_OR_ALIAS_165 = frozenset(
    {
        "codex_hooks",
        "connectors",
        "imagegenext",
        "memory_tool",
        "apply_patch_freeform",
        "js_repl",
        "js_repl_tools_only",
        "plugin_hooks",
        "web_search",
        "web_search_cached",
        "web_search_request",
        "search_tool",
        "remote_models",
        "responses_websockets",
        "responses_websockets_v2",
        "guardian_ext",
    }
)


AUTHORING_FEATURES = {
    "0.164.0": dict.fromkeys((*AUTHORING_DISABLED, "multi_agent", "multi_agent_v2"), False),
    "0.165.0": dict.fromkeys(
        (
            *[f for f in AUTHORING_DISABLED if f not in _RETIRED_OR_ALIAS_165],
            "multi_agent",
            "multi_agent_v2",
        ),
        False,
    ),
    "0.170.0": dict.fromkeys(
        (
            "agent_message_board agent_message_board_mcp runtime_agent_instances "
            "wake_on_agent_messages multi_agent multi_agent_v2 multi_agent_v2_dynamic_tools "
            "subagent_thread_environments use_agent_identity send_message_to_user_async "
            "shell_tool shell_snapshot shell_snapshot_v2 unified_exec unified_exec_tty "
            "code_mode code_mode_only code_mode_host code_mode_host_grpc "
            "code_mode_host_output_truncation code_mode_interrupt code_mode_only_strict_3p_tools "
            "code_mode_prewarm code_mode_tool_search "
            "apps apps_early_resource_discovery codex_apps_mcp_2026_07_28 enable_mcp_apps "
            "mcp_2026_07_28 mcp_connection_sharing mcp_lazy_stdio_startup mcp_tool_list_changed "
            "multi_environment_mcp non_prefixed_mcp_tool_names auth_elicitation "
            "tool_call_mcp_elicitation  "
            "incremental_exec_tools incremental_tools incremental_tools_legacy "
            "incremental_tools_legacy_gcp  "
            "deferred_tool_world_state executor_capability_discovery "
            "executor_capability_discovery_v2  "
            "plugins remote_plugin recommended_plugins plugin_sharing hooks tool_suggest "
            "browser_use browser_use_external browser_use_full_cdp_access browser_annotation_api "
            "computer_use in_app_browser in_app_chat in_app_local_automation in_app_dictation "
            "in_app_voice  "
            "image_generation image_generation_animation view_image artifact multi_env_copy_file "
            "memories external_agent_memory_import skill_search skill_mcp_dependency_install "
            "skill_catalog_deltas standalone_web_search hosted_search_policy "
            "request_permissions_tool  "
            "daemon_auto_start guardian_approval guardianv2 guardianv3 goals sleep_tool "
            "workspace_dependencies default_mode_request_user_input"
        ).split(),
        False,
    )
    # 0.170 normalizes this backend on; shell_tool and each model gate handlers separately.
    | {"skip_host_skill_discovery": True, "unified_exec": True},
}
AUTHORING_VERSIONS = {
    "codex": frozenset(AUTHORING_FEATURES),
    "claude": SUPPORTED_NATIVE_VERSIONS["claude"],
}
AUTHORING_CONFIG = {"0.170.0": {"suppress_unstable_features_warning": True}}
AUTH_KEYS = frozenset(
    {"forced_login_method", "forced_chatgpt_workspace_id", "cli_auth_credentials_store"}
)


def authoring_executable_identity(executable, host):
    identity = _observe_executable_identity(executable, host)
    if identity["version"] not in AUTHORING_VERSIONS[host]:
        raise ValueError("unsupported authoring executable version")
    return identity


def feature_values(version):
    if version not in AUTHORING_FEATURES:
        raise ValueError("unsupported authoring feature manifest")
    return AUTHORING_FEATURES[version]


def disabled_features(version):
    return tuple(k for k, value in feature_values(version).items() if value is False)


def profile_identity(profile):
    catalog_path = profile["catalog_path"]
    return digest(
        {
            "host": profile["host"],
            "executable": profile["executable"],
            "options": [
                option.replace(catalog_path, "<catalog:" + profile["catalog_hash"] + ">")
                for option in profile["options"]
            ],
            "flags": list(AUTHORING_FLAGS),
            "catalog_hash": profile["catalog_hash"],
            "allowed_tools": profile["allowed_tools"],
        }
    )


def verify_profile(profile, flags=AUTHORING_FLAGS):
    options = profile["options"]
    if len(options) % 2 or any(options[i] != "-c" for i in range(0, len(options), 2)):
        raise ValueError("Native authoring restrictions changed")
    values = [options[i + 1] for i in range(0, len(options), 2)]
    keys = [value.split("=", 1)[0] for value in values]
    required = {
        "agents.enabled": False,
        "mcp_servers": {},
        "web_search": "disabled",
        "approval_policy": "never",
        "sandbox_mode": "read-only",
        "model_catalog_json": profile["catalog_path"],
        **AUTHORING_CONFIG.get(profile["executable"]["version"], {}),
        **{
            "features." + key: value
            for key, value in feature_values(profile["executable"]["version"]).items()
        },
    }
    try:
        parsed = {
            key: tomllib.loads("value=" + raw.split("=", 1)[1])["value"]
            for key, raw in zip(keys, values, strict=True)
        }
    except (ValueError, IndexError, KeyError):
        raise ValueError("Native authoring restrictions changed") from None
    if (
        len(set(keys)) != len(keys)
        or set(parsed) - set(required) - AUTH_KEYS
        or any(
            parsed.get(key) != value or type(parsed.get(key)) is not type(value)
            for key, value in required.items()
        )
        or set(flags) != set(AUTHORING_FLAGS)
        or len(flags) != len(AUTHORING_FLAGS)
        or profile["allowed_tools"]
    ):
        raise ValueError("Native authoring restrictions changed")
    expected = digest(canonical(restricted_catalog()))
    catalogue_text = Path(profile["catalog_path"]).read_text()
    catalogue = json.loads(catalogue_text)
    models = catalogue.get("models") if isinstance(catalogue, dict) else None
    restrictions = {
        "shell_type": "disabled",
        "apply_patch_tool_type": None,
        "tool_mode": "direct",
        "experimental_supported_tools": [],
        "node_repl_disabled": True,
        "supports_search_tool": False,
    }
    if (
        profile["catalog_hash"] != expected
        or digest(catalogue_text) != expected
        or not isinstance(models, list)
        or not models
        or any(
            not isinstance(model, dict)
            or any(
                key not in model or model[key] != value or type(model[key]) is not type(value)
                for key, value in restrictions.items()
            )
            for model in models
        )
    ):
        raise ValueError("Native authoring no-tool catalogue changed")
    if profile["identity"] != profile_identity(profile):
        raise ValueError("Native authoring profile identity changed")


def build_profile(host, directory, *, executable=None):
    if host not in {"codex", "claude"}:
        raise ValueError("invalid host")
    binary = executable or shutil.which(host)
    if not binary:
        raise FileNotFoundError("native executable missing")
    if any(
        os.environ.get(key) for key in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "CODEX_API_BASE_URL")
    ):
        raise ValueError("unsupported authoring profile: custom provider environment")
    identity = authoring_executable_identity(binary, host)
    if host != "codex":
        raise ValueError("unsupported authoring profile: Claude no-tool restrictions not qualified")
    help_result = subprocess.run(
        [identity["path"], "exec", "--help"], capture_output=True, timeout=10
    )
    if help_result.returncode:
        raise ValueError("unsupported authoring profile: native help probe failed")
    for flag in (
        b"--ignore-user-config",
        b"--ignore-rules",
        b"--strict-config",
        b"--output-last-message",
    ):
        if flag not in help_result.stdout:
            raise ValueError("unsupported authoring profile: required native restriction missing")
    directory = Path(directory)
    catalog = directory / "models.json"
    catalog.write_text(canonical(restricted_catalog()))
    config = {
        "model_catalog_json": str(catalog),
        "web_search": "disabled",
        "approval_policy": "never",
        "sandbox_mode": "read-only",
        "mcp_servers": {},
        "agents.enabled": False,
        **auth_selection(),
        **AUTHORING_CONFIG.get(identity["version"], {}),
    }
    options = []
    for key, value in config.items():
        options += ["-c", key + "=" + _toml(value)]
    for feature, value in feature_values(identity["version"]).items():
        options += ["-c", "features." + feature + "=" + _toml(value)]
    profile = {
        "host": host,
        "executable": identity,
        "options": options,
        "catalog_path": str(catalog),
        "catalog_hash": digest(catalog.read_text()),
        "allowed_tools": [],
    }
    profile["identity"] = profile_identity(profile)
    verify_profile(profile)
    if identity["version"] == "0.170.0":
        _probe_features(profile)
    return profile


def _probe_features(profile):
    # This offline interface verifies overrides, not exec's ignore/strict flags.
    result = subprocess.run(
        [profile["executable"]["path"], *profile["options"], "features", "list"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError("unsupported authoring profile: native feature probe failed")
    observed = {}
    for line in result.stdout.decode("utf-8").splitlines():
        match = re.fullmatch(
            r"(\S+)\s+(under development|stable|experimental|deprecated|removed)\s+(true|false)",
            line,
        )
        if match:
            if match[1] in observed:
                raise ValueError("unsupported authoring profile: duplicate native feature")
            observed[match[1]] = (match[2], match[3] == "true")
    for key, value in feature_values(profile["executable"]["version"]).items():
        row = observed.get(key)
        if row is None or row[0] == "removed" or row[1] is not value:
            raise ValueError("unsupported authoring profile: native feature restriction missing")


def availability(host):
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="harness-authoring-probe-") as directory:
            profile = build_profile(host, directory)
        return {
            "host": host,
            "status": "profile_verified",
            "available": True,
            "authentication": "native; verified only when invocation succeeds",
            "version": profile["executable"]["version"],
            "executable_hash": profile["executable"]["sha256"],
            "profile_identity": profile["identity"],
        }
    except FileNotFoundError:
        return {
            "host": host,
            "status": "missing_executable",
            "available": False,
            "error": "Native executable not installed",
        }
    except (ValueError, OSError, subprocess.SubprocessError):
        return {
            "host": host,
            "status": "unsupported_profile",
            "available": False,
            "error": "Native version or no-tool profile is not supported",
        }
