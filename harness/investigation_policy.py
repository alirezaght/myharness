"""Closed acquisition authority; generated schemas alone never qualify native access."""

import hashlib
import json
import os
import re
import stat
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from harness.authoring_contract import canonical, digest, exact
from harness.authoring_policy import feature_values, manifest_version

ENDPOINT = "https://mcp.atlassian.com/v2/mcp"
TOOLS = ("getAccessibleAtlassianResources", "getJiraIssue")
FIELDS = ("summary", "description", "status", "updated")
EFFECTIVE_GROUPS = (
    "sandbox_mode",
    "approval_policy",
    "agents",
    "web_search",
    "features",
    "mcp_servers",
)
REMOTE_CONTROL_ENV = "CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED"
REASONS = {
    "unqualified_transport": (
        "Jira acquisition is not qualified on this host. Manual editing remains available."
    ),
    "authentication_required": (
        "The native Jira connection requires authentication. No "
        "automatic login or retry was performed."
    ),
    "reconnect_unavailable": (
        "Reconnect is unavailable until native same-account and same-scope behavior is qualified."
    ),
    "site_selection_required": "Select a verified Jira site before loading this issue.",
    "scope_denied": "The requested issue or site is outside the configured acquisition scope.",
    "issue_unavailable": "The selected Jira issue could not be read.",
    "schema_changed": "The qualified native interface or tool schema changed.",
    "source_changed": "Source content changed during investigation; refresh explicitly.",
    "output_limit": "Investigation exceeded its bounded output or repository budget.",
    "timeout": "Investigation timed out. Its owned process was stopped.",
    "cancelled": "Investigation was cancelled.",
    "cleanup_unknown": (
        "Native process settlement is unknown; explicit acknowledgement is required before retry."
    ),
    "protocol_error": "The native acquisition protocol returned an invalid response.",
    "model_unavailable": "Sources were acquired, but the selected interview model is unavailable.",
}
RESTRICTIONS = {
    "sandbox_mode": "read-only",
    "approval_policy": "never",
    "agents.enabled": False,
    "web_search": "disabled",
    "features.apps": False,
    "features.plugins": False,
    "features.shell_tool": False,
    "features.skip_host_skill_discovery": True,
    "features.multi_agent": False,
    "features.hooks": False,
    "features.daemon_auto_start": False,
}


# Reuse canonical version data, not the author's capability or execution policy.

RESTRICTIONS.update(
    {"features." + name: value for name, value in feature_values("0.170.0").items()}
)

RESTRICTIONS.update(
    {
        "features." + name: False
        for name in (
            "api_key_cyber_access_programs",
            "api_key_model_discovery",
            "background_paginated_rollout_migration",
            "daybreak_program_propagation",
            "mentions_v2",
            "remote_control",
            "windows_sandbox_service",
        )
    }
)


class AcquisitionError(ValueError):
    def __init__(self, reason, *, settlement="settled"):
        self.reason = reason if reason in REASONS else "protocol_error"
        self.settlement = settlement
        super().__init__(REASONS[self.reason])

    def public(self):
        return {"reason": self.reason, "message": str(self), "settlement": self.settlement}


@dataclass(frozen=True)
class AcquisitionCapability:
    executable: dict
    config: dict
    tools: dict
    arguments: dict
    sites: tuple
    bootstrap_qualified: bool = False
    read_qualified: bool = False
    reconnect_qualified: bool = False
    scopes: tuple = ()
    bootstrap: dict | None = None
    effective_config_digests: dict | None = None

    @property
    def identity(self):
        return digest(asdict(self))


BOOTSTRAP_ENV = (
    "CODEX_EXEC_SERVER_URL",
    "CODEX_EXEC_SERVER_NOISE_REGISTRY_URL",
    "CODEX_EXEC_SERVER_NOISE_ENVIRONMENT_ID",
    "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
    "CODEX_EXEC_SERVER_NOISE_CHATGPT_ACCOUNT_ID",
)


