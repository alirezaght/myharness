"""Immutable call-scoped parent correlation, propagated before each actual spawn."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

CURRENT = ContextVar("reliability_process_parent", default=None)


@dataclass(frozen=True)
class ProcessContext:
    database: Path
    execution_id: str
    epoch_id: str
    authority_generation: int
    observation_id: str
    principal_id: str


@contextmanager
def dispatch_scope(operation, observation_id, principal_id):
    context = (
        ProcessContext(
            operation.database,
            operation.execution_id,
            operation.epoch_id,
            operation.authority_generation,
            observation_id,
            principal_id,
        )
        if observation_id
        else None
    )
    token = CURRENT.set(context)
    try:
        yield context
    finally:
        CURRENT.reset(token)
