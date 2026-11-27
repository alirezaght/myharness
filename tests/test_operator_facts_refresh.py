"""Standalone public Knowledge refresh uses installed AL facts without an AL run."""

import hashlib
import json
import subprocess
from pathlib import Path

from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.models import CODE_FACTS_EXTRACTOR
from tests.al_facts_support import (
    owned_installed_launcher,
    owned_launcher_runner,
    private_git,
    real_resolve,
    tree_state,
)
from tests.test_knowledge_consumers import FILES


def _active_code_facts(connection):
    return {
        (row[0], row[1], row[2])
        for row in connection.execute(
            "SELECT s.locator,a.predicate,o.scoped_key "
            "FROM knowledge_projects p "
            "JOIN knowledge_snapshot_sources m ON m.snapshot_id=p.active_snapshot_id "
            "AND m.membership_state='present' "
            "JOIN knowledge_source_versions v ON v.version_id=m.version_id "
            "JOIN knowledge_sources s ON s.source_id=v.source_id "
            "JOIN knowledge_evidence e ON e.version_id=v.version_id "
            "JOIN knowledge_assertions a USING(assertion_id) "
            "JOIN knowledge_entities o ON o.entity_id=a.object_id "
            "WHERE a.predicate IN ('imports','serves-endpoint','calls-endpoint')"
        )
    }


def _present_code_sources(connection):
    return {
        row[0]: (row[1], row[2], row[3])
        for row in connection.execute(
            "SELECT s.locator,v.version_id,v.content_sha256,v.extractor_version "
            "FROM knowledge_projects p "
            "JOIN knowledge_snapshot_sources m ON m.snapshot_id=p.active_snapshot_id "
            "AND m.membership_state='present' "
            "JOIN knowledge_source_versions v ON v.version_id=m.version_id "
            "JOIN knowledge_sources s ON s.source_id=v.source_id"
        )
    }


def _git_blob_sha1(contents):
    return hashlib.sha1(b"blob " + str(len(contents)).encode() + b"\0" + contents).hexdigest()


def _protected_runtime_state(home, repository):
    # Pin storage is derived by validator_run_pin from the managed snapshot's issuer root;
    # run bindings live under its `al-run-pins/runs` child.
    paths = (
        Path(home) / "installer" / "al-run-pins",
        Path(repository) / ".codex" / "al-runtime",
        Path(repository) / ".claude" / "al-runtime",
    )
    return tuple((str(path), tree_state(path) if path.exists() else None) for path in paths)


