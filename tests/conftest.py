import os

import pytest


@pytest.fixture
def launched(tmp_path_factory):
    from al.tests.runtime_run_helpers import launched as isolated_launcher

    return isolated_launcher.__wrapped__(tmp_path_factory)


@pytest.fixture(scope="session", autouse=True)
def real_al_registry_guard():
    """Fail the session when a test writes run state into the real AL registry (#37)."""
    from al.tests.registry_guard import session_guard

    yield from session_guard()


@pytest.fixture(scope="session")
def global_alctl_shadow(tmp_path_factory):
    """PATH directories that hold the global `alctl`, mirrored without it (#37).

    The global launcher writes run pins into the real registry; a test reaches AL only
    through a launcher it bootstraps itself and puts on PATH."""
    from tests.al_launcher import path_without_global_alctl

    return path_without_global_alctl(tmp_path_factory.mktemp("path-without-alctl"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch, global_alctl_shadow):
    home = tmp_path / "home"
    monkeypatch.setenv("HARNESS_HOME", str(home))
    monkeypatch.delenv("HARNESS_DB", raising=False)
    # AL commands run in process unless a test opts into the resident AL service.
    monkeypatch.setenv("AL_SERVICE", "0")
    # No test may reach the global AL install or its registry (#37).
    monkeypatch.setenv("AL_DISTRIBUTION_STATE", str(tmp_path / "al-distribution" / "registry"))
    monkeypatch.setenv("HARNESS_ALCTL", str(tmp_path / "no-global-alctl" / "alctl"))
    monkeypatch.setenv("PATH", global_alctl_shadow(os.environ.get("PATH", "")))
    from tests.native_provider_fixture import isolated_provider_environment

    env = isolated_provider_environment(dict(os.environ), tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    return home


@pytest.fixture(autouse=True)
def al_code_facts(tmp_path, monkeypatch):
    """The knowledge graph's import edges are AL's code facts (#34): answer them from this
    checkout's AL analyser instead of an installed launcher. A test of the degraded graph
    replaces `harness.knowledge.al_facts.resolve` itself."""
    from tests.al_facts_support import source_facts

    monkeypatch.setattr(
        "harness.knowledge.al_facts.resolve",
        lambda repository, **_: source_facts(repository, tmp_path / "al-analysis-index"),
    )


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
