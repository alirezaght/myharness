"""M2 participant ledger and presentation boundary; caller holds the adapter's shared lock."""

import json

from .method import RESERVE
from .package import BUDGETS

REFUSAL = (
    "Evaluation retrieval exhausted. Stop retrieval; obtain fresh await_assignment and submit "
    "one sourced partial answer.md identifying unknown gaps. Do not retry submission."
)


def error_result(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def delivered_size(result, listing=False):
    if listing:
        # An empty tool list contains no tool metadata/text; its framing is unmeasured.
        return 0 if result == {"tools": []} else len(json.dumps(result).encode())
    return sum(
        len(c["text"].encode()) for c in result.get("content", []) if c.get("type") == "text"
    )


class Budget:
    def __init__(self, donor=False):
        self.donor = donor
        self.attempted = self.admitted = self.denied = self.listings = 0
        self.forwarded = self.delivered = self.internal_operations = 0
        self.errors_delivered = 0
        self.phase = "retrieval"
        self.sequence = "assignment"
        self.events = []
        self.active = None

    def classify(self, params):
        name, args = params.get("name"), params.get("arguments", {})
        if name == "await_assignment":
            return "assignment"
        if name == "submit_output":
            return "output"
        if (
            name == "read_file"
            and isinstance(args, dict)
            and (args.get("path") == "evaluation:complete-donor")
        ):
            return "completion"
        return "data"

    def begin(self, params):
        self.attempted += 1
        kind = self.classify(params)
        self.active = {"attempt": self.attempted, "kind": kind}
        if self.attempted > BUDGETS["tool_calls"]:
            return "over-limit-attempt"
        if self.phase == "terminal":
            return "control-budget-exhausted"
        reserve = RESERVE["donor_final_calls" if self.donor else "final_calls"]
        if self.phase == "retrieval" and self.attempted > BUDGETS["tool_calls"] - reserve:
            self.phase = "completion"
            return "completion-transition"
        if self.phase == "completion" and kind != self.sequence:
            return "completion-sequence-required"
        if kind == "completion" and not self.donor:
            return "completion-unavailable"
        self.forwarded += 1
        return None

    def refusal(self, reason):
        self.active["outcome"] = reason
        return error_result(REFUSAL)

    def present(self, result, *, listing=False, listing_error=False):
        kind = "listing" if listing or self.active is None else self.active["kind"]
        is_error = listing_error or bool(result.get("isError"))
        # No data or repeated listing may borrow final control capacity.
        evidence = not is_error and (
            kind in {"data", "listing"} or kind == "assignment" and self.phase == "retrieval"
        )
        ceiling = BUDGETS["delivered_bytes"] - (RESERVE["completion_bytes"] if evidence else 0)
        if not evidence:
            ceiling -= max(0, RESERVE["error_bytes"] - self.errors_delivered)
        size = delivered_size(result, listing)
        if self.delivered + size > ceiling:
            if evidence:
                self.phase = "completion"
                reason = "byte-budget-transition"
            else:
                self.phase = "terminal"
                reason = "control-budget-exhausted"
            if self.active is not None:
                self.active["outcome"] = reason
            else:
                self.events.append({"kind": "listing", "outcome": reason})
            result = (
                {"tools": [], "_meta": {"evaluation_error": REFUSAL}}
                if listing
                else error_result(REFUSAL)
            )
            is_error = True
            size = delivered_size(result, listing)
        if is_error:
            remaining = min(
                RESERVE["error_bytes"] - self.errors_delivered,
                BUDGETS["delivered_bytes"] - self.delivered,
            )
            if size > remaining:
                result, size = ({"tools": []} if listing else error_result("")), 0
            self.errors_delivered += size
        self.delivered += size
        if listing:
            self.events.append({"kind": "listing", "delivered_bytes": size})
        else:
            if self.active is not None:
                self.active.update(delivered_bytes=size, is_error=is_error)
        return result

    def finish(self, result):
        failed = result.get("isError", False)
        self.denied += int(failed)
        self.admitted += int(not failed)
        if not failed and self.phase == "completion":
            kind = self.active["kind"]
            self.sequence = {
                "assignment": "output",
                "output": ("completion" if self.donor else "finished"),
                "completion": "finished",
            }.get(kind, self.sequence)
        self.events.append(self.active)
        self.active = None

    def receipt(self):
        return {
            "attempted_tools_call": self.attempted,
            "admitted_requests": self.admitted,
            "denied_requests": self.denied,
            "forwarded_requests": self.forwarded,
            "tools_list_requests": self.listings,
            "internal_operations": self.internal_operations,
            "delivered_bytes": self.delivered,
            "error_bytes": self.errors_delivered,
            "phase": self.phase,
            "completion_sequence": self.sequence,
            "events": self.events,
            "byte_scope": "UTF-8 tool text once; full serialized filtered tools/list metadata",
        }


def request(adapter, connection, original, message):
    """Wrap actual original verifier/claim/dispatcher ordering, with no raw auth/args retained."""
    with adapter.lock:
        ledger = adapter.budget
        envelope = (
            isinstance(message, dict)
            and message.get("jsonrpc") == "2.0"
            and isinstance(message.get("params", {}), dict)
        )
        if not envelope:
            ledger.events.append({"kind": "transport-diagnostic", "outcome": "malformed-envelope"})
            raise ValueError("well-formed MCP envelope required")
        method = message.get("method")
        if method not in {"tools/call", "tools/list"}:
            return original(message)
        listing = method == "tools/list"
        if listing:
            ledger.listings += 1
            reason = "listing-after-transition" if ledger.phase != "retrieval" else None
        else:
            reason = ledger.begin(message.get("params", {}))
        try:
            adapter.check_time()
            if reason:
                result = error_result(REFUSAL) if listing else ledger.refusal(reason)
            else:
                result = original(message)
                if listing:
                    result["tools"] = [t for t in result["tools"] if t["name"] in adapter.allowed]
        except (ValueError, TypeError, KeyError, OSError) as exc:
            result = error_result(str(exc)[:2048])
        listing_error = listing and "tools" not in result
        if listing_error:
            text = result["content"][0]["text"]
            result = {"tools": [], "_meta": {"evaluation_error": text}}
        result = ledger.present(result, listing=listing, listing_error=listing_error)
        if listing_error:
            ledger.events.append(
                {"kind": "listing", "outcome": reason or "listing-refused", "is_error": True}
            )
        if not listing:
            ledger.finish(result)
            adapter.calls = ledger.attempted
            if adapter.output_state.get("output_committed") and not result.get("isError"):
                if message["params"].get("name") == "submit_output":
                    adapter.output_state["acknowledgment_delivered"] = True
            if (
                adapter.completion
                and not result.get("isError")
                and ledger.events[-1]["kind"] == "completion"
            ):
                adapter.completion_acknowledged = True
        adapter.delivered = ledger.delivered
        return result
