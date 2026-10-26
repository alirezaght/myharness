"""Closed, bounded browser plan language and exact URL scope."""

import posixpath
import re
from urllib.parse import unquote, urlsplit

from harness.execution_contract import bounded, canonical, loads
from harness.execution_logs import redact


def safe_text(value):
    if not isinstance(value, str) or not value or len(value) > 8192 or redact(value) != value:
        raise ValueError("invalid or secret-bearing browser operand")
    if re.search(
        r"(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{16,}|glpat-[A-Za-z0-9_-]{12,})",
        value,
    ):
        raise ValueError("secret-bearing browser literal refused")
    return value


def url_parts(value):
    safe_text(value)
    if any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("invalid browser URL")
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("browser URL requires http(s), host and no credentials")
    if url.query or url.fragment:
        raise ValueError("query and fragment operands are unsupported")
    path = unquote(url.path or "/")
    if "\\" in path or "%" in path or any(ord(c) < 32 for c in path):
        raise ValueError("ambiguous URL path")
    path = posixpath.normpath(path)
    return (
        url.scheme,
        url.hostname.lower(),
        url.port or (443 if url.scheme == "https" else 80),
    ), path


def in_scope(url, scope):
    origin, path = url_parts(url)
    return any(
        origin == url_parts(s["origin"])[0]
        and (
            path == s["path_prefix"].rstrip("/")
            or path.startswith(s["path_prefix"].rstrip("/") + "/")
        )
        for s in scope
    )


def validate_plan(value):
    bounded(value)
    plan = loads(canonical(value), max_bytes=4 * 1024 * 1024)
    if (
        set(plan)
        != {
            "schema_version",
            "execution_id",
            "binding",
            "target",
            "navigation_scope",
            "viewport",
            "timeouts",
            "evidence",
            "criteria",
            "approved_scenarios",
            "limitations",
            "authentication",
            "blockers",
        }
        or plan["schema_version"] != 1
    ):
        raise ValueError("invalid browser plan fields")
    if plan["authentication"] != "fresh_anonymous_profile":
        raise ValueError("unsupported authentication")
    if not isinstance(plan["approved_scenarios"], dict):
        raise ValueError("invalid approved scenario inventory")
    for key in ("limitations", "blockers"):
        if not isinstance(plan[key], list) or any(not isinstance(v, str) for v in plan[key]):
            raise ValueError("invalid plan " + key)
        for value in plan[key]:
            safe_text(value)
    if not isinstance(plan["criteria"], list) or len(plan["criteria"]) > 100:
        raise ValueError("invalid criterion inventory")
    if set(plan["target"]) != {"url", "source", "mapping", "ready"}:
        raise ValueError("invalid target fields")
    scope = plan["navigation_scope"]
    if not isinstance(scope, list) or not scope:
        raise ValueError("navigation scope required")
    for entry in scope:
        if (
            set(entry) != {"origin", "path_prefix"}
            or not isinstance(entry["path_prefix"], str)
            or not entry["path_prefix"].startswith("/")
        ):
            raise ValueError("invalid navigation scope")
        _, path = url_parts(entry["origin"])
        if path != "/" or url_parts(entry["origin"].rstrip("/") + entry["path_prefix"])[
            1
        ] != posixpath.normpath(entry["path_prefix"]):
            raise ValueError("scope origin/path invalid")
    if plan["target"].get("url") and not in_scope(plan["target"]["url"], scope):
        raise ValueError("target outside navigation scope")
    if set(plan["viewport"]) != {"width", "height"} or any(
        type(v) is not int or not 320 <= v <= 4096 for v in plan["viewport"].values()
    ):
        raise ValueError("invalid viewport")
    if set(plan["timeouts"]) != {"action_ms", "run_ms"} or any(
        type(v) is not int or not 100 <= v <= 300000 for v in plan["timeouts"].values()
    ):
        raise ValueError("invalid timeout")
    evidence = plan["evidence"]
    if (
        set(evidence) != {"screenshot", "mask_selectors"}
        or evidence["screenshot"] not in {"none", "each_criterion"}
        or not isinstance(evidence["mask_selectors"], list)
    ):
        raise ValueError("invalid evidence policy")
    for selector in evidence["mask_selectors"]:
        safe_text(selector)
    ids = set()
    fields = {
        "navigate": {"url"},
        "click": {"selector"},
        "fill": {"selector", "value"},
        "assert_visible": {"selector"},
        "assert_text": {"selector", "equals"},
        "assert_url": {"equals"},
    }
    for item in plan["criteria"]:
        if (
            set(item)
            != {"id", "text", "q_ref", "classification", "reason", "scenario_refs", "steps"}
            or item["id"] in ids
        ):
            raise ValueError("invalid criterion inventory")
        ids.add(item["id"])
        if item["classification"] not in {"selected", "excluded", "unresolved"}:
            raise ValueError("invalid criterion classification")
        safe_text(item["id"])
        safe_text(item["text"])
        if not isinstance(item["scenario_refs"], list) or any(
            not isinstance(ref, str) for ref in item["scenario_refs"]
        ):
            raise ValueError("invalid scenario references")
        safe_text(item["reason"])
        if not isinstance(item["steps"], list) or len(item["steps"]) > 100:
            raise ValueError("invalid steps")
        for step in item["steps"]:
            op = step.get("op")
            if op not in fields or set(step) != fields[op] | {"op"}:
                raise ValueError("unsupported browser step")
            for operand in fields[op]:
                safe_text(step[operand])
            if op in {"navigate", "assert_url"} and not in_scope(
                step.get("url", step.get("equals")), scope
            ):
                raise ValueError("step outside navigation scope")
        if item["classification"] == "selected" and not any(
            s["op"].startswith("assert_") for s in item["steps"]
        ):
            raise ValueError("selected criterion requires assertion")
        if item["classification"] != "selected" and item["steps"]:
            raise ValueError("unselected criterion has executable steps")
    return plan


def evidence_origin(target_url):
    import ipaddress

    hostname = urlsplit(target_url).hostname
    try:
        local = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        local = hostname == "localhost" or hostname.endswith(".localhost")
    return "local_browser_fixture" if local else "real_browser"
