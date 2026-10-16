import pytest

from harness.controller import HarnessController
from harness.db import connect
from tests.sample_repo import make_sample_repo


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HARNESS_HOME", str(home))
    monkeypatch.delenv("HARNESS_DB", raising=False)
    return home


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "state" / "state.db"


@pytest.fixture
def conn(db_path):
    connection = connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def controller(conn):
    return HarnessController(conn)


@pytest.fixture
def sample_repo(tmp_path):
    return make_sample_repo(tmp_path / "sample")
