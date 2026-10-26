"""Select the explicitly configured repository delivery provider."""

from harness.gitlab import GitLabClient


def delivery_client(config, credential_provider=None):
    provider = config.get("provider", "gitlab")
    if provider == "github":
        from harness.github import GitHubClient

        return GitHubClient(config, credential_provider)
    if provider != "gitlab":
        raise ValueError("unsupported delivery provider")
    return GitLabClient(config, credential_provider)
