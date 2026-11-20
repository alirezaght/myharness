"Closed, bounded contracts; no executable rules or caller-authored safe intents."

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


class PolicyError(ValueError):
    def __init__(self, code):
        if code not in CODES:
            code = "POLICY_INVALID"
        self.code = code
        super().__init__(code)


def require(condition, code="POLICY_INVALID"):
    if not condition:
        raise PolicyError(code)


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
