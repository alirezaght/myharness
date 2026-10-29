"""Prepared-mode client selection is installed only by the verified pinned bootstrap."""

import sys


def selected():
    context = sys.modules.get("_al_run_pin_context")
    return getattr(context, "initialization_client", None) if context else None


def initialize(transaction, supplied):
    client = selected()
    if client is None:
        raise ValueError("prepared initialization channel unavailable")
    result = client.request("initialize", supplied)
    if not isinstance(result, dict) or set(result) != {"transaction", "initialization"}:
        raise ValueError("prepared initialization response refused")
    current, initial = result["transaction"], result["initialization"]
    if (
        any(current[k] != transaction[k] for k in transaction if k != "initialization")
        or initial != dict(supplied, original_task_package=initial["original_task_package"])
        or supplied["original_task_package"]["sha256"] != initial["original_task_package"]["sha256"]
    ):
        raise ValueError("prepared initialization identity changed")
    client.transaction, client.initialization = current, initial
    return current, initial
