"""Fresh caller hashes for read-only terminal verification, never executable env."""

import hashlib
import os
import re
import sys


def observed():
    context = sys.modules.get("_al_terminal_observation")
    if context is None:
        return None
    trusted = sys.modules.get("_al_validator_context")
    if trusted is None or trusted.request.get("run_pin_context") is None:
        raise ValueError("environment observation requires the protected terminal pin context")
    value = context.environment
    if value != trusted.request.get("caller_environment"):
        raise ValueError("terminal caller environment binding changed")
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "kind", "values"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "terminal-caller-environment-v1"
    ):
        raise ValueError("unsupported terminal caller environment observation")
    values = value["values"]
    if not isinstance(values, dict) or len(values) > 4096:
        raise ValueError("invalid bounded terminal caller environment")
    for name, item in values.items():
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 4096
            or "\0" in name
            or "=" in name
            or not isinstance(item, dict)
            or set(item) != {"present", "digest"}
            or item["present"] is not True
            or not isinstance(item["digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["digest"])
        ):
            raise ValueError("invalid terminal caller environment fingerprint")
    return {name: dict(item) for name, item in values.items()}


# Variables that change what a verification command executes, beyond its declared names.
EXECUTION_NAMES = frozenset({"PATH", "HOME", "LANG", "TZ"})
EXECUTION_PREFIXES = ("PYTHON", "LC_")


def execution_environment(declared):
    """Declared names plus execution-relevant variables, by exact digest.

    Unrelated session variables (agent or terminal markers) never bind evidence."""
    snapshot = observed()
    present = set(snapshot) if snapshot is not None else set(os.environ)
    names = set(declared) | EXECUTION_NAMES | {n for n in present if n.startswith(EXECUTION_PREFIXES)}
    return environment(names)


def environment(names):
    snapshot = observed()
    if snapshot is not None:
        return {
            name: snapshot.get(name, {"present": False, "digest": None})
            for name in sorted(set(names))
        }
    return {
        name: {
            "present": name in os.environ,
            "digest": hashlib.sha256(os.environ[name].encode()).hexdigest()
            if name in os.environ
            else None,
        }
        for name in sorted(set(names))
    }


def ambient_environment():
    snapshot = observed()
    return snapshot if snapshot is not None else environment(os.environ)


def value_digest(name):
    value = environment([name])[name]
    return value["digest"] if value["present"] else "unset"
