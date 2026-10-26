"""Restricted native clients retain their own sign-in; tools use guarded operations."""

import json
import os
import secrets
import sys
import tomllib
from contextlib import ExitStack
from pathlib import Path

from harness.execution_bootstrap import authority_snapshot
from harness.execution_contract import canonical, digest
from harness.execution_mcp import serve_mcp
from harness.execution_operations import OperationController
from harness.execution_principals import PrincipalRegistry
from harness.native_tool_policy import (
    ROLES,
    build_claude_policy,
    build_native_policy,
    executable_identity,
    verify_native_policy,
)


def native_auth_environment():
    """Do not relocate HOME or override the provider's auth selection or credentials."""
    env = dict(os.environ)
    # These select the trusted Harness bridge, not a provider credential backend.
    for key in ("PYTHONPATH", "PYTHONHOME", "HARNESS_AUTHORITY_ROOT", "AL_RUNTIME_DIR"):
        env.pop(key, None)
    return env


def auth_selection():
    root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    path = root / "config.toml"
    if not path.is_file():
        return {}
    value = tomllib.loads(path.read_text())
    # Custom provider definitions need separate verified profiles; never substitute a provider.
    if value.get("model_provider", "openai") != "openai":
        raise ValueError("custom Codex provider requires a verified restricted native profile")
    return {
        k: value[k]
        for k in (
            "forced_login_method",
            "forced_chatgpt_workspace_id",
            "cli_auth_credentials_store",
        )
        if k in value
    }


class NativeRuntime:
    def __init__(self, database, execution, envelope, package, initialization, native_id):
        self.stack = ExitStack()
        self.operations = None
        self.host, self.native_id = execution["host"], native_id
        directory = Path(execution["handoff_path"]).parent
        trusted = authority_snapshot(directory)
        executable = executable_identity(package["executable"], self.host)
        stable_policy = digest(
            {
                "version": 1,
                "executable": executable,
                "pin": initialization["pin_root"],
                "authority": json.loads((Path(trusted) / "manifest.json").read_bytes())[
                    "fingerprint"
                ],
            }
        )
        native_root = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        self.registry = PrincipalRegistry(
            directory / "principals", native_root, execution["binding"], stable_policy
        )
        profile = directory / "native-profiles" / secrets.token_hex(16)
        profile.mkdir(mode=0o700, parents=True)
        self.operations = OperationController(
            database,
            execution,
            envelope,
            package,
            initialization,
            self.registry,
            directory / "operations",
        )
        profile_package = dict(
            package,
            package=str(Path(initialization["pin_root"]) / "payload" / self.host / "skills/al"),
        )
        try:
            if self.host == "codex":
                mcp = self.stack.enter_context(
                    serve_mcp(self.registry.resolve, self.operations.dispatch)
                )
                self.policy = build_native_policy(
                    execution,
                    profile_package,
                    auth_selection(),
                    mcp_url=mcp["url"],
                    token=mcp["token"],
                    directory=profile,
                )
            else:
                context = {
                    "execution_id": execution["id"],
                    "session_id": native_id,
                    "epoch": secrets.token_hex(16),
                    "policy_digest": stable_policy,
                    "signing_key": secrets.token_hex(32),
                    "registry": str(self.registry.directory),
                    "channels": {"harness": {"role": "orchestrator", "source": "dynamic"}}
                    | {
                        "harness_" + r.replace("-", "_"): {"role": r, "source": "agent"}
                        for r in ROLES
                    },
                }
                context_path = profile / "context.json"
                context_path.write_text(canonical(context))
                context_path.chmod(0o600)

                def verifier(channel, name, arguments):
                    return self.registry.resolve_claude(channel, name, arguments, context)

                channels = {}
                for role in ("orchestrator", *ROLES):
                    channel = (
                        "harness" if role == "orchestrator" else "harness_" + role.replace("-", "_")
                    )
                    channels[role] = self.stack.enter_context(
                        serve_mcp(
                            None,
                            self.operations.dispatch,
                            verifier=verifier,
                            claim=self.registry.claim_attestation,
                            channel=channel,
                        )
                    )
                program = (
                    "import sys,runpy;sys.path.insert(0,"
                    + repr(trusted)
                    + ");runpy.run_module('harness.claude_identity_hook',run_name='__main__')"
                )
                hook = [sys.executable, "-I", "-B", "-c", program, "--context", str(context_path)]
                self.policy = build_claude_policy(
                    profile_package, profile, channels, hook, native_id
                )
            self.policy["files"] = {
                str(p): digest(p.read_bytes()) for p in profile.rglob("*") if p.is_file()
            }
            verify_native_policy(self.policy)
        except BaseException:
            self.close()
            raise

    def argv(self, repository, *, resume=False):
        verify_native_policy(self.policy)
        binary = self.policy["executable"]["path"]
        options = self.policy["options"]
        if self.host == "codex":
            return [
                binary,
                "exec",
                *options,
                "--ignore-user-config",
                "--ignore-rules",
                "--strict-config",
                "--json",
                "--cd",
                str(repository),
                *(["resume", self.native_id] if resume else []),
                "-",
            ]
        return [
            binary,
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            *options,
            "--resume" if resume else "--session-id",
            self.native_id,
        ]

    def observe(self, native_id):
        if self.host == "claude":
            self.registry.observe_claude_root(native_id)
        else:
            self.registry.observe_root_start(native_id)
        self.native_id = native_id

    def tick(self):
        verify_native_policy(self.policy)
        self.operations.tick()

    def close(self):
        self.stack.close()
        if self.operations:
            self.operations.close()
