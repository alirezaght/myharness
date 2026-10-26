"""Bounded GitHub REST transport; links never redirect credentials to another host."""

import json
import os
import re
import subprocess
import time
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request

from harness.gitlab import GitLabClient, GitLabError


class GitHubHTTP(GitLabClient):
    def __init__(self, config, credential_provider=None):
        super().__init__(config, credential_provider)
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", config["project"]
        ):
            raise ValueError("GitHub project must be owner/repository")
        self.owner = config["project"].split("/")[0]
        self.repository = "/".join(quote(p, safe="") for p in config["project"].split("/"))
        url = urlsplit(self.endpoint)
        self.api_root = (
            "https://api.github.com"
            if url.hostname == "github.com" and url.path == "" and url.port in {None, 443}
            else self.endpoint + "/api/v3"
        )
        if credential_provider is None:

            def credentials():
                value = os.environ.get(config.get("credential_env", "GITHUB_TOKEN"))
                if value:
                    return value
                result = subprocess.run(
                    ["gh", "auth", "token", "--hostname", url.hostname],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode:
                    raise ValueError(
                        "GitHub credential unavailable in environment or existing gh login"
                    )
                return result.stdout.strip()

            self.credentials = credentials

    def request(self, method, path, payload=None, query=None):
        if (path and not path.startswith("/")) or ".." in path.split("/"):
            raise ValueError("invalid GitHub API path")
        url = self.api_root + "/repos/" + self.repository + path
        if query:
            url += "?" + urlencode(query)
        token = self.credentials()
        if not isinstance(token, str) or not token or any(c in token for c in "\r\n\x00"):
            raise ValueError("GitHub credential unavailable")
        request = Request(
            url,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "AL-Harness",
                "Content-Type": "application/json",
            },
            method=method,
        )
        raw, links = self._response(request, method, "Link")
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise GitLabError("GitHub response failure", uncertain=method != "GET") from None
        from harness.execution_logs import redact_value

        return redact_value(value, secrets=(token,)), links

    def raw_get(self, path, **query):
        return self.request("GET", path, query=query)[0]

    def pages(self, path, key=None, **query):
        try:
            return self._pages(path, key, **query)
        finally:
            self._read_deadline = float("inf")

    def _pages(self, path, key=None, **query):
        values = []
        deadline = time.monotonic() + 30
        for page in range(1, 101):
            if time.monotonic() >= deadline:
                raise GitLabError("GitHub pagination deadline exceeded")
            self._read_deadline = deadline
            value, links = self.request("GET", path, query=query | {"page": page, "per_page": 100})
            rows = value.get(key) if key and isinstance(value, dict) else value
            if not isinstance(rows, list):
                raise GitLabError("GitHub collection is not a list")
            values.extend(rows)
            if not re.search(r'rel="next"', links):
                return values
        raise GitLabError("GitHub pagination exceeds limit")
