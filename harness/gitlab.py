"""Bounded GitLab v4 transport. Mutations are never automatically retried."""

import json
import os
import re
import time
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

    def _response(self, request, method, page_header):
        deadline = min(time.monotonic() + 30, getattr(self, "_read_deadline", float("inf")))
        for attempt in range(3 if method == "GET" else 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GitLabError("Provider read deadline exceeded")
            delay = 0.1 * (attempt + 1)
            try:
                with self.opener.open(request, timeout=min(15, remaining)) as response:
                    chunks, size = [], 0
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise GitLabError(
                                "Provider response deadline exceeded", uncertain=method != "GET"
                            )
                        # read1 performs one underlying read per deadline check.
                        if response.fp is None:
                            break
                        response.fp.raw._sock.settimeout(min(15, remaining))
                        block = response.read1(min(65536, 2 * 1024 * 1024 + 1 - size))
                        if not block:
                            break
                        chunks.append(block)
                        size += len(block)
                        if size > 2 * 1024 * 1024:
                            raise GitLabError(
                                "Provider response exceeds limit", uncertain=method != "GET"
                            )
                    raw = b"".join(chunks)
                    return raw, response.headers.get(page_header, "")
            except HTTPError as error:
                retry_after = error.headers.get("Retry-After", "")
                error.close()
                if method != "GET" or error.code not in {429, 502, 503, 504} or attempt == 2:
                    raise GitLabError(
                        f"Provider HTTP {error.code}",
                        status=error.code,
                        uncertain=method != "GET" and error.code >= 500,
                    ) from None
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        raise GitLabError("Provider retry delay unavailable") from None
                    if not 0 <= delay <= 1:
                        raise GitLabError("Provider retry delay exceeds bounded policy") from None
            except (URLError, TimeoutError, OSError):
                if method != "GET" or attempt == 2:
                    raise GitLabError(
                        "Provider transport unavailable", uncertain=method != "GET"
                    ) from None
            if time.monotonic() + delay >= deadline:
                raise GitLabError("Provider read deadline exceeded")
            time.sleep(delay)
        raise GitLabError("Provider integration unavailable")

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
        raw, next_page = self._response(req, method, "X-Next-Page")
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise GitLabError("GitLab response failure", uncertain=method != "GET") from None
        from harness.execution_logs import redact_value

        return redact_value(value, secrets=(token,)), next_page

    def get(self, path, **query):
        return self.request("GET", path, query=query)[0]

    def list_pages(self, path, **query):
        try:
            return self._list_pages(path, **query)
        finally:
            self._read_deadline = float("inf")

    def _list_pages(self, path, **query):
        result, page = [], "1"
        deadline = time.monotonic() + 30
        for _ in range(100):
            if time.monotonic() >= deadline:
                raise GitLabError("GitLab pagination deadline exceeded")
            self._read_deadline = deadline
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
