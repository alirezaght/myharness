"""Discovery-only exact production selector AST; not an executable scanner.

See tests/fixtures/import-review-selector.md for provenance and coverage limits.
"""

def affected_services(files, paths):
    services = sorted(
        {
            str(Path(name).parent)
            for name in files
            if re.fullmatch(r"services/[^/]+/pyproject.toml", name)
            and "importlinter" in (text(files[name]) or "")
        }
    )
    if any(
        p.startswith("packages/")
        or p.startswith("ci/scripts/import_review/")
        or p == BASELINE
        for p in paths
    ):
        return services
    selected = {
        str(Path(p).parts[0] + "/" + Path(p).parts[1])
        for p in paths
        if p.startswith("services/") and len(Path(p).parts) > 2
    }
    return [s for s in services if s in selected]
