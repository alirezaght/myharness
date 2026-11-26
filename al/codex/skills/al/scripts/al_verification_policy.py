"""Versioned managed verification inputs, independent of caller environment names."""
import subprocess


def execution_policy():
    return {"version": 1, "default_stdin": "devnull"}


def verification_kwargs(kwargs):
    """Managed checks default to EOF; explicit standalone streams retain precedence."""
    result = dict(kwargs)
    result.setdefault("stdin", subprocess.DEVNULL)
    return result
