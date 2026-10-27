"""Ownership, implementation leads and main currency precede risk classification."""

import json
import subprocess

from harness.analyzer import analyze
from harness.repository_audit import audit
from tests.sample_repo import tree_snapshot, write_files


def test_manifest_scope_and_call_sites_outweigh_unrelated_test_volume(tmp_path):
    files = {
        "package.json": '{"name":"workspace"}',
        "apps/api/package.json": '{"name":"@shop/api"}',
        "apps/api/src/main.ts": "import {render} from '../../../packages/mail/render'; render();",
        "apps/api/AGENTS.md": "Use the existing renderer and API tests.",
        "apps/api/.env": "TOKEN=never-print-this",
        "apps/web/package.json": '{"name":"@shop/web"}',
        "packages/mail/package.json": '{"name":"@shop/mail"}',
        "packages/mail/render.ts": "export function render() {return 'existing behavior';}",
        "tools/migration/.env.dry-run": "OTHER=not-relevant-secret",
    }
    files.update({f"apps/web/tests/test_renderer_{i}.ts": "renderer renderer" for i in range(40)})
    root = write_files(tmp_path / "repo", files)
    before = tree_snapshot(root)
    result = audit(root, "Update the renderer used by @shop/api")
    assert [u["root"] for u in result["selected_units"]] == ["apps/api"]
    assert "packages/mail/render.ts" in result["relevant_paths"]
    assert result["call_sites"][0]["from"] == "apps/api/src/main.ts"
    assert {r["path"] for r in result["instructions"]} == {"apps/api/AGENTS.md"}
    assert "never-print-this" not in json.dumps(result)
    report = analyze(root, "Update the renderer used by @shop/api")
    assert not any("migration" in q.text for q in report.questions)
    assert not any("apps/web" in a.text for a in report.assumptions)
    assert any("apps/api/.env" in q.text for q in report.questions)
    assert tree_snapshot(root) == before


def test_unresolved_ownership_stays_unresolved_and_generated_files_are_excluded(tmp_path):
    root = write_files(
        tmp_path / "repo",
        {
            "apps/api/package.json": '{"name":"api"}',
            "apps/web/package.json": '{"name":"web"}',
            "apps/api/render.ts": "function renderer() {}",
            "apps/web/tests/render.test.ts": "renderer renderer renderer",
            "node_modules/generated/package.json": '{"name":"renderer"}',
            "dist/render.ts": "renderer",
        },
    )
    result = audit(root, "Update renderer")
    assert result["ownership"] == "UNRESOLVED" and result["selected_units"] == []
    assert len(result["units"]) == 2
    assert result["existing_implementation_leads"]
    assert all("node_modules" not in r["path"] for r in result["existing_implementation_leads"])
    assert all(
        r["status"] == "INSPECTION_LEAD_NOT_BEHAVIOR_PROOF"
        for r in result["existing_implementation_leads"]
    )


def test_known_main_divergence_is_read_only_and_does_not_claim_remote_freshness(tmp_path):
    root = write_files(tmp_path / "repo", {"app.py": "value=1\n"})

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    git("init", "-q", "-b", "main")
    git("add", ".")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "first")
    base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "older-work")
    git("checkout", "main")
    (root / "app.py").write_text("value=2\n")
    git("add", ".")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "new main")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "older-work")
    before = tree_snapshot(root)
    result = audit(root, "Update app.py")
    assert result["head"] == base
    assert {r["behind"] for r in result["main_comparisons"]} == {1}
    assert result["remote_freshness"] == "UNVERIFIED_NO_FETCH"
    assert tree_snapshot(root) == before
