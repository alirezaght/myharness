"""Account for delivered and service costs in paired knowledge evaluations.

Input is one JSON object per line. This utility reports observations only; it does not
create native AL executions, judge answer quality, or claim mission acceptance.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ARMS = {"B", "C"}
PHASES = {"cold-index", "warm-query", "continuity"}


def _nonnegative(value: Any, field: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(
            f"{field} must be a non-negative integer" + (" or null" if optional else "")
        )
    return value


def read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(event, dict):
            raise ValueError(f"line {line_number}: event must be a JSON object")
        required = {"pair_id", "arm", "phase", "participant", "visible_text", "service_read_bytes"}
        if not required <= event.keys():
            raise ValueError(
                f"line {line_number}: missing fields {sorted(required - event.keys())}"
            )
        if not isinstance(event["pair_id"], str) or not event["pair_id"]:
            raise ValueError(f"line {line_number}: pair_id must be non-empty")
        if event["arm"] not in ARMS or event["phase"] not in PHASES:
            raise ValueError(
                f"line {line_number}: arm must be B/C and phase must be one of {sorted(PHASES)}"
            )
        if event["participant"] not in {"single", "donor", "recipient"}:
            raise ValueError(f"line {line_number}: invalid participant")
        if event["phase"] == "warm-query":
            if not isinstance(event.get("query_id"), str) or not event["query_id"]:
                raise ValueError(f"line {line_number}: warm-query requires query_id")
            _nonnegative(event.get("query_number"), "query_number")
            if event["query_number"] < 1:
                raise ValueError(f"line {line_number}: query_number must be positive")
        if not isinstance(event["visible_text"], str):
            raise ValueError(
                f"line {line_number}: visible_text must contain the delivered UTF-8 text"
            )
        _nonnegative(event["service_read_bytes"], "service_read_bytes")
        _nonnegative(event.get("elapsed_ms"), "elapsed_ms", optional=True)
        _nonnegative(event.get("storage_bytes"), "storage_bytes", optional=True)
        _nonnegative(event.get("input_tokens"), "input_tokens", optional=True)
        _nonnegative(event.get("output_tokens"), "output_tokens", optional=True)
        if "integrity_violations" in event:
            _nonnegative(event["integrity_violations"], "integrity_violations")
        sources = event.get("sources", [])
        if not isinstance(sources, list):
            raise ValueError(f"line {line_number}: sources must be a list")
        for source in sources:
            if not isinstance(source, dict) or not all(
                isinstance(source.get(key), str) and source[key]
                for key in ("snapshot", "path", "span")
            ):
                raise ValueError(
                    f"line {line_number}: each source needs snapshot, path and span identities"
                )
            _nonnegative(source.get("bytes"), "source.bytes")
        events.append(event)
    return events


def _sum_known(events: list[dict], key: str) -> int | None:
    values = [event.get(key) for event in events]
    if not values or any(value is None for value in values):
        return None
    return sum(values)


def _arm_summary(events: list[dict]) -> dict:
    visible = sum(len(event["visible_text"].encode("utf-8")) for event in events)
    identities: dict[tuple, int] = {}
    for event in events:
        for source in event.get("sources", []):
            key = (source["snapshot"], source["path"], source["span"])
            identities.setdefault(key, source["bytes"])
    return {
        "events": len(events),
        "agent_visible_bytes": visible,
        "service_read_bytes": sum(event["service_read_bytes"] for event in events),
        "unique_source_bytes": sum(identities.values()),
        "unique_source_spans": len(identities),
        "input_tokens": _sum_known(events, "input_tokens"),
        "output_tokens": _sum_known(events, "output_tokens"),
        "elapsed_ms": _sum_known(events, "elapsed_ms"),
        "storage_bytes": max(
            (e["storage_bytes"] for e in events if e.get("storage_bytes") is not None), default=None
        ),
        "integrity_violations": sum(e.get("integrity_violations", 0) for e in events),
    }


def _byte_cost(events: list[dict]) -> dict:
    return {
        "agent_visible_bytes": sum(len(e["visible_text"].encode("utf-8")) for e in events),
        "service_read_bytes": sum(e["service_read_bytes"] for e in events),
    }


def _query_costs(events: list[dict]) -> dict:
    output = {}
    for arm in sorted(ARMS):
        arm_events = [event for event in events if event["arm"] == arm]
        cold = [event for event in arm_events if event["phase"] == "cold-index"]
        warm = [event for event in arm_events if event["phase"] == "warm-query"]
        by_number: dict[int, list[dict]] = defaultdict(list)
        for event in warm:
            by_number[event["query_number"]].append(event)
        first = _byte_cost(cold + by_number.get(1, [])) if cold and 1 in by_number else None
        warm_numbers = sorted(by_number)
        amortized = None
        if cold and warm_numbers:
            all_cost = _byte_cost(cold + warm)
            amortized = {
                key: all_cost[key] / len(warm_numbers)
                for key in ("agent_visible_bytes", "service_read_bytes")
            }
        output[arm] = {
            "cold_index_plus_one_query": first,
            "amortized_cold_index_cost_per_warm_query": amortized,
            "warm_query_count": len(warm_numbers),
        }
    return output


def build_report(events: list[dict], *, adapter: dict | None = None) -> dict:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for event in events:
        groups[(event["pair_id"], event["arm"])].append(event)
    summary = {
        arm: _arm_summary([event for event in events if event["arm"] == arm])
        for arm in sorted(ARMS)
    }
    phase_totals = {
        phase: {
            arm: _arm_summary([e for e in events if e["phase"] == phase and e["arm"] == arm])
            for arm in sorted(ARMS)
        }
        for phase in sorted(PHASES)
    }
    continuity = {}
    for arm in sorted(ARMS):
        arm_events = [e for e in events if e["phase"] == "continuity" and e["arm"] == arm]
        donor = _arm_summary([e for e in arm_events if e["participant"] == "donor"])
        recipient = _arm_summary([e for e in arm_events if e["participant"] == "recipient"])
        continuity[arm] = {
            "donor": donor,
            "recipient": recipient,
            "combined_agent_visible_bytes": donor["agent_visible_bytes"]
            + recipient["agent_visible_bytes"],
            "combined_service_read_bytes": donor["service_read_bytes"]
            + recipient["service_read_bytes"],
        }
    paired = []
    for pair_id in sorted({event["pair_id"] for event in events}):
        left = [e for e in groups.get((pair_id, "B"), []) if e["phase"] == "warm-query"]
        right = [e for e in groups.get((pair_id, "C"), []) if e["phase"] == "warm-query"]
        if left and right:
            b = _arm_summary(left)
            c = _arm_summary(right)
            paired.append(
                {
                    "pair_id": pair_id,
                    "agent_visible_reduction_fraction": None
                    if b["agent_visible_bytes"] == 0
                    else (b["agent_visible_bytes"] - c["agent_visible_bytes"])
                    / b["agent_visible_bytes"],
                    "service_read_reduction_fraction": None
                    if b["service_read_bytes"] == 0
                    else (b["service_read_bytes"] - c["service_read_bytes"])
                    / b["service_read_bytes"],
                    "combined_donor_recipient_visible_reduction_fraction": _continuity_reduction(
                        events, pair_id
                    ),
                    "quality_gate": "not_scored_by_accounting_tool",
                }
            )
    reductions = [
        p["agent_visible_reduction_fraction"]
        for p in paired
        if p["agent_visible_reduction_fraction"] is not None
    ]
    median_reduction = statistics.median(reductions) if reductions else None
    return {
        "schema": "knowledge-evaluation-accounting-v1",
        "interpretation": (
            "Agent-visible bytes are the primary reported context measure. Service bytes and "
            "donor-plus-recipient continuity totals are separate sensitivity views; the frozen "
            "wording leaves these interpretations ambiguous."
        ),
        "acceptance": (
            "not_assessed_without_independent_quality_labels_and_native_workflow_evidence"
        ),
        "adapter": adapter,
        "arms": summary,
        "phases": phase_totals,
        "cold_and_amortized_query_costs": _query_costs(events),
        "continuity": continuity,
        "pairs": paired,
        "median_agent_visible_reduction_fraction": median_reduction,
        "proposed_20_percent_threshold_met_on_accounting_only": bool(
            median_reduction is not None and median_reduction >= 0.20
        ),
        "integrity_violations": {arm: summary[arm]["integrity_violations"] for arm in sorted(ARMS)},
        "notes": [
            "Bytes are not token estimates.",
            "Unique source spans are counted once by snapshot/path/span identity.",
            "Unknown token or timing telemetry remains null.",
            "This report does not establish quality, authority safety, real-provider use "
            "or mission acceptance.",
        ],
    }


def _continuity_reduction(events: list[dict], pair_id: str) -> float | None:
    totals = {}
    for arm in ARMS:
        totals[arm] = sum(
            len(e["visible_text"].encode("utf-8"))
            for e in events
            if e["pair_id"] == pair_id
            and e["arm"] == arm
            and e["phase"] == "continuity"
            and e["participant"] in {"donor", "recipient"}
        )
    if totals["B"] == 0:
        return None
    return (totals["B"] - totals["C"]) / totals["B"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "events", type=Path, help="JSONL event export from a declared evaluator adapter"
    )
    parser.add_argument("--adapter-version", required=True)
    parser.add_argument("--adapter-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = build_report(
        read_events(args.events),
        adapter={"version": args.adapter_version, "sha256": args.adapter_sha256},
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
