"""Supervisor-owned native lineage registry; arguments never elect a root/role."""

import json
from pathlib import Path

from harness.execution_contract import canonical, digest
from harness.execution_handoff import durable_file
from harness.native_identity import identity_digest, read_native_identity

_READ_ONLY = {
    "al-scout",
    "al-architect",
    "al-architect-fallback",
    "al-designer",
    "al-designer-fallback",
}


class PrincipalRegistry:
    def __init__(self, directory, native_root, binding, policy):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.native_root = Path(native_root)
        self.binding, self.policy = binding, policy
        self.root = None
        path = self.directory / "root.json"
        if path.is_file():
            value = json.loads(path.read_bytes())
            if value["binding"] != binding or value["policy"] != policy:
                raise ValueError("native root registry approval/policy mismatch")
            self.root = value["thread_id"]

    def observe_root_start(self, thread_id):
        """Called solely by supervisor's machine-readable native stream observer."""
        if self.root is not None and self.root != thread_id:
            raise ValueError("native root conversation cannot change")
        value = {"thread_id": thread_id, "binding": self.binding, "policy": self.policy}
        durable_file(self.directory / "root.json", canonical(value).encode())
        self.root = thread_id

    def enroll(self, thread_id, ancestry=()):
        if not self.root:
            raise ValueError("IDENTITY_NOT_READY: supervised root start not observed")
        if thread_id in ancestry or len(ancestry) > 20:
            raise ValueError("native ancestry cycle/depth rejected")
        identity = read_native_identity(self.native_root, thread_id)
        if thread_id == self.root:
            if identity.parent_thread_id is not None:
                raise ValueError("supervised root has child provenance")
            writable, role = False, "orchestrator"
        else:
            if identity.parent_thread_id is None:
                raise ValueError("foreign native root has no supervised ancestry")
            parent = self.enroll(identity.parent_thread_id, (*ancestry, thread_id))
            role = identity.role
            writable = role not in _READ_ONLY and (
                parent["role"] == "orchestrator" or parent["writable"]
            )
        value = {
            "thread_id": thread_id,
            "session_id": identity.session_id,
            "parent_thread_id": identity.parent_thread_id,
            "role": role,
            "writable": writable,
            "root_thread_id": self.root,
            "binding": self.binding,
            "policy": self.policy,
            "metadata_digest": identity_digest(identity),
        }
        path = self.directory / (thread_id + ".json")
        durable_file(path, canonical(value).encode())
        return value

    def resolve(self, meta):
        if (
            not isinstance(meta, dict)
            or not isinstance(meta.get("threadId"), str)
            or not isinstance(meta.get("sessionId"), str)
        ):
            raise ValueError("native protocol identity metadata required")
        principal = self.enroll(meta["threadId"])
        if principal["session_id"] != meta["sessionId"]:
            raise ValueError("native protocol session metadata mismatch")
        return principal

    def observe_claude_root(self, session_id):
        if not (self.directory / "claude-root-staged.json").is_file():
            raise ValueError("IDENTITY_NOT_READY: native SessionStart hook not observed")
        staged = json.loads((self.directory / "claude-root-staged.json").read_bytes())
        if staged != {"session_id": session_id}:
            raise ValueError("native Claude SessionStart differs from supervised root")
        self.observe_root_start(session_id)

    def resolve_claude(self, channel_id, name, arguments, context):
        import hmac
        import time

        from harness.claude_identity_hook import signature

        if not self.root or not isinstance(arguments, dict):
            raise ValueError("IDENTITY_NOT_READY: supervised Claude root required")
        attestation = arguments.get("__harness_attestation")
        required = {
            "version",
            "execution_id",
            "epoch",
            "session_id",
            "agent_id",
            "role",
            "channel_id",
            "tool",
            "policy_digest",
            "args_digest",
            "nonce",
            "issued_at",
            "expires_at",
            "signature",
        }
        if not isinstance(attestation, dict) or set(attestation) != required:
            raise ValueError("trusted native Claude attestation required")
        envelope = {k: v for k, v in attestation.items() if k != "signature"}
        if not hmac.compare_digest(str(attestation["signature"]), signature(context, envelope)):
            raise ValueError("native Claude attestation signature differs")
        public = {k: v for k, v in arguments.items() if k != "__harness_attestation"}
        now = time.time()
        if (
            attestation["version"] != 1
            or attestation["epoch"] != context["epoch"]
            or attestation["execution_id"] != context["execution_id"]
            or attestation["session_id"] != self.root
            or attestation["channel_id"] != channel_id
            or attestation["policy_digest"] != self.policy
            or attestation["args_digest"] != digest(public)
            or attestation["tool"] != "mcp__" + channel_id + "__" + name
            or not isinstance(attestation["issued_at"], (int, float))
            or not isinstance(attestation["expires_at"], (int, float))
            or not now - 32 <= attestation["issued_at"] <= now + 2
            or not now <= attestation["expires_at"] <= now + 32
        ):
            raise ValueError("native Claude attestation binding/epoch/arguments/expiry differs")
        agent, role = attestation["agent_id"], attestation["role"]
        if role == "orchestrator":
            if agent != self.root:
                raise ValueError("native Claude root identity differs")
        else:
            from harness.execution_contract import identity

            identity(agent)
            record = json.loads((self.directory / ("claude-agent-" + agent + ".json")).read_bytes())
            if record != {"session_id": self.root, "agent_id": agent, "role": role}:
                raise ValueError("native Claude child enrollment differs")
        channel = context["channels"].get(channel_id)
        if not channel or channel["role"] != role:
            raise ValueError("native Claude principal role/channel differs")
        principal = {
            "thread_id": agent,
            "session_id": self.root,
            "root_thread_id": self.root,
            "parent_thread_id": None if role == "orchestrator" else self.root,
            "role": role,
            "writable": role not in _READ_ONLY and role != "orchestrator",
            "binding": self.binding,
            "policy": self.policy,
        }
        return principal, public, attestation

    def claim_attestation(self, attestation):
        import os
        import re

        nonce = attestation["nonce"]
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
            raise ValueError("invalid native attestation nonce")
        path = self.directory / "claimed-nonces" / (attestation["epoch"] + "-" + nonce + ".json")
        path.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise ValueError("attestation replay requires operation reconciliation") from None
        with os.fdopen(fd, "wb") as stream:
            stream.write(
                canonical(
                    {"nonce": nonce, "request_digest": digest(attestation), "state": "Claimed"}
                ).encode()
            )
            stream.flush()
            os.fsync(stream.fileno())
