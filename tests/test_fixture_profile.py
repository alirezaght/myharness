"""Lazy fixture imports preserve real setup, isolation and database teardown."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_fixture_collection_does_not_import_application_helpers(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc
import importlib.util
import sys
class RefuseApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'harness.controller', 'harness.db',
                        'tests.native_provider_fixture', 'tests.sample_repo'}:
            raise RuntimeError('collection imported application fixture helper: '+fullname)
sys.meta_path.insert(0, RefuseApplicationImports())
spec = importlib.util.spec_from_file_location('actual_root_fixtures', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert all(hasattr(module, name) for name in
           ('isolated_home', 'db_path', 'conn', 'controller', 'sample_repo'))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(root / "tests/conftest.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_real_fixture_setup_is_isolated_and_database_teardown_closes_connection(
    isolated_home, db_path, conn, controller, sample_repo, tmp_path, monkeypatch
):
    assert os.environ["HARNESS_HOME"] == str(isolated_home)
    assert "HARNESS_DB" not in os.environ
    assert Path(os.environ["PATH"].split(os.pathsep)[0]) == tmp_path / "native-provider-bin"
    assert (tmp_path / "native-provider-bin/claude").is_file()
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert controller.create_task("Exercise actual lazy fixture setup").id
    assert sample_repo.is_dir() and (sample_repo / ".git").is_dir()
    from tests import conftest

    lifetime = conftest.conn.__wrapped__(db_path)
    connection = next(lifetime)
    with pytest.raises(StopIteration):
        next(lifetime)
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    original_home = os.environ.get("HOME")
    with monkeypatch.context() as local:
        local.setenv("HARNESS_DB", "must-be-cleared")
        home = conftest.isolated_home.__wrapped__(tmp_path / "another", local)
        assert os.environ["HARNESS_HOME"] == str(home)
        assert "HARNESS_DB" not in os.environ
        assert os.environ.get("HOME") == original_home