def bootstrap_guard(value):
    proof = value.bootstrap
    home = Path.home() / ".codex"
    if (
        not isinstance(proof, dict)
        or manifest_version(proof.get("native_version")) != "0.170.0"
        or proof.get("child_environment") != {REMOTE_CONTROL_ENV: "1"}
        or proof.get("native_home") != str(home)
        or "CODEX_HOME" in os.environ
        or any(key in os.environ for key in BOOTSTRAP_ENV)
        or (home / "environments.toml").exists()
        or (home / "environments.toml").is_symlink()
    ):
        raise AcquisitionError("unqualified_transport")
    inputs = proof.get("config_inputs")
    if (
        not isinstance(inputs, list)
        or len(inputs) != 1
        or inputs[0].get("path") != str(home / "config.toml")
    ):
        raise AcquisitionError("unqualified_transport")
    path = home / "config.toml"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144:
        raise AcquisitionError("unqualified_transport")
    if hashlib.sha256(path.read_bytes()).hexdigest() != inputs[0].get("sha256"):
        raise AcquisitionError("schema_changed")


def capability():
    # A host-owned qualification receipt is installed only after independent verification.
    # Reading it never starts native processes, performs login or mutates configuration.
    root = Path(os.environ.get("HARNESS_HOME", str(Path.home() / ".local/share/harness")))
    path = root / "investigation/qualification.json"
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > 262144
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
        ):
            return None
        receipt = json.loads(path.read_text())
        exact(receipt, {"schema_version", "capability", "evidence"})
        if (
            receipt["schema_version"] != 2
            or not {"bootstrap", "rpc", "scope"}
            <= set(receipt["evidence"])
            <= {"bootstrap", "rpc", "scope", "tools"}
            or any(
                not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
                for v in receipt["evidence"].values()
            )
        ):
            return None
        data = dict(receipt["capability"])
        if data.get("read_qualified") and "tools" not in receipt["evidence"]:
            return None
        data["sites"], data["scopes"] = tuple(data["sites"]), tuple(data["scopes"])
        value = AcquisitionCapability(**data)
        bootstrap_guard(value)
        return validate_capability(value, observe=True, purpose="bootstrap")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def parse_issue(request):
    if not isinstance(request, str) or len(request.encode()) > 16000:
        raise AcquisitionError("scope_denied")
    if request.strip().startswith(("https://", "http://")):
        url = urlsplit(request.strip())
        if (
            url.scheme != "https"
            or url.username
            or url.password
            or url.port
            or url.query
            or url.fragment
            or not re.fullmatch(r"/browse/[A-Za-z][A-Za-z0-9]{1,9}-[1-9][0-9]*", url.path)
        ):
            raise AcquisitionError("scope_denied")
        return {"issue": url.path.rsplit("/", 1)[-1].upper(), "site_url": f"https://{url.netloc}"}
    keys = set(re.findall(r"(?<![\w-])([A-Za-z][A-Za-z0-9]{1,9}-[1-9][0-9]*)(?![\w-])", request))
    keys = {key.upper() for key in keys}
    if len(keys) > 1:
        raise AcquisitionError("scope_denied")
    return {"issue": next(iter(keys)), "site_url": None} if keys else None


def validate_launch_config(config):
    if not isinstance(config, dict) or set(config) != set(EFFECTIVE_GROUPS):
        raise AcquisitionError("scope_denied")
    expected = {}
    for path, value in RESTRICTIONS.items():
        target = expected
        parts = path.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    if any(canonical(config[key]) != canonical(value) for key, value in expected.items()):
        raise AcquisitionError("scope_denied")
    servers = config["mcp_servers"]
    if not isinstance(servers, dict) or "atlassian" not in servers or len(servers) > 100:
        raise AcquisitionError("scope_denied")
    for name, row in servers.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
            raise AcquisitionError("scope_denied")
        if name == "atlassian":
            safe = {
                "enabled": True,
                "url": ENDPOINT,
                "enabled_tools": list(TOOLS),
                "disabled_tools": [],
            }
            if canonical(row) != canonical(safe):
                raise AcquisitionError("scope_denied")
        elif canonical(row) not in (
            canonical({"enabled": False, "command": "/usr/bin/false"}),
            canonical({"enabled": False, "url": "https://invalid.example/mcp"}),
        ):
            raise AcquisitionError("scope_denied")


