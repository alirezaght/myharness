"Immutable call ownership, explicitly captured into process hooks before threading."

from contextlib import contextmanager
from contextvars import ContextVar

CURRENT = ContextVar("policy_attempt", default=None)


@contextmanager
def scope(attempt):
    token = CURRENT.set(attempt)
    try:
        yield attempt
    finally:
        CURRENT.reset(token)


def start_effect():
    attempt = CURRENT.get()
    if attempt and not attempt.started:
        attempt.start()


def process_hooks():
    attempt = CURRENT.get()
    if attempt is None:
        return None
    from harness.policy.processes import ProcessHooks

    return ProcessHooks(attempt)


def check_deadline():
    import time

    from harness.policy.contracts import require

    attempt = CURRENT.get()
    if attempt and attempt.deadline is not None:
        require(time.monotonic() < attempt.deadline, "BUDGET_EXHAUSTED")
