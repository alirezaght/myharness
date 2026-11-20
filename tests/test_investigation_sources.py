"""Scope is enforced before dispatch and repository evidence remains confined."""

from dataclasses import replace

import pytest

from harness.authoring import AuthoringService
from harness.investigation_policy import (
    AcquisitionError,
    authorize_request,
    validate_effective_config,
)
from harness.investigation_policy import capability as load_capability
from harness.investigation_repository import Reader, recheck_observation
from tests.investigation_fixture import native_fixture as native_fixture


def test_unknown_bootstrap_or_wrong_site_never_dispatches(controller, tmp_path, native_fixture):
    qualified, _, calls = native_fixture
    task = controller.create_task("AR-2906", "trivial")
    service = AuthoringService(controller, tmp_path, task.id)
    with pytest.raises(AcquisitionError):
        authorize_request(service, qualified=replace(qualified, bootstrap_qualified=False))
    with pytest.raises(AcquisitionError):
        authorize_request(service, "foreign-cloud", qualified)
    assert calls() == []


@pytest.mark.parametrize(
    "issue_request",
    [
        "AR-2906 AR-2907",
        "https://one.atlassian.net/browse/AR-2906?expand=all",
        "https://user:secret@one.atlassian.net/browse/AR-2906",
        "https://other.atlassian.net/browse/AR-2906",
    ],
)
def test_scope_refusals_are_before_transport(controller, tmp_path, native_fixture, issue_request):
    qualified, _, calls = native_fixture
    task = controller.create_task(issue_request, "trivial")
    with pytest.raises(AcquisitionError):
        authorize_request(AuthoringService(controller, tmp_path, task.id), qualified=qualified)
    assert calls() == []


def test_inherited_foreign_server_or_header_helper_fails_closed(native_fixture):
    import copy

    qualified, _, calls = native_fixture
    for tamper in ("foreign", "header"):
        config = copy.deepcopy(qualified.config)
        if tamper == "foreign":
            config["mcp_servers"]["foreign"]["enabled"] = True
        else:
            config["mcp_servers"]["atlassian"]["http_headers_helper"] = "execute-me"
        with pytest.raises(AcquisitionError):
            validate_effective_config(config, qualified)
    assert calls() == []


def test_confined_reader_refuses_aliases_and_rechecks_sources(tmp_path):
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("export_csv = True\n")
    (repo / ".env").write_text("SECRET=not-readable\n")
    (repo / "alias.py").symlink_to(repo / "app.py")
    os.link(repo / "app.py", repo / "hard.py")
    reader = Reader(repo)
    for path in ("../outside", "/absolute", ".env", "alias.py", "hard.py"):
        with pytest.raises((ValueError, OSError)):
            reader.read(path)
    (repo / "hard.py").unlink()
    observation = Reader(repo).observe("CSV export")
    assert all(s["locator"] != ".env" for s in observation["sources"])
    (repo / "app.py").write_text("export_csv = False\n")
    with pytest.raises(AcquisitionError, match="changed"):
        recheck_observation(repo, observation)


def test_budget_exhaustion_is_explicit_incomplete_observation(tmp_path):
    (tmp_path / "one.py").write_text("a" * 100)
    result = Reader(tmp_path, {"bytes": 10}).observe("export")
    assert not result["complete"] and result["limitations"]
    assert result["counts"]["bytes"] <= 10


def test_quote_hash_and_range_forgeries_are_rejected(native_fixture):
    import copy

    from harness.interview_coverage import validate_evidence
    from harness.investigation_repository import source_record

    source = source_record(
        "ticket", "https://one.atlassian.net/browse/AR-2906", "Actual requirement", {}
    )
    citation = {
        "source": source["id"],
        "sha256": source["sha256"],
        "start": 0,
        "end": 6,
        "quote": "Actual",
    }
    value = {
        "coverage": [
            {
                "key": "business_requirements.goal",
                "disposition": "fact",
                "statement": "Requirement",
                "rationale": "Quoted evidence",
                "citations": [citation],
            }
        ],
        "decisions": [],
        "reads": [],
    }
    validate_evidence(value, [source])
    for key, changed in [
        ("source", "foreign-source"),
        ("sha256", "0" * 64),
        ("end", 999),
        ("quote", "Invented"),
    ]:
        invalid = copy.deepcopy(value)
        invalid["coverage"][0]["citations"][0][key] = changed
        with pytest.raises(ValueError, match="citation"):
            validate_evidence(invalid, [source])


