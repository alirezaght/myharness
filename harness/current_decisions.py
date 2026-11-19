"""Current task-bound registry authority, overlaid without rewriting investigation history."""

from dataclasses import asdict
from pathlib import Path

from harness.decisions import DecisionStatus, parse_decision


def recorded_bindings(ws):
    bindings = {}
    for event in ws.events():
        if event.kind in {"decision.recorded", "decision.superseded"} and event.payload.get("path"):
            value = dict(event.payload)
            value.setdefault("status", "approved")
            bindings[value["id"]] = value
    return bindings


def matching_record(binding, registry=None):
    path = Path(binding.get("path", ""))
    if not path.is_file():
        return None
    record = registry.get(binding["id"]) if registry else parse_decision(path)
    if (
        record.id != binding["id"]
        or record.version != binding["version"]
        or str(record.path) != str(path)
        or str(record.status) != binding.get("status", "approved")
    ):
        return None
    return record


def current_decisions(ws, registry=None):
    checkpoint = ws.latest_checkpoint("investigation")
    selected = {
        row["id"]: dict(row)
        for row in (checkpoint.payload.get("decisions", []) if checkpoint else [])
    }
    selected.update(recorded_bindings(ws))
    result = []
    for binding in selected.values():
        record = matching_record(binding, registry)
        if record is None:
            result.append(dict(binding, status="unavailable"))
        else:
            result.append({**binding, **asdict(record), "path": str(record.path)})
    return result


def current_investigation_decisions(ws):
    checkpoint = ws.latest_checkpoint("investigation")
    cohort = {row["id"] for row in (checkpoint.payload.get("decisions", []) if checkpoint else [])}
    return [row for row in current_decisions(ws) if row["id"] in cohort]


def current_recorded_decisions(ws):
    result = []
    for binding in recorded_bindings(ws).values():
        record = matching_record(binding)
        if record is not None and record.status is DecisionStatus.APPROVED:
            result.append(record)
    return result
