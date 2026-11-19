"""Authoring rollback also covers a failed enclosing SQLite commit."""

from contextlib import contextmanager

from harness.db import transaction


@contextmanager
def committing(conn):
    try:
        with transaction(conn):
            yield
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
