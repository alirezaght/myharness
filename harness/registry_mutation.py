"""Exception/commit-failure compensation for exact registry paths owned by an operation."""

from contextlib import contextmanager
from pathlib import Path


class RegistryMutation:
    def __init__(self):
        self.before = {}
        self.changed = []

    def capture(self, path):
        path = Path(path)
        if path not in self.before:
            self.before[path] = path.read_bytes() if path.exists() else None
        return path

    def mark(self, path):
        path = Path(path)
        if path not in self.changed:
            self.changed.append(path)

    def create(self, path, content, create):
        path = self.capture(path)
        try:
            create(path, content)
        except FileExistsError:
            raise
        except BaseException:
            if self.before[path] is None and path.is_file():
                self.mark(path)
            raise
        self.mark(path)

    def merge(self, other):
        for path in other.changed:
            self.before.setdefault(path, other.before[path])
            self.mark(path)

    def rollback(self):
        for path in reversed(self.changed):
            previous = self.before[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous)


@contextmanager
def registry_mutation():
    mutation = RegistryMutation()
    try:
        yield mutation
    except BaseException:
        mutation.rollback()
        raise