def test_public_refresh_publishes_installed_operator_facts_without_run(tmp_path, monkeypatch):
    from harness import cli_knowledge
    from harness.knowledge import al_facts

    _, home, repository, launcher, environment, bootstrap_owners = owned_installed_launcher(
        tmp_path / "managed-launcher"
    )
    owners = []
    command_artifacts = tmp_path / "git-commands"
    for name, contents in FILES.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    private_git(repository, command_artifacts, owners, "init", "-q", "--initial-branch=main")
    private_git(repository, command_artifacts, owners, "add", ".")
    private_git(
        repository,
        command_artifacts,
        owners,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--no-gpg-sign",
        "-qm",
        "operator facts source",
    )
    private_git(
        repository,
        command_artifacts,
        owners,
        "config",
        "remote.origin.url",
        "https://example.invalid/inert",
    )
    private_git(
        repository,
        command_artifacts,
        owners,
        "config",
        "--add",
        "remote.origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )

    run_owners = []
    monkeypatch.setenv("HARNESS_ALCTL", str(launcher))
    monkeypatch.setattr(al_facts, "resolve", real_resolve)
    monkeypatch.setattr(
        al_facts,
        "subprocess",
        type(
            "OwnedSubprocess",
            (),
            {
                "run": staticmethod(
                    owned_launcher_runner(tmp_path / "launcher-commands", run_owners, environment)
                ),
                "DEVNULL": subprocess.DEVNULL,
                "SubprocessError": subprocess.SubprocessError,
            },
        ),
    )

    database = tmp_path / "knowledge" / "state.db"
    connection = connect(database)
    try:
        controller = HarnessController(connection)
        runtime_before = _protected_runtime_state(home, repository)
        assert runtime_before[0][1] is None, "fixture must begin without AL pin storage"
        repository_before = tree_state(repository)
        result = cli_knowledge.refresh_knowledge(controller, repository)
        assert result["status"] == "published", result
        assert result["snapshot_id"]
        assert run_owners, "public refresh must use the protected installed launcher"
        assert tree_state(repository) == repository_before, (
            "operator refresh must preserve target source and Git state"
        )
    finally:
        connection.close()

    # Reopen the disposable DB through a second connection to prove persisted facts.
    reader = connect(database)
    try:
        facts = _active_code_facts(reader)
        initial_sources = _present_code_sources(reader)
        assert any(predicate == "imports" for _, predicate, _ in facts)
        assert any(predicate == "serves-endpoint" for _, predicate, _ in facts)
        assert any(predicate == "calls-endpoint" for _, predicate, _ in facts)
        assert any(
            locator.endswith("services/orders/src/application/api.py")
            for locator, predicate, _ in facts
            if predicate == "serves-endpoint"
        )
        assert any(
            locator.endswith("apps/web/src/pages/home.tsx")
            for locator, predicate, _ in facts
            if predicate == "calls-endpoint"
        )
    finally:
        reader.close()

    # A refresh after an edit plus a tracked deletion removes the old current edges.
    old_about = ("apps/web/src/pages/about.tsx", "imports", "apps/web/src/lib/format.ts")
    old_call = ("apps/web/src/pages/home.tsx", "calls-endpoint", "endpoint:/orders/{}")
    assert any(
        locator == old_about[0] and predicate == old_about[1] for locator, predicate, _ in facts
    ), "fixture must contain the deleted file's edge"
    assert any(
        locator == old_call[0] and predicate == old_call[1] for locator, predicate, _ in facts
    ), "fixture must contain the edited caller edge"
    edited_home = b"export const load = 1;\n"
    repository.joinpath("apps/web/src/pages/home.tsx").write_bytes(edited_home)
    private_git(
        repository,
        command_artifacts,
        owners,
        "rm",
        "--cached",
        "--",
        "apps/web/src/pages/about.tsx",
    )
    repository.joinpath("apps/web/src/pages/about.tsx").unlink()
    edited_repository = tree_state(repository)
    connection = connect(database)
    try:
        updated = cli_knowledge.refresh_knowledge(HarnessController(connection), repository)
        assert updated["status"] == "published", updated
        assert tree_state(repository) == edited_repository
    finally:
        connection.close()
    reader = connect(database)
    try:
        current = _active_code_facts(reader)
        current_sources = _present_code_sources(reader)
        assert not any(
            locator == old_about[0] and predicate == old_about[1]
            for locator, predicate, _ in current
        )
        assert not any(
            locator == old_call[0] and predicate == old_call[1] for locator, predicate, _ in current
        )
        old_home_version = initial_sources[old_call[0]][0]
        new_home_version, new_home_sha, new_home_extractor = current_sources[old_call[0]]
        assert new_home_version != old_home_version
        assert new_home_sha == hashlib.sha256(edited_home).hexdigest()
        assert new_home_extractor == CODE_FACTS_EXTRACTOR
        assert old_about[0] not in current_sources, (
            "deleted source must be absent from present rows"
        )
    finally:
        reader.close()

    runtime_after = _protected_runtime_state(home, repository)
    assert runtime_after == runtime_before, "standalone refresh must not create AL run/pin state"
    edited_oid = _git_blob_sha1(edited_home)
    matching_documents = []
    for stdout_path in sorted((tmp_path / "launcher-commands").glob("*/stdout")):
        try:
            document = json.loads(stdout_path.read_bytes())
        except (ValueError, UnicodeError):
            continue
        if (
            isinstance(document, dict)
            and document.get("schema") == 1
            and old_call[0] in document.get("files", {})
        ):
            matching_documents.append(document)
    initial_oid = _git_blob_sha1(FILES[old_call[0]].encode())
    initial_documents = [
        document for document in matching_documents if document["files"][old_call[0]] == initial_oid
    ]
    assert initial_documents, "initial source blob must be observed through the installed client"
    assert initial_oid != edited_oid
    edited_documents = [
        document for document in matching_documents if document["files"][old_call[0]] == edited_oid
    ]
    assert edited_documents, "edited source blob must be observed through the installed client"
    assert all(old_about[0] not in document["files"] for document in edited_documents)
    assert all(
        current["tree"] != initial["tree"]
        for current in edited_documents
        for initial in initial_documents
    )
    for owner in [*bootstrap_owners, *owners, *run_owners]:
        assert owner.last_cleanup["complete"], owner.last_cleanup
        assert owner.process is not None and owner.process.poll() is not None
        assert owner.members() == []


def test_explicit_refusal_does_not_select_operator_route(monkeypatch, tmp_path):
    from harness.knowledge import al_facts

    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 19, b"", b"explicit run refused")

    monkeypatch.setattr(al_facts, "resolve", real_resolve)
    client = al_facts.resolve(
        tmp_path,
        platform="codex",
        session="missing-run",
        runner=runner,
        launcher=tmp_path / "private-alctl",
        allow_operator=True,
    )
    assert isinstance(client, al_facts.ALFacts)
    assert not isinstance(client, al_facts.OperatorFacts)
    try:
        client.query("edges")
    except al_facts.FactsUnavailable as error:
        assert "explicit run refused" in str(error)
    else:
        raise AssertionError("explicit run refusal was hidden")
    assert len(calls) == 1
    assert calls[0][1:3] == ["run", "--repository"]


