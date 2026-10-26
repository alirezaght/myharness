"""Focused effect and secret boundaries for Phase 4 core adapters."""

import sqlite3

import pytest

from harness.command_safety import classify_command
from harness.diagnostics_db import query_sqlite
from harness.execution_logs import StreamRedactor, redact_value
from harness.gitlab import GitLabClient


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "-c", "core.pager=touch /tmp/bad", "log"],
        ["git", "--config-env=core.sshCommand=EVIL", "log"],
        ["env", "git", "status"],
        ["sh", "-c", "git status"],
        ["git", "show", "--ext-diff"],
        ["git", "status", "--untracked-files=all"],
    ],
)
def test_unknown_effects_are_never_automatic(tmp_path, argv):
    assert classify_command({"argv": argv, "cwd": str(tmp_path)})["decision"] == "approval_required"


def test_sqlite_enforces_capability_beyond_select(tmp_path):
    path = tmp_path / "target.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items(value)")
        conn.execute("INSERT INTO items VALUES ('one')")
    assert query_sqlite(path, "SELECT value FROM items")["rows"] == [["one"]]
    for query in (
        "DELETE FROM items",
        "PRAGMA writable_schema=ON",
        "ATTACH ':memory:' AS x",
        "SELECT load_extension('/tmp/plugin')",
        "SELECT randomblob(100000000)",
    ):
        with pytest.raises(ValueError):
            query_sqlite(path, query)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_redaction_nested_and_chunk_boundary():
    assert "sensitive" not in str(redact_value({"PRIVATE-TOKEN": "sensitive"}))
    stream = StreamRedactor(["long-sensitive-value"])
    assert stream.feed("long-sensitive-") == ""
    assert "sensitive" not in stream.feed("value\n")


def test_json_credentials_and_streamed_private_keys_are_not_retained():
    from harness.execution_logs import redact

    for value in (
        '{"access_token": "sensitive-value"}',
        '{"password": "sensitive with spaces"}',
        '{"private_key": "sensitive\\nvalue"}',
    ):
        assert "sensitive" not in redact(value)
    stream = StreamRedactor()
    chunks = [
        "-----BEGIN RSA PRIV",
        "ATE KEY-----\n",
        "sensitive-key-material\n",
        "-----END RSA PRIVATE KEY-----\n",
        "ordinary diagnostic\n",
    ]
    output = "".join(stream.feed(c) for c in chunks) + stream.feed("", final=True)
    assert "sensitive" not in output and "ordinary diagnostic" in output


@pytest.mark.parametrize(
    "endpoint", ["http://gitlab.example", "https://user:password@host", "https://host?token=x"]
)
def test_gitlab_rejects_unsafe_endpoints(endpoint):
    with pytest.raises(ValueError):
        GitLabClient({"endpoint": endpoint, "project": "group/project"})
