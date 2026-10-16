"""Lookup errors shared by the controller and the per-task workspace."""


class TaskNotFound(LookupError):
    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"unknown task: {task_id}")


class RecordNotFound(LookupError):
    def __init__(self, kind: str, record_id: int, task_id: str):
        self.kind = kind
        self.record_id = record_id
        self.task_id = task_id
        super().__init__(f"unknown {kind} {record_id} for task {task_id}")
