"""Controller-owned publication; intent precedes each external mutation."""

import subprocess
import time

from harness.delivery_providers import delivery_client
from harness.delivery_store import (
    claim_operation,
    delivery_view,
    finish_operation,
    get_delivery,
)
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, loads
from harness.execution_hosts import al_argv, installed_package
from harness.execution_publication_contract import validate_delivery
from harness.execution_store import get_execution
from harness.gitlab import GitLabError


def publication_candidate(controller, execution_id):
    envelope = require_execution_authority(controller, execution_id, operation="delivery")
    execution = get_execution(controller.conn, execution_id)
    package = installed_package(execution["host"], envelope["repository"])
    command = al_argv(
        package["launcher"],
        execution["host"],
        envelope["repository"],
        execution["al_session_id"],
        "publication",
        "status",
        existing_run=True,
    )
    result = subprocess.run(command, cwd=envelope["repository"], capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 262144:
        raise ValueError("authoritative publication readiness unavailable")
    readiness = loads(result.stdout)
    binding = execution["binding"] | {
        "al_session_id": execution["al_session_id"],
        "al_run_id": execution["al_run_id"],
    }
    validate_delivery(
        {"projection_version": 2, "status": readiness["status"], "publication": readiness}, binding
    )
    if not readiness["ready_to_publish"] or not readiness["current_source"]:
        raise ValueError("current AL publication authorization/checks are not ready")
    # Current AL readiness must authorize this exact configured external repository.
    import hashlib
    from pathlib import Path

    reference = readiness["record_ref"]
    if not reference:
        raise ValueError("publication requires admitted current AL publication intent")
    raw = Path(reference["stored_path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("publication intent artifact changed")
    record = loads(raw)
    config = get_delivery(controller.conn, execution_id)["config"]
    expected_remote = config["endpoint"].rstrip("/") + "/" + str(config["project"])
    remote = record.get("remote", {})
    if (
        remote.get("provider") != config.get("provider", "gitlab")
        or remote.get("url", "").removesuffix(".git") != expected_remote
    ):
        raise ValueError("AL publication authorization targets another repository")
    root = envelope["repository"]
    from harness.command_safety import git_read

    sha = git_read(root, ["rev-parse", "HEAD"]).strip()
    if git_read(root, ["status", "--porcelain"]).strip():
        raise ValueError("publication requires committed implementation")
    if sha != readiness["source_revision"]["commit_oid"]:
        raise ValueError("AL publication candidate differs from current commit")
    return sha


def _mutate(conn, delivery, kind, sha, request, action):
    operation, prior = claim_operation(conn, delivery, kind, sha, request)
    if prior is not None:
        return prior
    try:
        result = action()
    except GitLabError as error:
        finish_operation(conn, operation, "Uncertain" if error.uncertain else "Denied")
        raise
    except BaseException:
        finish_operation(conn, operation, "Uncertain")
        raise
    finish_operation(conn, operation, "Confirmed", result)
    return result


def publish_delivery(controller, execution_id, *, client=None):
    delivery = get_delivery(controller.conn, execution_id)
    if not delivery["config"]["publication_authorized"]:
        raise ValueError("explicit external publication authorization required")
    sha = publication_candidate(controller, execution_id)
    api = client or delivery_client(delivery["config"])
    config = delivery["config"]
    # GitLab creates a branch only from an existing remote object. Never substitute target HEAD.
    try:
        commit = api.get("/repository/commits/" + sha)
    except GitLabError as error:
        if error.status != 404:
            raise
        from harness.delivery_upload import upload_candidate

        envelope = require_execution_authority(controller, execution_id, operation="delivery")
        upload_candidate(controller, execution_id, delivery, sha, api, envelope["repository"])
        commit = api.get("/repository/commits/" + sha)
    if commit.get("id") != sha:
        raise ValueError(
            "candidate is not present in configured provider project; authorized upload required"
        )
    try:
        branch = api.branch(config["source_branch"])
    except GitLabError as error:
        if error.status != 404:
            raise
        branch = _mutate(
            controller.conn,
            delivery,
            "branch",
            sha,
            {"branch": config["source_branch"], "ref": sha},
            lambda: api.create_branch(config["source_branch"], sha),
        )
    if branch.get("commit", {}).get("id") != sha:
        from harness.delivery_upload import upload_candidate

        envelope = require_execution_authority(controller, execution_id, operation="delivery")
        branch = upload_candidate(
            controller, execution_id, delivery, sha, api, envelope["repository"]
        )
        if branch.get("commit", {}).get("id") != sha:
            raise ValueError("source branch differs from implementation candidate")
    marker = f"<!-- harness:{execution_id}:{delivery['generation']} -->"
    if delivery["mr"]:
        mr = api.get(f"/merge_requests/{delivery['mr']['iid']}")
    else:
        mr = _mutate(
            controller.conn,
            delivery,
            "merge_request",
            sha,
            {
                "source": config["source_branch"],
                "target": config["target_branch"],
                "marker": marker,
            },
            lambda: api.create_merge_request(
                config["source_branch"], config["target_branch"], "Harness " + execution_id, marker
            ),
        )
    _validate_mr(mr, config, sha)
    controller.conn.execute(
        "UPDATE deliveries SET candidate_sha=?,mr=?,status='Published' WHERE execution_id=?",
        (sha, canonical(mr), execution_id),
    )
    return delivery_view(controller.conn, execution_id)


def _validate_mr(mr, config, sha):
    if (
        type(mr.get("iid")) is not int
        or mr["iid"] < 1
        or mr.get("sha") != sha
        or mr.get("source_branch") != config["source_branch"]
        or mr.get("target_branch") != config["target_branch"]
    ):
        raise ValueError("Review request identity does not match delivery")


def reconcile_delivery(controller, execution_id, *, client=None):
    require_execution_authority(controller, execution_id, operation="delivery")
    delivery = get_delivery(controller.conn, execution_id)
    api = client or delivery_client(delivery["config"])
    rows = controller.conn.execute(
        "SELECT * FROM delivery_operations WHERE execution_id=? "
        "AND state IN ('Uncertain','Dispatching')",
        (execution_id,),
    ).fetchall()
    for row in rows:
        request, sha = loads(row["request"]), row["candidate_sha"]
        if row["kind"] in {"branch", "push"}:
            value = api.branch(request.get("branch", delivery["config"]["source_branch"]))
            if value.get("commit", {}).get("id") != sha:
                raise ValueError("uncertain branch conflicts with candidate")
        elif row["kind"] == "merge_request":
            values = api.list_pages(
                "/merge_requests",
                scope="all",
                state="all",
                source_branch=request["source"],
                target_branch=request["target"],
            )
            matches = [v for v in values if request["marker"] in (v.get("description") or "")]
            if len(matches) != 1:
                raise ValueError(
                    "uncertain MR has no unique exact reconciliation; no mutation retried"
                )
            value = matches[0]
            _validate_mr(value, delivery["config"], sha)
        else:
            raise ValueError("unsupported reconciliation operation")
        finish_operation(controller.conn, row["id"], "Confirmed", value)
    return delivery_view(controller.conn, execution_id)


def watch_delivery(controller, execution_id, *, interval=5, max_polls=60):
    from harness.delivery_monitor import poll_delivery

    if not 1 <= interval <= 60 or not 1 <= max_polls <= 1000:
        raise ValueError("watch limits exceeded")
    for count in range(max_polls):
        value = poll_delivery(controller, execution_id)
        if value["status"] in {"Success", "Blocked", "DeveloperReview", "Correcting"}:
            return value
        if count + 1 < max_polls:
            time.sleep(interval)
    from harness.delivery_store import save_snapshot

    row = get_delivery(controller.conn, execution_id)
    snapshot = row["snapshot"] | {
        "blockers": [
            *row["snapshot"].get("blockers", []),
            "Delivery monitoring limit reached; inspect the pipeline before resuming.",
        ]
    }
    save_snapshot(controller, execution_id, snapshot, "Blocked")
    return delivery_view(controller.conn, execution_id)
