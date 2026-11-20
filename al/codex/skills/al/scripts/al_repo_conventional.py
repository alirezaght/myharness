"""Compose conventional observations without replacing retained Python semantics."""

import al_repo_go as Go
import al_repo_python as Python
from al_repository_facts import within

ID = "conventional"
VERSION = 1


def observe(scan):
    facts = Python.observe(scan)
    Go.observe(scan, facts)
    # Only a positively observed module contradicts a tooling-only exclusion.
    roots = [u["path"] for u in facts["units"] if "go" in u["languages"]]
    facts["enumeration"]["exclusions"] = [
        e
        for e in facts["enumeration"]["exclusions"]
        if not (
            e["reason"] == "manifest-less tooling root outside service coverage"
            and any(within(e["path"], r) for r in roots)
        )
    ]
    return facts
