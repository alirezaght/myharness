"""Eligibility uses supervisor registry records, never caller-selected role metadata."""

import json

from harness.reliability.contracts import ROLES


def verified_principal(operation, principal):
    try:
        registry = operation.registry
        registry.require_live(principal)
        if (
            principal["binding"] != registry.binding
            or principal["policy"] != registry.policy
            or principal["role"] not in ROLES
        ):
            return None
        from harness.execution_contract import identity

        identity(principal["thread_id"])
        if operation.envelope["host"] == "codex":
            record = json.loads(
                (registry.directory / (principal["thread_id"] + ".json")).read_bytes()
            )
            if record != principal:
                return None
        elif principal["thread_id"] == registry.root:
            if principal["role"] != "orchestrator" or principal["session_id"] != registry.root:
                return None
        else:
            record = json.loads(
                (
                    registry.directory / ("claude-agent-" + principal["thread_id"] + ".json")
                ).read_bytes()
            )
            if (
                record
                != {
                    "session_id": registry.root,
                    "agent_id": principal["thread_id"],
                    "role": principal["role"],
                }
                or principal["session_id"] != registry.root
            ):
                return None
        return {"principal_id": principal["thread_id"], "role": principal["role"]}
    except (KeyError, TypeError, ValueError, OSError, AttributeError):
        return None
