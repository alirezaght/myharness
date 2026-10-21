"""Disposable explicit publication intent; all actual remote operations stay absent."""

import json
import sys

from al.tests.editorial_receipt_helpers import prepare_readers, state, succeed
from al.tests.runtime_run_helpers import public_run

REMOTE = {
    "provider": "gitlab",
    "identity": "synthetic/review",
    "url": "https://example.invalid/synthetic/review",
}


def command(launched, platform, action, value):
    path = launched[2].parent / "publication-input.json"
    path.write_text(json.dumps(value))
    return public_run(launched, platform, "publication", action, "--file", str(path))


def get_packet(launched, platform):
    return json.loads(succeed(public_run(launched, platform, "publication", "packet")).stdout)


def prepare(launched, platform):
    path = launched[2] / ".al/publication-policy.json"
    path.parent.mkdir()
    policy = {
        "schema_version": 1,
        "kind": "publication-policy-v1",
        "allowed_remotes": [REMOTE],
        "required_areas": ["publication-code"],
        "actions": ["publish_review_request"],
    }
    path.write_text(json.dumps(policy))
    prepare_readers(
        launched, platform, duplicate=False, extra_paths=[".al/publication-policy.json"]
    )
    succeed(public_run(launched, platform, "gates", "run", "--stage", "base"))
    succeed(public_run(launched, platform, "gates", "run", "--stage", "candidate"))
    succeed(
        public_run(
            launched,
            platform,
            "evidence",
            "plan",
            "--area",
            "publication-code",
            "--kind",
            "check",
            "--tier",
            "broad",
            "--scope",
            ".",
            "--",
            sys.executable,
            "-I",
            "-m",
            "json.tool",
            "rows.json",
        )
    )
    succeed(public_run(launched, platform, "evidence", "run", "--area", "publication-code"))
    packet = get_packet(launched, platform)
    authorization = {
        "schema_version": 1,
        "kind": "publication-authorization-v1",
        **{
            key: packet[key]
            for key in ("repository_identity", "session_id", "run_id", "source_revision")
        },
        "remote": REMOTE,
        "actions": ["publish_review_request"],
        "source": {
            "kind": "user",
            "reference": "disposable explicit user input",
            "text": "Record a review request publication fact for this exact synthetic revision; do not merge or deploy.",
        },
    }
    admitted = json.loads(succeed(command(launched, platform, "authorize", authorization)).stdout)
    record = {
        "schema_version": 1,
        "kind": "publication-record-v1",
        **{
            key: packet[key]
            for key in ("repository_identity", "session_id", "run_id", "source_revision")
        },
        "remote": REMOTE,
        **admitted,
        "required_check_bindings": packet["readiness"]["code"]["bindings"],
        "observation": {"provenance": "reported", "artifact_refs": [], "observer_binding": None},
        "result": "pending",
        "reason": "Explicit reported fact from an authorized existing mechanism; no transport is invoked here.",
    }
    return record
