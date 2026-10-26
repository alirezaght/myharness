"""Reviewable browser mappings derived only from the sealed specification."""

import re

from harness.browser_models import url_parts, validate_plan
from harness.db import transaction, utcnow
from harness.delivery_monitor import poll_delivery
from harness.delivery_store import get_delivery
from harness.execution_authority import require_execution_authority
from harness.execution_contract import canonical, digest
from harness.execution_store import get_execution
from harness.spec_record import content_hash, parse_spec_text


def extract_criteria(sealed_bytes):
    lines = sealed_bytes.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == "## Acceptance criteria"]
    if len(starts) != 1:
        raise ValueError("exactly one acceptance criteria section required")
    raw, current = [], None
    for line in lines[starts[0] + 1 :]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            if current is not None:
                raw.append(current.rstrip("\r\n"))
            current = line[2:]
        elif line.strip():
            if current is None or not line.startswith((" ", "\t")):
                if line.strip() == "(none)" and current is None:
                    continue
                raise ValueError("malformed acceptance criterion")
            current += line
        elif current is not None:
            current += line
    if current is not None:
        raw.append(current.rstrip("\r\n"))
    refs = [re.search(r"\bQ\d+\b", text) for text in raw]
    refs = [m.group() if m else None for m in refs]
    return [
        {
            "id": ref
            if ref and refs.count(ref) == 1
            else f"criterion-{i + 1}-{digest(text)[7:19]}",
            "text": text,
            "q_ref": ref,
        }
        for i, (text, ref) in enumerate(zip(raw, refs, strict=True))
    ]


def local_binding(controller, execution_id, target_mapping=None):
    execution = get_execution(controller.conn, execution_id)
    delivery = get_delivery(controller.conn, execution_id)
    staging = (delivery["snapshot"] or {}).get("staging", {})
    config = delivery["config"]
    url = staging.get("external_url")
    source = "provider"
    if not url and target_mapping:
        if set(target_mapping) != {"url", "reason"} or not target_mapping["reason"].strip():
            raise ValueError("explicit target mapping requires reason")
        url, source = target_mapping["url"], "explicit_mapping"
    if url:
        url_parts(url)
    target = {
        "url": url,
        "source": source,
        "mapping": target_mapping,
        "ready": staging.get("ready") is True,
    }
    binding = {
        "execution": execution["binding"],
        "handoff_hash": execution["handoff_hash"],
        "al_session_id": execution["al_session_id"],
        "al_run_id": execution["al_run_id"],
        "generation": delivery["generation"],
        "candidate_sha": delivery["candidate_sha"],
        "provider": config.get("provider", "gitlab"),
        "endpoint": config["endpoint"],
        "project": config["project"],
        "staging": staging,
        "target": target,
    }
    # Observation timestamps are freshness metadata, not deployment identity.
    return binding, target


def current_target(controller, execution_id, *, client=None, mapping=None):
    require_execution_authority(controller, execution_id, operation="delivery")
    poll_delivery(controller, execution_id, client=client, route_failures=False)
    return local_binding(controller, execution_id, mapping)


def propose_browser(controller, execution_id, mapping=None, *, client=None):
    envelope = require_execution_authority(controller, execution_id, operation="delivery")
    record = parse_spec_text(envelope["sealed_bytes"], None)
    if (
        content_hash(record.body) != record.content_hash
        or digest(envelope["sealed_bytes"].encode()) != envelope["sealed_hash"]
    ):
        raise ValueError("sealed specification hash mismatch")
    mapping = mapping or {}
    allowed = {
        "schema_version",
        "target_mapping",
        "navigation_scope",
        "viewport",
        "timeouts",
        "evidence",
        "criteria",
        "limitations",
    }
    if (
        not isinstance(mapping, dict)
        or not mapping.keys() <= allowed
        or mapping.get("schema_version", 1) != 1
    ):
        raise ValueError("invalid mapping fields")
    inventory = extract_criteria(record.body)
    supplied = mapping.get("criteria", [])
    if (
        not isinstance(supplied, list)
        or any(not isinstance(c, dict) or not isinstance(c.get("id"), str) for c in supplied)
        or len({c["id"] for c in supplied}) != len(supplied)
    ):
        raise ValueError("duplicate mapping criteria")
    by_id = {c["id"]: c for c in supplied}
    if not by_id.keys() <= {c["id"] for c in inventory}:
        raise ValueError("unknown mapped criterion")
    scenarios = {s["ref"] for category in envelope["plan"]["categories"].values() for s in category}
    for item in inventory:
        supplied = by_id.get(item["id"], {})
        if not supplied.keys() <= {"id", "classification", "reason", "scenario_refs", "steps"}:
            raise ValueError("mapping cannot replace approved criterion text")
        refs = supplied.get("scenario_refs", [])
        if supplied.get("classification") in {"selected", "excluded"} and not supplied.get(
            "reason"
        ):
            raise ValueError("explicit selected/excluded mapping requires rationale")
        if not isinstance(refs, list) or not set(refs) <= scenarios:
            raise ValueError("unknown approved scenario reference")
        item.update(
            {
                "classification": supplied.get("classification", "unresolved"),
                "reason": supplied.get("reason", "Executable mapping required"),
                "scenario_refs": refs,
                "steps": supplied.get("steps", []),
            }
        )
    blockers = []
    try:
        binding, target = current_target(
            controller, execution_id, client=client, mapping=mapping.get("target_mapping")
        )
    except (ValueError, OSError) as error:
        from harness.execution_logs import redact

        blockers.append(redact(str(error)))
        binding, target = local_binding(controller, execution_id, mapping.get("target_mapping"))
    if not target["ready"]:
        blockers.append("Exact candidate staging deployment unavailable")
    if not target["url"]:
        blockers.append("Staging URL requires explicit target mapping")
    origin = None
    if target["url"]:
        from urllib.parse import urlsplit

        parsed = urlsplit(target["url"])
        origin = f"{parsed.scheme}://{parsed.netloc}/"
    plan = validate_plan(
        {
            "schema_version": 1,
            "execution_id": execution_id,
            "binding": binding,
            "target": target,
            "navigation_scope": mapping.get(
                "navigation_scope", [{"origin": origin or "http://localhost/", "path_prefix": "/"}]
            ),
            "viewport": mapping.get("viewport", {"width": 1280, "height": 800}),
            "timeouts": mapping.get("timeouts", {"action_ms": 5000, "run_ms": 60000}),
            "evidence": mapping.get(
                "evidence", {"screenshot": "each_criterion", "mask_selectors": []}
            ),
            "criteria": inventory,
            "approved_scenarios": envelope["plan"]["categories"],
            "authentication": "fresh_anonymous_profile",
            "limitations": mapping.get("limitations", [])
            + [
                "Fresh anonymous profile; no authenticated session import",
                "Document navigation interception does not isolate resource or XHR traffic",
                "Deployment stability is verified only at observation times",
                "Query and fragment URL operands are unsupported",
                "Mocked approved scenarios remain limited to their recorded provenance",
            ],
            "blockers": blockers,
        }
    )
    plan_id = digest(plan)
    with transaction(controller.conn):
        controller.conn.execute(
            "INSERT OR IGNORE INTO browser_plans VALUES (?,?,?,?)",
            (plan_id, execution_id, canonical(plan), utcnow()),
        )
        from harness.browser_store import browser_event

        browser_event(controller.conn, execution_id, "browser.proposed", {"plan_id": plan_id})
    return {"plan_id": plan_id, "plan": plan}