def effective_config_digests(config):
    # Only whole-group fingerprints leave the private effective-config observation.
    if not isinstance(config, dict) or any(key not in config for key in EFFECTIVE_GROUPS):
        raise AcquisitionError("schema_changed")
    decode_message(canonical({key: config[key] for key in EFFECTIVE_GROUPS}))
    return {"schema_version": 1, "groups": {key: digest(config[key]) for key in EFFECTIVE_GROUPS}}


def validate_effective_config(config, qualified):
    if not isinstance(config, dict) or not qualified:
        raise AcquisitionError("unqualified_transport")
    for path, expected in RESTRICTIONS.items():
        value = config
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value != expected or type(value) is not type(expected):
            raise AcquisitionError("scope_denied")
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict) or "atlassian" not in servers:
        raise AcquisitionError("scope_denied")
    for name, row in servers.items():
        if not isinstance(row, dict) or type(row.get("enabled")) is not bool:
            raise AcquisitionError("scope_denied")
        if name != "atlassian":
            if row["enabled"] or not (
                (row.get("command") == "/usr/bin/false" and not row.get("url"))
                or (row.get("url") == "https://invalid.example/mcp" and not row.get("command"))
            ):
                raise AcquisitionError("scope_denied")
            continue
        if (
            row.get("enabled") is not True
            or row.get("url") != ENDPOINT
            or set(row.get("enabled_tools", [])) != set(TOOLS)
            or row.get("disabled_tools") != []
            or any(
                row.get(k)
                for k in (
                    "command",
                    "args",
                    "env",
                    "bearer_token_env_var",
                    "http_headers",
                    "env_http_headers",
                    "http_headers_helper",
                )
            )
        ):
            raise AcquisitionError("scope_denied")
    if effective_config_digests(config) != qualified.effective_config_digests:
        raise AcquisitionError("schema_changed")


def validate_capability(value, *, observe=False, purpose="read"):
    if (
        not value
        or not value.bootstrap_qualified
        or (purpose == "read" and not value.read_qualified)
        or (purpose == "reconnect" and not value.reconnect_qualified)
    ):
        raise AcquisitionError("unqualified_transport")
    if manifest_version(value.executable.get("version")) != "0.170.0":
        raise AcquisitionError("unqualified_transport")
    validate_launch_config(value.config)
    proof = value.effective_config_digests
    if (
        not isinstance(proof, dict)
        or set(proof) != {"schema_version", "groups"}
        or type(proof["schema_version"]) is not int
        or proof["schema_version"] != 1
        or not isinstance(proof["groups"], dict)
        or set(proof["groups"]) != set(EFFECTIVE_GROUPS)
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
            for v in proof["groups"].values()
        )
        or not isinstance(value.bootstrap, dict)
        or value.bootstrap.get("child_environment") != {REMOTE_CONTROL_ENV: "1"}
    ):
        raise AcquisitionError("unqualified_transport")
    if purpose == "read" or value.read_qualified:
        if set(value.tools) != set(TOOLS):
            raise AcquisitionError("schema_changed")
        exact(value.arguments, {"site", "issue", "fields"})
        schema = value.tools[TOOLS[1]].get("inputSchema", {})
        if (
            not isinstance(schema.get("properties"), dict)
            or set(value.arguments.values()) - set(schema["properties"])
            or set(schema.get("required", [])) - set(value.arguments.values())
            or value.tools[TOOLS[0]].get("inputSchema", {}).get("required", [])
        ):
            raise AcquisitionError("schema_changed")
    if observe:
        path = Path(value.executable["path"])
        before = path.stat()
        if before.st_size > 512 * 1024 * 1024:
            raise AcquisitionError("schema_changed")
        observed, count = hashlib.sha256(), 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                count += len(chunk)
                if count > 512 * 1024 * 1024:
                    raise AcquisitionError("schema_changed")
                observed.update(chunk)
        after = path.stat()
        if (
            count != before.st_size
            or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
            or observed.hexdigest() != value.executable["sha256"]
        ):
            raise AcquisitionError("schema_changed")
    return value


