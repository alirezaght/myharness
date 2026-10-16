"""Evidence scope containment, normalization, and submodule boundaries."""
from pathlib import Path

from evidence_helpers import entries, git, ok_cmd

def test_a_scope_git_cannot_describe_is_infra_not_pass(cli, tmp_path, monkeypatch, state_file):
    import al_store
    plain = tmp_path / "not-a-repo"
    (plain / "svc").mkdir(parents=True)
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    monkeypatch.setattr(al_store, "REPO_ROOT", plain)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    assert code != 0 and "not a git repository" in str(code)
    assert entries(state_file) == []
def test_scope_patterns_are_refused(cli):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    for pattern in ("svc/*.py", ":(glob)svc/**", "svc/[ab].py"):
        code, *_ = cli("evidence", "plan", "--area", "x", "--scope", pattern, "--", *ok_cmd("1 passed"))
        assert isinstance(code, str) and "is a pattern" in code, pattern
def test_symlinked_directories_cannot_hide_scope_changes(cli, repo, tmp_path):
    real = tmp_path / "outside-lib"
    real.mkdir()
    (real / "mod.py").write_text("a = 1\n")
    (repo / "svc/lib").symlink_to(real)
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("evidence", "plan", "--area", "x", "--scope", "svc/lib", "--", *ok_cmd("1 passed"))
    assert isinstance(code, str) and "symlink" in code
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--", *ok_cmd("1 passed"))
    assert code == 1  # a symlinked directory inside the scope cannot be fingerprinted
def test_absolute_scopes_become_repository_relative(cli, repo, state_file):
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    cli("evidence", "plan", "--area", "svc", "--scope", str(repo / "svc"), "--", *ok_cmd("1 passed"))
    cli("evidence", "run", "--area", "svc")
    assert entries(state_file)[0]["scope"] == ["svc"]
def test_an_unchanged_submodule_in_parent_scope_is_opaque(cli, repo, tmp_path):
    sub = tmp_path / "sub"
    (sub / "x.py").parent.mkdir(parents=True)
    (sub / "x.py").write_text("1\n")
    git(sub, "init", "-q")
    git(sub, "add", ".")
    git(sub, "commit", "-qm", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "svc/vendor")
    git(repo, "commit", "-qam", "unchanged submodule")
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    from gitlink_helpers import source_file
    code, *_ = cli("evidence", "run", "--area", "svc", "--scope", "svc", "--sources-file", source_file(tmp_path), "--", *ok_cmd("1 passed"))
    assert code == 0
def test_a_scope_inside_a_submodule_is_refused(cli, repo, tmp_path):
    sub = tmp_path / "sub2"
    (sub / "pkg").mkdir(parents=True)
    (sub / "pkg/x.py").write_text("1\n")
    git(sub, "init", "-q")
    git(sub, "add", ".")
    git(sub, "commit", "-qm", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor")
    cli("start", "--spec", "Verify the candidate task preserves repository state while recording evidence for its implementation.")
    code, *_ = cli("evidence", "plan", "--area", "x", "--scope", "vendor/pkg", "--", *ok_cmd("1 passed"))
    assert isinstance(code, str) and "submodule" in code


def test_declared_future_gate_scope_preserves_evidence_typo_and_symlink_guards(cli,repo,tmp_path):
    import sys
    import al_ledger as L
    from batch16_helpers import manifest, plan
    from batch13_helpers import start
    from hook_regression_helpers import invoke
    start(cli,repo)
    declared=manifest(cli,repo,['future.py'])
    gate=next(g for g in declared['gates'] if g['id']=='loc')
    gate['argv']=[sys.executable,'-c','from pathlib import Path; assert not Path("future.py").exists()']
    plan(cli,tmp_path,declared)
    for stage in ('base','candidate'):
        invoke(cli,'gates','run','--stage',stage,'--id','loc')
    marker=tmp_path/'not-launched'
    result=cli('evidence','run','--area','typo','--scope','typo.py','--',*ok_cmd('1 passed',marker))
    assert result[0]!=0 and 'matches nothing' in str(result) and not marker.exists()
    (repo/'scope-link').symlink_to(repo/'svc/app.py')
    assert any('symlink' in problem for problem in L.check_scope(['scope-link'],require_matches=False))
