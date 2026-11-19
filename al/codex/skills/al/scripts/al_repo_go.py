"""Bounded, literal Go manifests. No compiler, resolver or repository code runs here."""

import re
import shlex
from pathlib import PurePosixPath

from al_repository_capture import CaptureDrift
from al_repository_facts import gap, safe, unit_depth, within

MAX_BYTES = 262144
MAX_ROWS = 4096
MAX_MODULES = 2048
NAME = re.compile(r"[A-Za-z0-9_.~+/-]+\Z")
VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.+-]+)?\Z")


def relative(parent, value):
    if not value or value.startswith("/") or "\\" in value or any(c in value for c in "$`*?[]"):
        raise ValueError("nonliteral or external local path")
    parts = [] if parent == "." else list(PurePosixPath(parent).parts)
    for part in value.split("/"):
        if part == "..":
            if not parts:
                raise ValueError("local path escapes repository")
            parts.pop()
        elif part not in {"", "."}:
            parts.append(part)
    path = "/".join(parts) or "."
    safe(path)
    return path


def _tokens(line):
    quoted = False
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        if not quoted and line[i : i + 2] == "//":
            line = line[:i]
            break
    if any(c in line for c in ("`", "\\", ";", "$")):
        raise ValueError("unsupported Go manifest syntax")
    return shlex.split(line, comments=False)


def parse(text, workspace=False):
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("Go manifest byte limit")
    lines = text.splitlines()
    if len(lines) > MAX_ROWS:
        raise ValueError("Go manifest row limit")
    result = {
        "module": None,
        "require": [],
        "replace": [],
        "exclude": [],
        "use": [],
        "go": None,
        "toolchain": None,
    }
    block = None
    for line in lines:
        tokens = _tokens(line)
        if not tokens:
            continue
        if tokens == [")"]:
            if block is None:
                raise ValueError("unexpected Go block end")
            block = None
            continue
        if block:
            directive = block
        else:
            directive, *tokens = tokens
            if tokens == ["("]:
                if directive not in (
                    {"use", "replace"} if workspace else {"require", "replace", "exclude"}
                ):
                    raise ValueError("unsupported Go block")
                block = directive
                continue
        allowed = (
            {"go", "toolchain", "use", "replace"}
            if workspace
            else {"module", "go", "toolchain", "require", "replace", "exclude"}
        )
        if directive not in allowed:
            raise ValueError("unsupported Go directive: " + directive)
        if directive in {"module", "go", "toolchain"}:
            if len(tokens) != 1 or result[directive] is not None:
                raise ValueError("duplicate/invalid Go " + directive)
            value = tokens[0]
            if directive == "module" and (
                not NAME.fullmatch(value) or value.startswith((".", "/"))
            ):
                raise ValueError("invalid Go module identity")
            if directive == "go" and not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", value):
                raise ValueError("invalid Go version")
            if directive == "toolchain" and not re.fullmatch(
                r"(?:default|go[0-9]+\.[0-9]+(?:\.[0-9]+)?)", value
            ):
                raise ValueError("unsupported Go toolchain")
            result[directive] = value
        elif directive in {"require", "exclude"}:
            if (
                len(tokens) != 2
                or not NAME.fullmatch(tokens[0])
                or not VERSION.fullmatch(tokens[1])
            ):
                raise ValueError("invalid Go " + directive)
            if any(
                (row[0] == tokens[0] if directive == "require" else row == tokens)
                for row in result[directive]
            ):
                raise ValueError("duplicate Go " + directive)
            result[directive].append(tokens)
        elif directive == "use":
            if len(tokens) != 1 or tokens[0] in result["use"]:
                raise ValueError("invalid/duplicate Go workspace member")
            result["use"].append(tokens[0])
        else:
            if tokens.count("=>") != 1:
                raise ValueError("invalid Go replacement")
            index = tokens.index("=>")
            old, new = tokens[:index], tokens[index + 1 :]
            if len(old) not in {1, 2} or len(new) not in {1, 2} or not NAME.fullmatch(old[0]):
                raise ValueError("invalid Go replacement")
            if len(old) == 2 and not VERSION.fullmatch(old[1]):
                raise ValueError("invalid replaced version")
            if len(new) == 2 and (not NAME.fullmatch(new[0]) or not VERSION.fullmatch(new[1])):
                raise ValueError("invalid replacement version")
            if len(new) == 1 and new[0] not in {".", ".."} and not new[0].startswith(("./", "../")):
                raise ValueError("replacement requires literal local path or version")
            if any(row["old"] == old for row in result["replace"]):
                raise ValueError("duplicate Go replacement")
            result["replace"].append({"old": old, "new": new})
    if block:
        raise ValueError("unclosed Go manifest block")
    if not workspace and not result["module"]:
        raise ValueError("missing Go module declaration")
    return result


def parse_mod(text):
    return parse(text)


def parse_work(text):
    return parse(text, workspace=True)


def owner(units, path):
    matches = [u for u in units if "go" in u["languages"] and within(u["path"], path)]
    return max(matches, key=lambda u: unit_depth(u["path"])) if matches else None


