"Closed, bounded contracts; no executable rules or caller-authored safe intents."

import re
from dataclasses import dataclass

from harness.execution_contract import canonical, digest, identity
from harness.native_tool_policy import TOOLS

MAXIMUM = 2**53 - 1
SCOPES = {"global", "project", "workflow", "role", "execution"}
DOMAINS = {"execution_tree", "workflow_instance", "named"}
DIMENSIONS = {
    "mutating_operations",
    "retry_attempts",
    "execution_elapsed_ms",
    "operation_deadline_ms",
    "generation_tokens",
    "monetary_cost",
}
CODES = {
    "POLICY_INVALID",
    "RESOURCE_UNRESOLVED",
    "AUTHORITY_STALE",
    "POLICY_DENIED",
    "APPROVAL_REQUIRED",
    "UNSUPPORTED_ACCOUNTING_DOMAIN",
    "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    "BUDGET_EXHAUSTED",
    "CAPABILITY_INVALID",
    "CLOCK_UNCERTAIN",
    "ATTEMPT_NOT_EXECUTABLE",
}


QUALIFICATION_CAUSES = frozenset(
    {
        "source_inventory_mismatch",
        "source_bytes_mismatch",
        "origin_binding_mismatch",
        "receipt_binding_mismatch",
        "origin_inventory_mismatch",
        "interpreter_binding_mismatch",
        "interpreter_bytes_mismatch",
        "launcher_request_mismatch",
        "launcher_bytes_mismatch",
        "platform_unsupported",
        "boundary_probe_failed",
        "preparation_failed",
        "source_state_changed",
        "git_configuration_changed",
        "boundary_profile_changed",
        "git_version_unsupported",
    }
)


def qualification_diagnostic(value):
    """Closed metadata only; validation grants no execution authority."""
    fields = {"schema_version", "cause", "qualification_ref"}
    if (
        not isinstance(value, dict)
        or set(value) not in (fields, fields | {"observation_id"})
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or not isinstance(value.get("cause"), str)
        or value["cause"] not in QUALIFICATION_CAUSES
        or any(
            not isinstance(value.get(key), str) or re.fullmatch(r"[0-9a-f]{32}", value[key]) is None
            for key in (
                "qualification_ref",
                *(["observation_id"] if "observation_id" in value else []),
            )
        )
    ):
        raise ValueError("invalid qualification diagnostic")
    return dict(value)


class PolicyError(ValueError):
    def __init__(self, code, *, diagnostic=None):
        if code not in CODES:
            code = "POLICY_INVALID"
        self.code = code
        self.diagnostic = None
        if diagnostic is not None:
            if code != "UNSUPPORTED_ENFORCEMENT_CAPABILITY":
                raise ValueError("invalid qualification diagnostic")
            self.diagnostic = qualification_diagnostic(diagnostic)
        super().__init__(
            code if self.diagnostic is None else code + " " + canonical(self.diagnostic)
        )


def require(condition, code="POLICY_INVALID"):
    if not condition:
        raise PolicyError(code)


def qualification_require(condition, cause, qualification_ref):
    if not condition:
        diagnostic = (
            None
            if qualification_ref is None
            else {
                "schema_version": 1,
                "cause": cause,
                "qualification_ref": qualification_ref,
            }
        )
        raise PolicyError("UNSUPPORTED_ENFORCEMENT_CAPABILITY", diagnostic=diagnostic) from None


def number(value, *, positive=False):
    require(type(value) is int and (1 if positive else 0) <= value <= MAXIMUM)
    return value


def names(values, *, allowed=None):
    require(isinstance(values, list) and len(values) <= 64 and len(set(values)) == len(values))
    for value in values:
        require(isinstance(value, str))
        identity(value)
        require(allowed is None or value in allowed)
    return tuple(sorted(values))


def policy_document(value):
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "schema_version",
            "policy_id",
            "version",
            "scope_kind",
            "scope_ref",
            "parents",
            "rules",
            "limits",
        }
    )
    require(value["schema_version"] == 1 and type(value["schema_version"]) is int)
    identity(value["policy_id"])
    number(value["version"], positive=True)
    require(value["scope_kind"] in SCOPES)
    identity(value["scope_ref"])
    require(value["scope_kind"] != "global" or value["scope_ref"] == "all")
    require(value["scope_kind"] != "project" or value["scope_ref"] == "current")
    if value["scope_kind"] == "role":
        from harness.native_tool_policy import ROLES

        require(value["scope_ref"] in {"orchestrator", *ROLES})
    names(value["parents"])
    rules = value["rules"]
    require(
        isinstance(rules, dict)
        and set(rules)
        <= {
            "allow_tools",
            "deny_tools",
            "required_approvals",
            "read_paths",
            "write_paths",
            "network",
            "database",
            "production",
            "delegation",
        }
    )
    for key in {"allow_tools", "deny_tools"} & rules.keys():
        names(rules[key], allowed=set(TOOLS))
    if "required_approvals" in rules:
        names(rules["required_approvals"])
    for key in {"read_paths", "write_paths"} & rules.keys():
        from harness.execution_files import relative_path

        require(isinstance(rules[key], list) and len(rules[key]) <= 64)
        for path in rules[key]:
            require(path == "." or str(relative_path(path)) == path)
    for key in {"network", "database", "production", "delegation"} & rules.keys():
        require(rules[key] in {"deny", "existing"})
    require(isinstance(value["limits"], list) and len(value["limits"]) <= 32)
    seen = set()
    for limit in value["limits"]:
        require(
            isinstance(limit, dict)
            and set(limit) == {"pool_id", "dimension", "domain", "owner_ref", "maximum"}
        )
        identity(limit["pool_id"])
        identity(limit["owner_ref"])
        require(limit["pool_id"] not in seen)
        seen.add(limit["pool_id"])
        require(limit["domain"] in DOMAINS, "UNSUPPORTED_ACCOUNTING_DOMAIN")
        require(limit["dimension"] in DIMENSIONS)
        number(limit["maximum"])
    require(len(canonical(value).encode()) <= 32768)
    return value


@dataclass(frozen=True)
class OperationIntent:
    id: str
    execution_id: str
    epoch_id: str
    generation: int
    principal_id: str
    role: str
    task_id: str | None
    revision: str | None
    operation: str
    resources: tuple[str, ...]
    mutating: bool
    retry: bool
    binding: str
    observation_id: str | None
    boundary: str = "closed_handler"
    remote_liability: str = "none"
    retry_reference: tuple[str, str, str, str] | None = None
    claims: tuple[dict, ...] = ()

    @property
    def units(self):
        return int(self.mutating) + int(self.retry)


def policy_digest(documents):
    return digest(sorted(documents, key=lambda row: (row["policy_id"], row["version"])))
