"""Closed bundled adapter registry; repository content is never imported."""
import al_repo_python
import al_repo_amber


def select(scan):
    if scan.required_adapter=='amber' or any(p in scan.files or p in scan.base for p in ('ci/manifest.yml','ci/scripts/import_review/checker.py')):return al_repo_amber
    return al_repo_python
