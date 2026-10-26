"""Validated explicit delivery configuration; credentials are references only."""

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DeliveryConfig:
    endpoint: str
    project: str
    source_branch: str
    target_branch: str
    staging_environment: str
    credential_env: str | None = None
    publication_authorized: bool = False
    upload_authorized: bool = False
    retry_limit: int = 3
    test_loopback: bool = False
    provider: str = "gitlab"

    def to_dict(self):
        value = asdict(self)
        if self.provider not in {"github", "gitlab"}:
            raise ValueError("delivery provider must be github or gitlab")
        value["credential_env"] = self.credential_env or (
            "GITHUB_TOKEN" if self.provider == "github" else "GITLAB_TOKEN"
        )
        if type(self.retry_limit) is not int or not 1 <= self.retry_limit <= 10:
            raise ValueError("retry limit must be between 1 and 10")
        if any(
            type(v) is not bool
            for v in (self.publication_authorized, self.upload_authorized, self.test_loopback)
        ):
            raise ValueError("delivery authorizations must be explicit booleans")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", value["credential_env"]):
            raise ValueError("invalid credential environment reference")
        for name in (self.source_branch, self.target_branch):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,199}", name) or (
                ".." in name or "//" in name or name.endswith(("/", ".", ".lock"))
            ):
                raise ValueError("invalid delivery branch")
        if (
            self.source_branch == self.target_branch
            or not self.project
            or not self.staging_environment
        ):
            raise ValueError("explicit distinct source/target and staging environment required")
        return value
