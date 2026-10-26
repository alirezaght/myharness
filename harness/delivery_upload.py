"""Separately authorized non-force upload to one explicit GitLab source ref."""

import base64
import subprocess
from urllib.parse import quote

from harness.command_safety import git_argv, safe_environment
from harness.gitlab import GitLabError


def upload_candidate(controller, execution_id, delivery, sha, api, repository):
    from harness.delivery import _mutate

    config = delivery["config"]
    if not config.get("upload_authorized"):
        raise ValueError(
            "candidate is not remote; explicit source-ref upload authorization required"
        )
    remote = config["endpoint"].rstrip("/") + "/" + quote(str(config["project"]), safe="/") + ".git"
    request = {"remote": remote, "source_ref": "refs/heads/" + config["source_branch"], "sha": sha}

    def perform():
        # Credentials stay in supervisor process environment, never argv/intent/error output.
        token = api.credentials()
        if not isinstance(token, str) or not token or "\x00" in token:
            raise ValueError("GitLab upload credential unavailable")
        environment = safe_environment() | {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http." + remote + ".extraHeader",
            "GIT_CONFIG_VALUE_0": "Authorization: Basic "
            + base64.b64encode(
                (
                    ("x-access-token:" if config.get("provider") == "github" else "oauth2:") + token
                ).encode()
            ).decode(),
        }
        argv = git_argv(
            [
                "-c",
                "http.followRedirects=false",
                "-c",
                "http.sslVerify=true",
                "-c",
                "credential.helper=",
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.https.allow=always",
                "push",
                "--no-verify",
                "--",
                remote,
                sha + ":" + request["source_ref"],
            ]
        )
        result = subprocess.run(
            argv,
            cwd=repository,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        if result.returncode:
            raise GitLabError(
                "candidate upload failed; reconcile source ref before retry", uncertain=True
            )
        branch = api.branch(config["source_branch"])
        if branch.get("commit", {}).get("id") != sha:
            raise GitLabError("uploaded ref verification failed", uncertain=True)
        return branch

    return _mutate(controller.conn, delivery, "push", sha, request, perform)