def test_receipt_loader_binds_bootstrap_inputs_and_refuses_environment_drift(
    tmp_path, native_fixture, monkeypatch
):
    import json
    import os
    from dataclasses import asdict
    from pathlib import Path

    from harness.investigation_policy import BOOTSTRAP_ENV

    # This is the actual loader function, not the injected capability provider.

    qualified, _, calls = native_fixture
    path = Path(os.environ["HARNESS_HOME"]) / "investigation/qualification.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "capability": asdict(qualified),
                "evidence": {key: "0" * 64 for key in ("bootstrap", "rpc", "scope", "tools")},
            }
        )
    )
    assert load_capability().identity == qualified.identity
    original = json.loads(path.read_text())
    legacy = json.loads(path.read_text())
    legacy["schema_version"] = 1
    path.write_text(json.dumps(legacy))
    assert load_capability() is None
    path.write_text(json.dumps(original))
    missing_proof = json.loads(path.read_text())
    del missing_proof["capability"]["effective_config_digests"]
    path.write_text(json.dumps(missing_proof))
    assert load_capability() is None
    path.write_text(json.dumps(original))
    missing_tools = json.loads(path.read_text())
    del missing_tools["evidence"]["tools"]
    path.write_text(json.dumps(missing_tools))
    assert load_capability() is None
    path.write_text(json.dumps(original))
    for name in (*BOOTSTRAP_ENV, "CODEX_HOME"):
        with monkeypatch.context() as patch:
            patch.setenv(name, "" if name == "CODEX_HOME" else "synthetic-value")
            assert load_capability() is None
    environments = Path(qualified.bootstrap["native_home"]) / "environments.toml"
    environments.write_text("unexpected = true\n")
    assert load_capability() is None
    environments.unlink()
    Path(qualified.bootstrap["config_inputs"][0]["path"]).write_text("drift = true\n")
    assert load_capability() is None
    assert calls() == []


def test_additive_schema_reader_distinguishes_absent_and_partial_without_migration():
    import sqlite3

    from harness.investigation_store import schema_available

    conn = sqlite3.connect(":memory:")
    try:
        assert schema_available(conn) is False
        assert conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0] == 0
        conn.execute("CREATE TABLE investigation_bindings(task_id TEXT)")
        with pytest.raises(sqlite3.OperationalError, match="Partial"):
            schema_available(conn)
    finally:
        conn.close()


