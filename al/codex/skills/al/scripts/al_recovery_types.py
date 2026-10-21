"""Closed recovery action constructors, containing no executable shell text."""


def action(command, arguments):
    return {
        "kind": "public_operation",
        "command": command,
        "arguments": arguments,
        "required_input": None,
    }


def supply(kind, reason, operation=None, arguments=None):
    return {
        "kind": "supply_input",
        "command": None,
        "arguments": {},
        "required_input": {
            "kind": kind,
            "description": reason,
            "operation": operation,
            "arguments": arguments or {},
        },
    }


def issue(code, kind, identifier, reason, recovery, observation):
    recovery = dict(
        recovery, completion_condition={"code": code + "_CLEARED", "subject_id": identifier}
    )
    return {
        "code": code,
        "subject": {"kind": kind, "id": identifier},
        "reason": reason,
        "recovery": recovery,
        "observation": observation,
    }
