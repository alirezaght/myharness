"""Sticky same-database enrollment; untouched legacy presence is explicitly unqualified."""

import json

from harness.policy.claims import overlaps, require_clear
from harness.policy.contracts import require


def affected(conn, claims):
    domains = conn.execute("SELECT repository,resource FROM policy_resource_domains").fetchall()
    return any(
        value["access"] == "unknown"
        or value["repository"] == domain["repository"]
        and overlaps(value["resource"], domain["resource"])
        for value in claims
        for domain in domains
    )


def enroll(conn, repository, resources, decision_id):
    existing = conn.execute(
        "SELECT resource FROM policy_resource_domains WHERE repository=?", (repository,)
    ).fetchall()
    resources = [
        resource
        for resource in resources
        if not any(
            row["resource"] == "."
            or resource == row["resource"]
            or resource.startswith(row["resource"] + "/")
            for row in existing
        )
    ]
    if not resources:
        return
    require(
        not conn.execute(
            "SELECT 1 FROM execution_attempts WHERE child_pid IS NOT NULL LIMIT 1"
        ).fetchone()
        and not conn.execute(
            "SELECT 1 FROM reliability_process_spans s WHERE NOT EXISTS "
            "(SELECT 1 FROM policy_processes p WHERE p.span_id=s.span_id) LIMIT 1"
        ).fetchone(),
        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    )
    proposed = [
        {"repository": repository, "resource": resource, "access": "write"}
        for resource in resources
    ]
    require_clear(conn, proposed)
    for row in conn.execute("SELECT resources FROM policy_legacy_admissions"):
        for old in json.loads(row["resources"]):
            require(
                not any(
                    old["access"] == "unknown"
                    or old["repository"] == new["repository"]
                    and overlaps(old["resource"], new["resource"])
                    for new in proposed
                ),
                "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
            )
    for resource in resources:
        conn.execute(
            "INSERT OR IGNORE INTO policy_resource_domains VALUES (?,?,?)",
            (repository, resource, decision_id),
        )
