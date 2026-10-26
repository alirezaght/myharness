"""Bounded GitLab v4 transport. Mutations are never automatically retried."""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GitLabError(ValueError):
    def __init__(self, message, *, uncertain=False, status=None):
        super().__init__(message)
        self.uncertain, self.status = uncertain, status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitLabClient:
    def __init__(self, config, credential_provider=None):
        self.config = config
        self.endpoint = config["endpoint"].rstrip("/")
        url = urlsplit(self.endpoint)
        loopback = url.hostname in {"127.0.0.1", "::1", "localhost"}
        if (
            url.scheme != "https"
            and not (url.scheme == "http" and config.get("test_loopback") is True and loopback)
        ) or (url.username or url.password or url.query or url.fragment or not url.hostname):
            raise ValueError("Delivery endpoint requires HTTPS without credentials/query/fragment")
        self.project = quote(str(config["project"]), safe="")
        self.credentials = credential_provider or (lambda: os.environ[config["credential_env"]])
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, payload=None, query=None):
        if (path and not path.startswith("/")) or ".." in path.split("/"):
            raise ValueError("invalid GitLab API path")
        url = self.endpoint + "/api/v4/projects/" + self.project + path
        if query:
            url += "?" + urlencode(query)
        token = self.credentials()
        if not token or "\n" in token or "\r" in token:
            raise ValueError("GitLab credential unavailable")
        req = Request(
            url,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self.opener.open(req, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise GitLabError("GitLab response exceeds limit", uncertain=method != "GET")
                value = json.loads(raw)
                from harness.execution_logs import redact_value

                return redact_value(value, secrets=(token,)), response.headers.get(
                    "X-Next-Page", ""
                )
        except HTTPError as error:
            raise GitLabError(
                f"GitLab HTTP {error.code}",
                uncertain=method != "GET" and error.code >= 500,
                status=error.code,
            ) from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise GitLabError(
                "GitLab transport or response failure", uncertain=method != "GET"
            ) from None

    def get(self, path, **query):
        return self.request("GET", path, query=query)[0]

    def list_pages(self, path, **query):
        result, page = [], "1"
        for _ in range(100):
            values, next_page = self.request(
                "GET", path, query=dict(query, page=page, per_page=100)
            )
            if not isinstance(values, list):
                raise GitLabError("GitLab collection is not a list")
            result.extend(values)
            if not next_page:
                return result
            if not re.fullmatch(r"[1-9][0-9]*", next_page) or int(next_page) <= int(page):
                raise GitLabError("GitLab pagination did not advance")
            page = next_page
        raise GitLabError("GitLab pagination exceeds limit")

    def branch(self, name):
        return self.get("/repository/branches/" + quote(name, safe=""))

    def create_branch(self, name, sha):
        return self.request("POST", "/repository/branches", {"branch": name, "ref": sha})[0]

    def create_merge_request(self, source, target, title, marker):
        return self.request(
            "POST",
            "/merge_requests",
            {
                "source_branch": source,
                "target_branch": target,
                "title": title,
                "description": marker,
                "remove_source_branch": False,
            },
        )[0]