def observe(scan, facts, *, declared=None):
    names = sorted(
        {
            str(PurePosixPath(p).parent)
            for p in set(scan.files) | set(scan.base)
            if PurePosixPath(p).name == "go.mod"
        }
    )
    if len(names) > MAX_MODULES:
        facts["enumeration"]["gaps"].append(
            gap("go-module-limit", ".", "Go module enumeration bound exceeded")
        )
        names = names[:MAX_MODULES]
    existing = {u["path"]: u for u in facts["units"]}
    for path in names:
        if any(part in {"vendor", "node_modules"} for part in PurePosixPath(path).parts):
            continue
        prefix = "" if path == "." else path + "/"
        manifest = prefix + "go.mod"
        if declared is not None and path not in declared:
            facts["enumeration"]["gaps"].append(
                gap(
                    "undeclared-go-module",
                    manifest,
                    "contract does not declare this Go module",
                    path,
                )
            )
            continue
        unit = existing.get(path)
        if unit is None:
            unit = {
                "id": path,
                "path": path,
                "languages": ["go"],
                "source_roots": [],
                "module_contexts": [],
                "provenance": [manifest],
                "roles": [],
                "external_distributions": [],
                "python_requirement": None,
            }
            facts["units"].append(unit)
            facts["enumeration"]["observed_members"].append(path)
        unit["languages"] = sorted(set(unit["languages"]) | {"go"})
        unit["go"] = {
            "snapshots": {},
            "incomplete_snapshots": [],
            "nested_modules": sorted(p for p in names if p != path and within(path, p)),
        }
        for snapshot, files in [("current", scan.files), ("base", scan.base)]:
            if manifest not in files:
                continue
            try:
                raw = (
                    scan.text(manifest, required=True, rule=True)
                    if snapshot == "current"
                    else files[manifest].decode("utf-8")
                )
                unit["go"]["snapshots"][snapshot] = parse_mod(raw)
            except CaptureDrift:
                raise
            except (ValueError, UnicodeError) as exc:
                unit["go"]["incomplete_snapshots"].append(snapshot)
                facts["enumeration"]["gaps"].append(
                    gap("unsupported-go-module", manifest, str(exc), path)
                )
        for name in ("go.mod", "go.sum"):
            scan.text(prefix + name, rule=True)
        oid = "verification-" + path.replace("/", "-").replace(".", "root")
        if not any(o["id"] == oid for o in facts["checks"]["obligations"]):
            facts["checks"]["obligations"].append(
                {"id": oid, "category": "verification", "units": [path]}
            )
            facts["checks"]["gaps"].append(
                gap(
                    "missing-verification-check",
                    manifest,
                    "Go module requires a complete sourced verification recipe",
                    path,
                )
            )
    workspaces = []
    for snapshot, files in [("current", scan.files), ("base", scan.base)]:
        identities = {}
        for unit in facts["units"]:
            data = unit.get("go", {}).get("snapshots", {}).get(snapshot)
            if not data:
                continue
            if data["module"] in identities:
                for duplicate in (unit["id"], identities[data["module"]]):
                    facts["enumeration"]["gaps"].append(
                        gap(
                            "duplicate-go-module",
                            duplicate,
                            "conflicting module identity",
                            duplicate,
                        )
                    )
            identities[data["module"]] = unit["id"]
        for manifest in sorted(p for p in files if PurePosixPath(p).name == "go.work"):
            parent = str(PurePosixPath(manifest).parent)
            try:
                raw = (
                    scan.text(manifest, required=True, rule=True)
                    if snapshot == "current"
                    else files[manifest].decode("utf-8")
                )
                data = parse_work(raw)
                members = [relative(parent, p) for p in data["use"]]
                if len(set(members)) != len(members):
                    raise ValueError("duplicate canonical workspace member")
                for member in members:
                    name = ("" if member == "." else member + "/") + "go.mod"
                    if snapshot == "current":
                        scan.observe_path(name)
                    if name not in files:
                        raise ValueError("workspace member absent or unavailable: " + member)
                workspaces.append(
                    {
                        "snapshot": snapshot,
                        "path": manifest,
                        "members": sorted(members),
                        "replace": data["replace"],
                    }
                )
                if snapshot == "current":
                    scan.text(manifest + ".sum", rule=True)
            except CaptureDrift:
                raise
            except (ValueError, UnicodeError) as exc:
                facts["enumeration"]["gaps"].append(
                    gap("unsupported-go-workspace", manifest, str(exc))
                )
    facts["enumeration"]["declarations"].extend({"go_workspace": row} for row in workspaces)
    for path in sorted(set(scan.files) | set(scan.base) | set(scan.paths)):
        if "vendor" in PurePosixPath(path).parts and not any(within(ch, path) for ch in scan.paths):
            continue
        if path.endswith(".go") and owner(facts["units"], path) is None:
            facts["enumeration"]["gaps"].append(
                gap("unowned-go-source", path, "Go source has no captured module owner")
            )
    facts["units"].sort(key=lambda u: u["id"])
