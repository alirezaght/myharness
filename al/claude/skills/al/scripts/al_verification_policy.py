"""Versioned managed verification inputs, independent of caller environment names."""
from contextlib import contextmanager
import os


DEFAULT_STDIN = object()


def execution_policy():
    return {"version": 2, "default_stdin": "closed-pipe"}


@contextmanager
def verification_input(kwargs):
    """Own the default EOF pipe through launch; explicit standalone streams win."""
    result = dict(kwargs)
    if result.get("stdin", DEFAULT_STDIN) is not DEFAULT_STDIN:
        yield result
        return
    reader, writer = os.pipe()
    try:
        os.close(writer)
        writer = None
        result["stdin"] = reader
        yield result
    finally:
        try:
            if writer is not None:
                os.close(writer)
        finally:
            os.close(reader)
