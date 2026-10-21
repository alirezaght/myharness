import os

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HARNESS_HOME", str(home))
    monkeypatch.delenv("HARNESS_DB", raising=False)
    from tests.native_provider_fixture import isolated_provider_environment

    env = isolated_provider_environment(dict(os.environ), tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    return home


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "state" / "state.db"


@pytest.fixture
def conn(db_path):
    from harness.db import connect

    connection = connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def controller(conn):
    from harness.controller import HarnessController

    return HarnessController(conn)


@pytest.fixture
def sample_repo(tmp_path):
    from tests.sample_repo import make_sample_repo

    return make_sample_repo(tmp_path / "sample")
