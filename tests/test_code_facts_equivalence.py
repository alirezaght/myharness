"""#34: one import analyser. AL's code facts and the knowledge graph's view of them give
byte-identical edges and consumers on a monorepo with a Python src layout, a uv workspace,
TypeScript tsconfig paths and pnpm workspace packages."""

import json
import subprocess

import pytest

from harness.controller import HarnessController
from harness.db import connect
from harness.knowledge.consumers import consumers, edges
from harness.knowledge.integration import refresh_until_settled
from harness.knowledge.policy import current_operator_scope
from tests.al_facts_support import al_facts, direct
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