def authorize_request(service, selection=None, qualified=None, *, purpose="read"):
    qualified = validate_capability(
        qualified if qualified is not None else capability(), purpose=purpose
    )
    issue = parse_issue(service.controller.get_task(service.task_id).request)
    if not issue:
        raise AcquisitionError("scope_denied")
    choices = [s for s in qualified.sites if s.get("verified") is True or purpose == "reconnect"]
    if selection is not None:
        choices = [s for s in choices if s["id"] == selection]
    if issue["site_url"]:
        choices = [s for s in choices if s["url"] == issue["site_url"]]
    if len(choices) != 1 and not (purpose == "reconnect" and not choices and selection is None):
        raise AcquisitionError("site_selection_required" if selection is None else "scope_denied")
    site = choices[0] if len(choices) == 1 else None
    if purpose == "read" and site.get("scopes") != ["read:jira:agent-interface"]:
        raise AcquisitionError("scope_denied")
    return {
        "schema_version": 1,
        "task": service.task_id,
        "project": service.project,
        "repository": str(service.root),
        "issue": issue["issue"],
        "site": site,
        "policy": qualified.identity,
        "fields": list(FIELDS),
        "reconnect_available": qualified.reconnect_qualified,
    }


def launch_argv(qualified):
    validate_capability(qualified, observe=True, purpose="bootstrap")
    bootstrap_guard(qualified)

    # JSON is valid TOML only for scalar values; tables are rendered recursively.
    def toml(value):
        if isinstance(value, dict):
            return "{" + ",".join(canonical(k) + "=" + toml(v) for k, v in value.items()) + "}"
        if isinstance(value, list):
            return "[" + ",".join(toml(v) for v in value) + "]"
        return canonical(value)

    argv = [qualified.executable["path"], "app-server", "--listen", "stdio://", "--strict-config"]
    for key, value in qualified.config.items():
        argv += ["-c", key + "=" + toml(value)]
    return argv


def decode_message(raw):
    from harness.authoring_contract import decode

    value = decode(raw)
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > 10000:
            raise AcquisitionError("output_limit")
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
    return value


def validate_request(request, qualified, *, reconnect=False):
    exact(
        request,
        {
            "schema_version",
            "task",
            "project",
            "repository",
            "issue",
            "site",
            "policy",
            "fields",
            "reconnect_available",
        },
    )
    if (
        request["schema_version"] != 1
        or request["fields"] != list(FIELDS)
        or not isinstance(request["issue"], str)
        or not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-[1-9][0-9]*", request["issue"])
        or request["policy"] != qualified.identity
        or request["reconnect_available"] is not qualified.reconnect_qualified
        or not isinstance(request["task"], str)
        or not 1 <= len(request["task"]) <= 100
        or request["project"] != digest({"repository": str(Path(request["repository"]).resolve())})
        or (request["site"] not in qualified.sites and not (reconnect and request["site"] is None))
    ):
        raise AcquisitionError("scope_denied")


class TransientHandoffs:
    def __init__(self):
        self.lock, self.values = threading.Lock(), {}

    def publish(self, claim, value):
        with self.lock:
            self.values = {k: v for k, v in self.values.items() if v["expires"] > time.monotonic()}
            if len(self.values) >= 8:
                raise AcquisitionError("output_limit")
            self.values[(claim["id"], claim["attempt"], claim["claim"], claim["owner"])] = dict(
                value, expires=time.monotonic() + 120
            )

    def get(self, claim):
        with self.lock:
            value = self.values.get((claim["id"], claim["attempt"], claim["claim"], claim["owner"]))
            if not value or value["expires"] <= time.monotonic() or claim["state"] != "running":
                return None
            return {k: v for k, v in value.items() if k != "expires"}

    def discard(self, claim):
        with self.lock:
            self.values.pop((claim["id"], claim["attempt"], claim["claim"], claim["owner"]), None)


HANDOFFS = TransientHandoffs()
