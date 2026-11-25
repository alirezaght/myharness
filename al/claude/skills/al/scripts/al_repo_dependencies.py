"""Compose Python imports and current/base Go module-level reverse impact."""

import al_python_imports as Python
import al_repo_go as Go
from al_repository_facts import gap, within

# Generic literal artifact references are shared by both dependency engines.
_referencing = Python._referencing


def analyze(facts, scan):
    result = Python.analyze(facts, scan)
    units = [u for u in facts["units"] if "go" in u["languages"] and "go" in u]
    if not units:
        return result
    by_id = {u["id"]: u for u in units}
    edges = set()
    known = set(result["known_affected"])
    potential = set(result["potential_services"]) | set(by_id)
    membership = []
    contexts = []
    for snapshot, files in [("current", scan.files), ("base", scan.base)]:
        names = {
            u["go"]["snapshots"][snapshot]["module"]: u["id"]
            for u in units
            if snapshot in u["go"]["snapshots"]
        }
        for unit in units:
            data = unit["go"]["snapshots"].get(snapshot)
            if not data:
                continue
            replacements = list(data["replace"])
            for declaration in facts["enumeration"]["declarations"]:
                work = declaration.get("go_workspace") if isinstance(declaration, dict) else None
                if work and work["snapshot"] == snapshot and unit["id"] in work["members"]:
                    context = {
                        "snapshot": snapshot,
                        "path": work["path"],
                        "members": sorted(set(work["members"]) & set(by_id)),
                    }
                    if context not in contexts:
                        contexts.append(context)
                    for dependency, _ in data["require"]:
                        if dependency in names and names[dependency] in context["members"]:
                            edges.add((unit["id"], names[dependency]))
                    if any(
                        within(ch, work["path"]) or within(work["path"], ch) for ch in scan.paths
                    ):
                        known.update(context["members"])
                    replacements.extend(work["replace"])
            for replacement in replacements:
                if len(replacement["new"]) != 1:
                    continue
                try:
                    parent = unit["path"]
                    # Workspace replacements are relative to their declaring workspace.
                    for declaration in facts["enumeration"]["declarations"]:
                        work = (
                            declaration.get("go_workspace")
                            if isinstance(declaration, dict)
                            else None
                        )
                        if (
                            work
                            and work["snapshot"] == snapshot
                            and unit["id"] in work["members"]
                            and replacement in work["replace"]
                        ):
                            from pathlib import PurePosixPath

                            parent = str(PurePosixPath(work["path"]).parent)
                    target = Go.relative(parent, replacement["new"][0])
                    if target not in by_id:
                        raise ValueError("local replacement has no captured module")
                    edges.add((unit["id"], target))
                except ValueError as exc:
                    facts["enumeration"]["gaps"].append(
                        gap("unsupported-go-dependency", unit["path"], str(exc), unit["id"])
                    )
        for path in files:
            if not path.endswith(".go"):
                continue
            unit = Go.owner(units, path)
            if not unit:
                continue
            membership.append([snapshot, path, unit["id"], path])
            if any(within(ch, path) or within(path, ch) for ch in scan.paths):
                known.add(unit["id"])
        for unit in units:
            prefix = "" if unit["path"] == "." else unit["path"] + "/"
            if any(p in {unit["path"], prefix + "go.mod", prefix + "go.sum"} for p in scan.paths):
                known.add(unit["id"])
    # The change's reach (#38): module edges and changed Go sources are literal; a consumer found
    # only by a shared file name widens the checks (known) but not the reach.
    python_reach = result.get("change_reach") or {}
    reach = set(python_reach.get("units", result["known_affected"])) | (known - set(result["known_affected"]))
    literal = python_reach.get("consumers")
    for row in result.get("artifact_consumers", []):
        if row["owner"] in by_id and row["kind"] != "documentation":
            known.add(row["owner"])
            reach.add(row["owner"])
        for path in row["consumers"]:
            unit = Go.owner(units, path)
            if unit:
                known.add(unit["id"])
                if literal is None or path in literal:
                    reach.add(unit["id"])
    for closed in (known, reach):
        while True:
            added = {a for a, b in edges if b in closed}
            for context in contexts:
                if closed.intersection(context["members"]):
                    added.update(context["members"])
            if added <= closed:
                break
            closed |= added
    # An unreadable/unsupported manifest can hide reverse consumers of any affected Go module.
    # Documentation without a Go consumer remains demonstrably independent and stays deferred.
    if known.intersection(by_id) or result["uncertainties"]:
        for unit in units:
            if unit["go"].get("incomplete_snapshots"):
                result["uncertainties"].append(
                    {
                        "code": "uncertain-go-dependency",
                        "unit": unit["id"],
                        "path": ("" if unit["path"] == "." else unit["path"] + "/") + "go.mod",
                        "location": None,
                        "target": None,
                        "reason": (
                            "unsupported module syntax leaves reverse Go "
                            "consumer closure unverified"
                        ),
                    }
                )
    result["go_workspace_contexts"] = contexts
    result["go_module_edges"] = [list(e) for e in sorted(edges)]
    result["source_membership"].extend(membership)
    result["potential_services"] = sorted(potential)
    result["known_affected"] = sorted(known)
    result["affected_source_paths"] = sorted(
        set(result["affected_source_paths"]) | {row[1] for row in membership if row[2] in known}
    )
    result["change_reach"] = {
        **python_reach,
        "units": sorted(reach),
        "source_paths": sorted(
            set(python_reach.get("source_paths", [])) | {row[1] for row in membership if row[2] in reach}
        ),
    }
    return result
