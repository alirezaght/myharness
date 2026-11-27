"""#34: one import analyser. AL's code facts and the knowledge graph's view of them give
byte-identical edges and consumers on a monorepo with a Python src layout, a uv workspace,
TypeScript tsconfig paths and pnpm workspace packages."""

import hashlib
import json
import random
import string
import subprocess

import pytest

from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.consumers import consumers, edges
from harness.knowledge.integration import refresh_until_settled
from harness.knowledge.policy import current_operator_scope
from tests.al_facts_support import (
    al_facts,
    direct,
    owned_installed_launcher,
    owned_launcher_runner,
    private_git,
    tree_state,
)
from tests.test_knowledge_consumers import FILES

MONOREPO = {**FILES, "pnpm-workspace.yaml": "packages:\n  - 'apps/*'\n  - 'packages/*'\n"}
TARGETS = [
    "services/orders/src/application/pricing.py",
    "shared_lib.money",
    "packages/ui/src/Button.tsx",
    "packages/ui/src/index.ts",
    "apps/web/src/lib/format.ts",
    "packages/legacy",
    "services/orders/src/application/api.py",
    "/api/orders/42",
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("equivalence")
    root = tmp_path / "repo"
    for name, text in MONOREPO.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    conn = connect(tmp_path / "state.db")
    scope = current_operator_scope(conn, root)
    with pytest.MonkeyPatch.context() as patch:
        # The refresh asks AL (this checkout's analyser) for the edges it stores.
        patch.setattr(
            "harness.knowledge.al_facts.resolve",
            lambda repository, **_: al_facts(repository, tmp_path / "al-index"),
        )
        assert refresh_until_settled(HarnessController(conn), scope).status == "published"
    yield conn, current_operator_scope(conn, root), tmp_path / "al-index"
    conn.close()


def test_edges_are_byte_identical(graph):
    conn, scope, index = graph
    analysed = json.loads(direct(scope.repository, index, "edges"))
    viewed = edges(conn, scope, facts=al_facts(scope.repository, index))
    assert viewed["source"] == "al-facts" and viewed["status"] == analysed["status"] == "complete"
    assert canonical(viewed["edges"]) == canonical(analysed["edges"])
    relations = {(e["relation"], e["target"].split(":", 1)[0]) for e in analysed["edges"]}
    assert relations >= {
        ("imports", "module"),
        ("imports", "path"),
        ("imports", "import"),
        ("serves-endpoint", "endpoint"),
        ("calls-endpoint", "endpoint"),
    }
    # Generated output is outside the analyser's view for both.
    assert not any(e["locator"].startswith("apps/web/dist/") for e in analysed["edges"])


def test_stored_graph_edges_are_als_edges(graph):
    """The graph's own import/endpoint assertions are AL's rows, stored verbatim at refresh."""
    conn, scope, index = graph
    analysed = json.loads(direct(scope.repository, index, "edges"))
    stored = conn.execute(
        "SELECT s.locator,a.predicate,o.scoped_key,a.value_json,e.start_byte,e.end_byte,a.text "
        "FROM knowledge_assertions a JOIN knowledge_entities o ON o.entity_id=a.object_id "
        "JOIN knowledge_evidence e USING(assertion_id) "
        "JOIN knowledge_source_versions v ON v.version_id=e.version_id "
        "JOIN knowledge_sources s ON s.source_id=v.source_id "
        "WHERE a.project_id=? AND a.predicate IN ('imports','serves-endpoint','calls-endpoint')",
        (scope.project_id,),
    ).fetchall()
    rows = sorted(
        canonical(
            {
                "locator": r[0],
                "relation": r[1],
                "target": r[2],
                "value": json.loads(r[3]),
                "start": r[4],
                "end": r[5],
                "text": r[6],
            }
        )
        for r in stored
    )
    assert rows == sorted(canonical(edge) for edge in analysed["edges"])


@pytest.mark.parametrize("target", TARGETS)
def test_consumers_are_byte_identical(graph, target):
    conn, scope, index = graph
    analysed = json.loads(direct(scope.repository, index, "consumers", target))
    viewed = consumers(conn, scope, target, facts=al_facts(scope.repository, index))
    assert analysed["consumers"], target
    assert canonical(viewed["consumers"]) == canonical(analysed["consumers"])
    assert canonical(viewed["resolved"]) == canonical(analysed["resolved"])


def test_installed_operator_and_run_routes_match_for_commit_and_working_copy(tmp_path):
    from harness.knowledge.al_facts import ALFacts, OperatorFacts

    _, home, repository, launcher, environment, bootstrap_owners = owned_installed_launcher(
        tmp_path / "managed-launcher"
    )
    owners = []
    private_git(repository, tmp_path / "fixture-git", owners, "init", "-q", "--initial-branch=main")
    for name, text in FILES.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    many_paths = {
        f"services/orders/src/application/many_file_{number:03}.py" for number in range(256)
    }
    for number, name in enumerate(sorted(many_paths)):
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"VALUE = {number}\n")
    # A deterministic high-entropy source makes the one-byte revision a real pack delta.
    generator = random.Random(34)
    payload = "".join(generator.choices(string.ascii_letters + string.digits, k=8192))
    (repository / "services/orders/src/application/delta.py").write_text(
        "DATA = '" + payload + "'\n"
    )
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
        "initial analysed tree",
    )
    old_commit = (
        private_git(repository, tmp_path / "fixture-git", owners, "rev-parse", "HEAD")
        .decode()
        .strip()
    )
    old_blob = (
        private_git(
            repository,
            tmp_path / "fixture-git",
            owners,
            "rev-parse",
            "HEAD:services/orders/src/application/delta.py",
        )
        .decode()
        .strip()
    )
    changed = payload[:4096] + ("A" if payload[4096] != "A" else "B") + payload[4097:]
    (repository / "services/orders/src/application/delta.py").write_text(
        "DATA = '" + changed + "'\n"
    )
    private_git(
        repository,
        tmp_path / "fixture-git",
        owners,
        "add",
        "--",
        "services/orders/src/application/delta.py",
    )
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
        "near-identical source",
    )
    new_commit = (
        private_git(repository, tmp_path / "fixture-git", owners, "rev-parse", "HEAD")
        .decode()
        .strip()
    )
    private_git(
        repository, tmp_path / "fixture-git", owners, "repack", "-adf", "--window=20", "--depth=20"
    )
    current_blob = (
        private_git(
            repository,
            tmp_path / "fixture-git",
            owners,
            "rev-parse",
            "HEAD:services/orders/src/application/delta.py",
        )
        .decode()
        .strip()
    )
    index_path = next((repository / ".git/objects/pack").glob("*.idx"))
    packed = private_git(
        repository, tmp_path / "fixture-git", owners, "verify-pack", "-v", str(index_path)
    )
    rows_by_blob = {
        oid: [line.split() for line in packed.decode().splitlines() if line.startswith(oid + " ")]
        for oid in (old_blob, current_blob)
    }
    assert all(len(rows) == 1 for rows in rows_by_blob.values()), rows_by_blob
    deltified_revisions = [
        (revision, oid, rows_by_blob[oid][0])
        for revision, oid in ((old_commit, old_blob), (new_commit, current_blob))
        if len(rows_by_blob[oid][0]) >= 7
    ]
    assert deltified_revisions, (
        "at least one known committed source blob must be a genuine deltified pack member",
        rows_by_blob,
    )
    commit, selected_blob, selected_delta_row = deltified_revisions[0]
    selected_path_blob = (
        private_git(
            repository,
            tmp_path / "fixture-git",
            owners,
            "rev-parse",
            f"{commit}:services/orders/src/application/delta.py",
        )
        .decode()
        .strip()
    )
    assert selected_path_blob == selected_blob
    assert len(selected_delta_row) >= 7
    assert old_blob != current_blob

    run_owners = []
    runner = owned_launcher_runner(tmp_path / "launcher-commands", run_owners, environment)
    session = "operator-facts-equivalence"
    start = runner(
        [
            str(launcher),
            "run",
            "--repository",
            str(repository),
            "--platform",
            "codex",
            "--session",
            session,
            "--",
            "start",
            "--spec",
            "private facts equivalence fixture",
        ],
        cwd=repository,
        capture_output=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert start.returncode == 0, start.stderr[-2000:]
    run_facts = ALFacts(repository, "codex", session, launcher=launcher, runner=runner)
    operator_facts = OperatorFacts(repository, launcher=launcher, runner=runner)

    protected = run_facts.query("edges", "--commit", commit)
    protected_consumers = run_facts.query(
        "consumers", "services/orders/src/application/pricing.py", "--commit", commit
    )
    delta_path = "services/orders/src/application/delta.py"
    assert protected["files"][delta_path] == selected_blob
    target_before_operator = tree_state(repository)
    pin_store = home / "installer" / "al-run-pins"
    pins_before_operator = tree_state(pin_store) if pin_store.exists() else None
    standalone = operator_facts.query("edges", "--commit", commit)
    assert standalone["files"][delta_path] == selected_blob
    assert many_paths <= protected["files"].keys()
    assert many_paths <= standalone["files"].keys()
    assert tree_state(repository) == target_before_operator
    assert (tree_state(pin_store) if pin_store.exists() else None) == pins_before_operator
    assert protected["tree"] == standalone["tree"]
    assert protected["files"] == standalone["files"]
    assert canonical(protected["edges"]) == canonical(standalone["edges"])
    standalone_consumers = operator_facts.query(
        "consumers", "services/orders/src/application/pricing.py", "--commit", commit
    )
    assert canonical(protected_consumers["resolved"]) == canonical(standalone_consumers["resolved"])
    assert canonical(protected_consumers["consumers"]) == canonical(
        standalone_consumers["consumers"]
    )

    # Working-copy comparison covers an unstaged edit and a staged deletion together.
    home_path = "apps/web/src/pages/home.tsx"
    home_contents = (
        b"import { format } from '@/lib/format';\n"
        b"export const load = () => fetch('/api/orders/8');\n"
    )
    committed_tree = protected["tree"]
    committed_home_blob = protected["files"][home_path]
    (repository / home_path).write_bytes(home_contents)
    private_git(
        repository,
        tmp_path / "fixture-git",
        owners,
        "rm",
        "-f",
        "--",
        "apps/web/src/pages/about.tsx",
    )
    protected_worktree = run_facts.query("edges")
    target_before_operator = tree_state(repository)
    pins_before_operator = tree_state(pin_store) if pin_store.exists() else None
    standalone_worktree = operator_facts.query("edges")
    assert many_paths <= protected_worktree["files"].keys()
    assert many_paths <= standalone_worktree["files"].keys()
    assert tree_state(repository) == target_before_operator
    assert (tree_state(pin_store) if pin_store.exists() else None) == pins_before_operator
    assert protected_worktree["tree"] == standalone_worktree["tree"]
    git_blob_header = b"blob " + str(len(home_contents)).encode() + b"\0"
    expected_home_blob = hashlib.sha1(git_blob_header + home_contents).hexdigest()
    assert protected_worktree["files"][home_path] == expected_home_blob
    assert standalone_worktree["files"][home_path] == expected_home_blob
    assert expected_home_blob != committed_home_blob
    assert protected_worktree["tree"] != committed_tree
    assert protected_worktree["files"] == standalone_worktree["files"]
    assert canonical(protected_worktree["edges"]) == canonical(standalone_worktree["edges"])
    assert not any(
        row[0] == "apps/web/src/pages/about.tsx" for row in standalone_worktree["files"].items()
    )

    for owner in [*bootstrap_owners, *owners, *run_owners]:
        assert owner.last_cleanup["complete"], owner.last_cleanup
        assert owner.process is not None and owner.process.poll() is not None
        assert owner.members() == []
