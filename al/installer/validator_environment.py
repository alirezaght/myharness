"""Hash actual caller inputs without restoring them to the isolated worker."""

import hashlib
import os
import sys
import types


def capture_environment():
    return {
        "schema_version": 1,
        "kind": "terminal-caller-environment-v1",
        "values": {
            name: {"present": True, "digest": hashlib.sha256(value.encode()).hexdigest()}
            for name, value in os.environ.items()
        },
    }


def install_environment(value):
    if value is None:
        return
    if "_al_terminal_observation" in sys.modules:
        raise ValueError("terminal caller observation already installed")
    context = types.ModuleType("_al_terminal_observation")
    context.environment = value
    sys.modules[context.__name__] = context
    from al_environment_observation import observed

    observed()
