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


def test_git_membership_excludes_ignored_worktrees_and_keeps_tracked_generated_source(tmp_path):
    from harness.repository_audit import inventory

    root = write_files(
        tmp_path / "repo", {".gitignore": ".worktrees/\ndist/\n", "current.py": "needle=1\n"}
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    write_files(
        root,
        {
            "dist/authored.py": "needle=2\n",
            ".worktrees/old/pkg/pyproject.toml": '[project]\nname="old"',
            ".worktrees/old/old.py": "needle=0",
        },
    )
    subprocess.run(["git", "add", "-f", "dist/authored.py"], cwd=root, check=True)
    for name in [" leading.py", "trailing.py ", "line\nbreak.py"]:
        (root / name).write_text("needle=3\n")
    files, complete = inventory(root)
    assert complete
    assert {
        "dist/authored.py",
        "current.py",
        " leading.py",
        "trailing.py ",
        "line\nbreak.py",
    } <= set(files)
    assert not any(p.startswith(".worktrees/") for p in files)
    result = audit(root, "Fix needle")
    assert result["inventory_complete"]
    assert "current.py" in {r["path"] for r in result["existing_implementation_leads"]}
    assert not any(u["name"] == "old" for u in result["units"])


def test_nested_git_directory_file_and_symlink_boundaries_are_truthful(tmp_path):
    root = write_files(
        tmp_path / "repo",
        {"outer.py": "needle=1", "nested_dir/app.py": "needle=0", "nested_file/app.py": "needle=0"},
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    (root / "nested_dir/.git").mkdir()
    (root / "nested_file/.git").write_text("gitdir: /unavailable/worktree")
    (root / "linked.py").symlink_to("outer.py")
    result = audit(root, "Fix needle")
    assert not result["inventory_complete"]
    assert {r["path"] for r in result["existing_implementation_leads"]} == {"outer.py"}
    assert any("nested repository boundary: nested_dir" in d for d in result["diagnostics"])
    assert any("nested repository boundary: nested_file" in d for d in result["diagnostics"])
    assert any("symlink source boundary: linked.py" in d for d in result["diagnostics"])


def test_git_failure_and_limits_do_not_become_complete_filesystem_walks(tmp_path, monkeypatch):
    import harness.repository_audit as module

    root = write_files(tmp_path / "repo", {"one.py": "needle=1", "two.py": "needle=2"})
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.setattr(module, "MAX_FILES", 1)
    result = module.audit(root, "Fix needle")
    assert not result["inventory_complete"]
    assert any("limit" in d for d in result["diagnostics"])
    monkeypatch.setattr(module, "_git", lambda *args: None)
    result = module.audit(root, "Fix needle")
    assert not result["inventory_complete"] and not result["existing_implementation_leads"]
    assert any("no filesystem fallback" in d for d in result["diagnostics"])


def test_supplied_inventory_is_validated_without_another_walk(tmp_path, monkeypatch):
    import harness.repository_audit as module

    root = write_files(tmp_path / "repo", {"dist/authored.py": "needle=1", "other.py": "needle=2"})
    monkeypatch.setattr(
        module,
        "inventory",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected walk")),
    )
    result = module.audit(root, "Fix needle", files=["dist/authored.py"])
    assert result["inventory_complete"]
    assert [r["path"] for r in result["existing_implementation_leads"]] == ["dist/authored.py"]
    result = module.audit(root, "Fix needle", files=["../outside.py"])
    assert not result["inventory_complete"]


def test_non_git_fallback_does_not_descend_into_nested_checkout(tmp_path):
    root = write_files(
        tmp_path / "repo",
        {"outer.py": "needle=1", "nested/app.py": "needle=0", "nested/.git": "gitdir: elsewhere"},
    )
    result = audit(root, "Fix needle")
    assert not result["inventory_complete"]
    assert {r["path"] for r in result["existing_implementation_leads"]} == {"outer.py"}
    assert any("non-Git" in d for d in result["diagnostics"])
