"""Strict bounded authoring messages and deterministic identities."""

import hashlib
import json
import re

MAX_BODY = 100000
MAX_CONTEXT = 524288
BASE_KEYS = {"project", "task", "generation", "source", "working", "graph", "authority"}


class AuthoringConflict(ValueError):
    def __init__(self, message, *, current_target=None):
        super().__init__(message)
        self.current_target = current_target


class AuthoringContextTooLarge(ValueError):
    pass


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, name, limit=16000, *, empty=False):
    if (
        not isinstance(value, str)
        or (not empty and not value.strip())
        or len(value.encode()) > limit
    ):
        raise ValueError(f"invalid {name}: expected text up to {limit} bytes")
    return value


def request_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", value):
        raise ValueError("invalid request_id")
    return value


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid fields: expected " + ", ".join(sorted(keys)))
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode(raw, limit=262144):
    if len(raw) > limit:
        raise ValueError("authoring payload too large")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")),
        )
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid bounded JSON") from error


def validate_context(value):
    encoded = canonical(value)
    if len(encoded.encode()) > MAX_CONTEXT:
        raise AuthoringContextTooLarge(
            "Complete authoring context cannot fit the native input limit; "
            "reduce effective inputs or split the task"
        )
    return encoded
