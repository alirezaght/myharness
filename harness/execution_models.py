"""Execution states remain separate from the pre-execution approval phase."""

TERMINAL = frozenset({"Completed", "Failed"})
RECOVERABLE = frozenset({"Unavailable", "Interrupted", "RecoveryRequired"})


class ExecutionConflict(ValueError):
    pass
