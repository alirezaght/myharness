"""Closed bundled adapter registry; repository content is never imported."""
import al_repo_python
import al_repo_contract
import al_repo_conventional


def select(scan):
    # A contract once present (base, worktree or a prior manifest) cannot be dropped to downgrade coverage.
    if scan.required_adapter=='contract' or al_repo_contract.CONTRACT in scan.files or al_repo_contract.CONTRACT in scan.base:return al_repo_contract
    if any(p.endswith(('.go','/go.mod','/go.work')) or p in {'go.mod','go.work'} for p in set(scan.files)|set(scan.base)|set(scan.paths)):return al_repo_conventional
    return al_repo_python