def test_large_native_executable_uses_bounded_streaming_hash(native_fixture, monkeypatch):
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace

    from harness.investigation_policy import validate_capability

    qualified, _, _ = native_fixture
    size = 284118160
    chunk = b"\0" * 65536
    expected = hashlib.sha256()
    for _ in range(size // len(chunk)):
        expected.update(chunk)
    expected.update(chunk[: size % len(chunk)])
    value = replace(qualified, executable=dict(qualified.executable, sha256=expected.hexdigest()))
    original_stat, original_open = Path.stat, Path.open
    native = value.executable["path"]
    metadata = {"size": size}
    opened = []

    class Stream:
        def __init__(self):
            self.remaining = size

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == 65536
            count = min(self.remaining, limit)
            self.remaining -= count
            return chunk[:count]

    def observed_stat(path, *args, **kwargs):
        if str(path) == native:
            return SimpleNamespace(
                st_size=metadata["size"], st_dev=1, st_ino=1, st_mtime_ns=1, st_ctime_ns=1
            )
        return original_stat(path, *args, **kwargs)

    def observed_open(path, *args, **kwargs):
        if str(path) == native:
            opened.append(True)
            return Stream()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", observed_stat)
    monkeypatch.setattr(Path, "open", observed_open)
    assert validate_capability(value, observe=True) is value
    assert len(opened) == 1
    metadata["size"] = 512 * 1024 * 1024 + 1
    with pytest.raises(AcquisitionError):
        validate_capability(value, observe=True)
    assert len(opened) == 1


def test_minimal_launch_is_separate_from_qualified_effective_defaults(native_fixture):
    import copy

    from harness.authoring_contract import digest

    qualified, _, _ = native_fixture
    effective = copy.deepcopy(qualified.config)
    effective["agents"]["max_threads"] = 6
    effective["mcp_servers"]["foreign"]["env"] = {"TOKEN": "synthetic-private-sentinel"}
    groups = ("sandbox_mode", "approval_policy", "agents", "web_search", "features", "mcp_servers")
    qualified = replace(
        qualified,
        effective_config_digests={
            "schema_version": 1,
            "groups": {key: digest(effective[key]) for key in groups},
        },
    )
    validate_effective_config(effective, qualified)
    assert "synthetic-private-sentinel" not in str(qualified.config)


def test_adapter_observes_defaults_without_exporting_inherited_secrets(
    controller, tmp_path, native_fixture
):
    import copy
    import json
    from dataclasses import asdict
    from pathlib import Path

    from harness.investigation_native import NativeInvestigator
    from harness.investigation_policy import effective_config_digests, launch_argv
    from tests.test_investigation_native import request

    qualified, _, calls = native_fixture
    path = Path(qualified.executable["path"] + ".json")
    settings = json.loads(path.read_text())
    effective = copy.deepcopy(settings["config"])
    effective["agents"]["max_threads"] = 6
    effective["mcp_servers"]["foreign"]["env"] = {"TOKEN": "synthetic-private-sentinel"}
    qualified = replace(qualified, effective_config_digests=effective_config_digests(effective))
    settings["config"] = effective
    path.write_text(json.dumps(settings))
    result = NativeInvestigator(qualified).acquire(request(controller, tmp_path, qualified))
    assert result["issue"] == "AR-2906"
    assert "synthetic-private-sentinel" not in json.dumps(
        [asdict(qualified), launch_argv(qualified), calls(), result]
    )
    with pytest.raises(AcquisitionError):
        validate_effective_config(qualified.config, qualified)


@pytest.mark.parametrize("change", ["default", "new-default", "new-server", "missing-group"])
def test_effective_drift_refuses_before_thread(controller, tmp_path, native_fixture, change):
    import json
    from pathlib import Path

    from harness.investigation_native import NativeInvestigator
    from tests.test_investigation_native import request

    qualified, _, calls = native_fixture
    path = Path(qualified.executable["path"] + ".json")
    data = json.loads(path.read_text())
    if change == "default":
        data["config"]["agents"]["enabled"] = True
    elif change == "new-default":
        data["config"]["agents"]["max_threads"] = 9
    elif change == "new-server":
        data["config"]["mcp_servers"]["new"] = {"enabled": False, "command": "/usr/bin/false"}
    else:
        del data["config"]["web_search"]
    path.write_text(json.dumps(data))
    with pytest.raises(AcquisitionError):
        NativeInvestigator(qualified).acquire(request(controller, tmp_path, qualified))
    assert not any(c.get("method") == "thread/start" for c in calls())


@pytest.mark.parametrize("change", ["foreign", "transport", "header", "endpoint", "tools", "type"])
def test_matching_fingerprints_never_override_safety(native_fixture, change):
    import copy

    from harness.investigation_policy import effective_config_digests

    qualified, _, _ = native_fixture
    config = copy.deepcopy(qualified.config)
    if change == "foreign":
        config["mcp_servers"]["foreign"]["enabled"] = True
    elif change == "transport":
        config["mcp_servers"]["foreign"]["command"] = "/bin/sh"
    elif change == "header":
        config["mcp_servers"]["atlassian"]["http_headers"] = {"Authorization": "synthetic-secret"}
    elif change == "endpoint":
        config["mcp_servers"]["atlassian"]["url"] = "https://evil.example/mcp"
    elif change == "tools":
        config["mcp_servers"]["atlassian"]["enabled_tools"] += ["writeJira"]
    else:
        config["features"]["plugins"] = 0
    value = replace(qualified, effective_config_digests=effective_config_digests(config))
    with pytest.raises(AcquisitionError):
        validate_effective_config(config, value)


@pytest.mark.parametrize(
    "change",
    [
        "defaults",
        "credentials",
        "activation",
        "marker",
        "missing-digests",
        "digest-key",
        "digest-type",
        "foreign-type",
    ],
)
def test_malformed_receipt_refuses_before_launch(native_fixture, change):
    import copy

    from harness.investigation_policy import launch_argv

    qualified, _, calls = native_fixture
    config, bootstrap, proof = (
        copy.deepcopy(qualified.config),
        copy.deepcopy(qualified.bootstrap),
        copy.deepcopy(qualified.effective_config_digests),
    )
    if change == "defaults":
        config["agents"]["max_threads"] = 6
    elif change == "credentials":
        config["mcp_servers"]["foreign"]["env"] = {"TOKEN": "synthetic-secret"}
    elif change == "activation":
        config["remote-control"] = True
    elif change == "foreign-type":
        config["mcp_servers"]["foreign"]["enabled"] = 0
    elif change == "marker":
        bootstrap["child_environment"] = {}
    elif change == "missing-digests":
        proof = None
    elif change == "digest-key":
        del proof["groups"]["agents"]
    else:
        proof["schema_version"] = True
    with pytest.raises(AcquisitionError):
        launch_argv(
            replace(qualified, config=config, bootstrap=bootstrap, effective_config_digests=proof)
        )
    assert calls() == []


def test_real_empty_and_whitespace_repository_sources_preserve_bytes(tmp_path):
    import hashlib

    from harness.interview_coverage import validate_sources

    (tmp_path / "__init__.py").write_bytes(b"")
    (tmp_path / "whitespace.py").write_bytes(b" \n\t")
    (tmp_path / "export.py").write_text("export_csv = True\n")
    sources = Reader(tmp_path).observe("CSV export")["sources"]
    validated = validate_sources(sources)
    by_path = {s["locator"]: s for s in validated.values()}
    assert by_path["__init__.py"]["content"] == ""
    assert by_path["whitespace.py"]["content"] == " \n\t"
    assert by_path["__init__.py"]["sha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize(
    "kind,content",
    [("ticket", ""), ("ticket", " \n"), ("repository", None), ("repository", "x" * 262145)],
)
def test_empty_source_fix_keeps_ticket_type_and_budget_limits(kind, content):
    from harness.interview_coverage import validate_sources
    from harness.investigation_repository import source_record

    row = source_record(kind, "fixture", "temporary")
    row["content"] = content
    with pytest.raises(ValueError):
        validate_sources([row])
