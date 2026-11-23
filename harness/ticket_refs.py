"""Tracker ticket references (e.g. ABC-1234) in a request: they name a ticket, not code."""

import re

TICKET_ID = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]+-\d+(?![\w-])")
# What may join ticket IDs in a request that carries nothing else.
_JOINERS = re.compile(r"(?:[\s,;+&/|]|\band\b)*", re.IGNORECASE)


def ticket_ids(request: str) -> list[str]:
    return list(dict.fromkeys(TICKET_ID.findall(request or "")))


def without_ticket_ids(request: str) -> str:
    return TICKET_ID.sub(" ", request or "")


def ticket_only(request: str) -> bool:
    """True when the request names ticket IDs and nothing but joiners (+, &, and, commas)."""
    return bool(ticket_ids(request)) and bool(_JOINERS.fullmatch(without_ticket_ids(request)))