def test_native_refusal_does_not_select_operator_route(monkeypatch, tmp_path):
    from harness.knowledge import al_facts

    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 19, b"", b"native run refused")

    monkeypatch.setenv("CLAUDE_SESSION_ID", "native-facts-session")
    monkeypatch.setattr(al_facts, "resolve", real_resolve)
    client = al_facts.resolve(
        tmp_path, runner=runner, launcher=tmp_path / "private-alctl", allow_operator=True
    )
    assert isinstance(client, al_facts.ALFacts)
    assert not isinstance(client, al_facts.OperatorFacts)
    try:
        client.query("edges")
    except al_facts.FactsUnavailable as error:
        assert "native run refused" in str(error)
    else:
        raise AssertionError("native run refusal was hidden")
    assert len(calls) == 1
    assert calls[0][1:3] == ["run", "--repository"]
    assert "--session" not in calls[0]


def test_installed_operator_rejects_unsafe_path_and_commit_without_state_change(tmp_path):
    _, home, repository, launcher, environment, bootstrap_owners = owned_installed_launcher(
        tmp_path / "managed-launcher"
    )
    owners = []
    private_git(repository, tmp_path / "fixture-git", owners, "init", "-q", "--initial-branch=main")
    (repository / "safe.py").write_text("VALUE = 1\n")
    private_git(repository, tmp_path / "fixture-git", owners, "add", ".")
    private_git(
        repository,
        tmp_path / "fixture-git",
        owners,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--no-gpg-sign",
        "-qm",
        "safe fixture",
    )
    run_owners = []
    run = owned_launcher_runner(tmp_path / "launcher-commands", run_owners, environment)
    pin_store = home / "installer" / "al-run-pins"
    safe_contents = b"VALUE = 1\n"
    safe_blob = _git_blob_sha1(safe_contents)
    repo_before = tree_state(repository)
    pins_before = tree_state(pin_store) if pin_store.exists() else None
    runtime_before = _protected_runtime_state(home, repository)
    positive = run(
        [str(launcher), "facts", "--repository", str(repository), "edges", "safe.py"],
        cwd=repository,
        capture_output=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert positive.returncode == 0, positive.stderr[-2000:]
    document = json.loads(positive.stdout)
    assert document["schema"] == 1
    assert document["files"]["safe.py"] == safe_blob
    assert tree_state(repository) == repo_before
    assert (tree_state(pin_store) if pin_store.exists() else None) == pins_before
    assert _protected_runtime_state(home, repository) == runtime_before
    for arguments in (
        ["facts", "--repository", str(repository), "edges", "../outside.py"],
        ["facts", "--repository", str(repository), "edges", "--commit", "../outside"],
    ):
        repo_before = tree_state(repository)
        pins_before = tree_state(pin_store) if pin_store.exists() else None
        runtime_before = _protected_runtime_state(home, repository)
        result = run(
            [str(launcher), *arguments],
            cwd=repository,
            capture_output=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode != 0, result.stdout
        assert result.stdout == b"", "refusal must not publish success JSON"
        expected_error = (
            "unsafe operator facts path"
            if arguments[-1] == "../outside.py"
            else "unsupported operator facts revision"
        )
        assert expected_error in result.stderr.decode("utf-8", "replace").lower()
        assert tree_state(repository) == repo_before
        assert (tree_state(pin_store) if pin_store.exists() else None) == pins_before
        assert _protected_runtime_state(home, repository) == runtime_before
    for owner in [*bootstrap_owners, *owners, *run_owners]:
        assert owner.last_cleanup["complete"], owner.last_cleanup
        assert owner.process is not None and owner.process.poll() is not None
        assert owner.members() == []


def test_fact_clients_translate_transport_errors_without_route_fallback(tmp_path):
    from harness.knowledge.al_facts import ALFacts, FactsUnavailable, OperatorFacts

    oversized = b"x" * ((32 * 1024 * 1024) + 1)
    failures = (
        ("malformed JSON", lambda argv: subprocess.CompletedProcess(argv, 0, b"{", b"")),
        ("oversized output", lambda argv: subprocess.CompletedProcess(argv, 0, oversized, b"")),
        (
            "timeout",
            lambda argv: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 120)),
        ),
    )
    for client_type in (ALFacts, OperatorFacts):
        for expected, failure in failures:
            calls = []

            def synthetic_runner(argv, *, calls=calls, failure=failure, **kwargs):
                calls.append((argv, kwargs))
                return failure(argv)

            if client_type is ALFacts:
                client = ALFacts(
                    tmp_path,
                    "codex",
                    "selected-run",
                    launcher=tmp_path / "private-alctl",
                    runner=synthetic_runner,
                )
                route = "run"
            else:
                client = OperatorFacts(
                    tmp_path, launcher=tmp_path / "private-alctl", runner=synthetic_runner
                )
                route = "facts"
            try:
                client.query("edges")
            except FactsUnavailable as error:
                message = str(error).lower()
                assert message and len(message) <= 500
                if expected == "malformed JSON":
                    assert "not json" in message
                elif expected == "oversized output":
                    assert "too large" in message
                else:
                    assert "timed out" in message or "timeout" in message
            else:
                raise AssertionError(f"{client_type.__name__} accepted {expected}")
            assert len(calls) == 1, "a selected facts route must not fall back after failure"
            argv, kwargs = calls[0]
            assert argv[1] == route, argv
            assert kwargs["capture_output"] is True
            assert kwargs["stdin"] is subprocess.DEVNULL


def test_tampered_verified_snapshot_refuses_installed_facts_command(tmp_path):
    import subprocess

    _, home, repository, launcher, environment, bootstrap_owners = owned_installed_launcher(
        tmp_path / "managed-launcher"
    )
    snapshots = list((home / "installer/installer/cli/snapshots").iterdir())
    assert len(snapshots) == 1, "private bootstrap must yield one verified CLI snapshot"
    resources = list(snapshots[0].rglob("al_snapshot.py"))
    assert resources, "verified runtime snapshot must include the shared snapshot resource"
    resource = resources[0]
    resource.write_bytes(resource.read_bytes() + b"\n# deliberate fixture tamper\n")
    target_before = tree_state(repository)
    owners = []
    run = owned_launcher_runner(tmp_path / "tampered-runtime-commands", owners, environment)
    result = run(
        [str(launcher), "facts", "--repository", str(repository), "edges"],
        cwd=repository,
        capture_output=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0, "tampered managed resources must fail closed"
    assert result.stdout == b"", "tampered runtime must not publish fact JSON"
    assert b"snapshot corrupt" in result.stderr.lower() or b"managed" in result.stderr.lower()
    assert tree_state(repository) == target_before
    for owner in [*bootstrap_owners, *owners]:
        assert owner.last_cleanup["complete"], owner.last_cleanup
        assert owner.process is not None and owner.process.poll() is not None
        assert owner.members() == []
