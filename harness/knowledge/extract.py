"""Deterministic observations from safe bytes; never import or execute indexed code."""

import ast
import re
from pathlib import Path, PurePosixPath

from harness.decisions import DecisionParseError, parse_decision_text
from harness.knowledge.adapters import extract_record
from harness.knowledge.models import Assertion, Extraction, SourceVersion


def extract_source(source: SourceVersion) -> Extraction:
    if source.kind in {"requirements", "design", "checkpoint", "task-event", "execution-event"}:
        return extract_record(source)
    raw, locator = source.raw, source.locator
    text = raw.decode("utf-8")
    module = ".".join(PurePosixPath(locator).with_suffix("").parts)
    if module.endswith(".__init__"):
        module = module[:-9]
    subject = f"module:{module}" if source.kind == "python" else f"file:{locator}"
    assertions = [
        Assertion(
            subject,
            "described-by",
            None,
            locator,
            text[:8192],
            0,
            min(len(raw), len(text[:8192].encode())),
        )
    ]
    coverage = []
    if source.kind == "python":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return Extraction(tuple(assertions), ({"locator": locator, "status": "syntax-error"},))
        lines = raw.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        qualified = {}

        def definitions(node, prefix=""):
            for child in ast.iter_child_nodes(node):
                name = prefix
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = f"{prefix}.{child.name}" if prefix else child.name
                    qualified[id(child)] = name
                definitions(child, name)

        definitions(tree)
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                start, end = offsets[node.lineno - 1], offsets[node.end_lineno]
                fragment = raw[start:end].decode()
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = (
                        module.split(".")
                        if locator.endswith("/__init__.py")
                        else module.split(".")[:-1]
                    )
                    if node.level:
                        base = (
                            base[: len(base) - node.level + 1]
                            if node.level <= len(base) + 1
                            else []
                        )
                        target = ".".join([*base, *([node.module] if node.module else [])])
                    else:
                        target = node.module or ""
                    targets = [target] if target else []
                    if not node.module:
                        targets = [
                            ".".join(filter(None, [target, alias.name])) for alias in node.names
                        ]
                else:
                    assertions.append(
                        Assertion(
                            subject,
                            "defines",
                            f"symbol:{module}:{qualified[id(node)]}",
                            None,
                            fragment[:1024],
                            start,
                            end,
                            origin="static-analysis",
                        )
                    )
                    continue
                for target in targets:
                    assertions.append(
                        Assertion(
                            subject,
                            "imports",
                            f"module:{target}",
                            None,
                            fragment,
                            start,
                            end,
                            origin="static-analysis",
                        )
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("__import__", "eval", "exec")
            ):
                coverage.append({"locator": locator, "status": "dynamic-reference-unresolved"})
    elif PurePosixPath(locator).name.startswith("DEC-") and locator.endswith(".md"):
        try:
            record = parse_decision_text(text, Path(locator))
        except DecisionParseError:
            coverage.append({"locator": locator, "status": "malformed-decision"})
        else:
            stable = f"decision:{record.id}"
            decision = f"{stable}:v{record.version}"
            applicability = {"historical": "/history/" in locator}
            assertions.append(
                Assertion(
                    decision,
                    "version-of",
                    stable,
                    record.version,
                    record.id,
                    0,
                    len(raw),
                    applicability=applicability,
                )
            )
            for predicate, value in [
                ("title", record.title),
                ("status", record.status.value),
                ("scope", record.scope),
                ("rationale", record.rationale),
                ("decision", record.decision),
            ]:
                assertions.append(
                    Assertion(
                        decision,
                        predicate,
                        None,
                        value,
                        str(value),
                        0,
                        len(raw),
                        applicability=applicability,
                    )
                )
            for ref in re.findall(r"\bDEC-\d+\b", record.supersedes or ""):
                assertions.append(
                    Assertion(
                        decision,
                        "supersedes",
                        f"decision:{ref}",
                        None,
                        record.supersedes,
                        0,
                        len(raw),
                        applicability=applicability,
                    )
                )
    elif not locator.endswith(".md"):
        coverage.append({"locator": locator, "status": "text-only-no-dependency-analysis"})
    # Explicit document references are observations, never semantic entailments.
    if locator.endswith(".md"):
        for match in re.finditer(r"\b(?:DEC-\d+|REQ-[A-Za-z0-9_-]+)\b", text):
            start = len(text[: match.start()].encode())
            end = start + len(match[0].encode())
            kind = "decision" if match[0].startswith("DEC-") else "requirement"
            assertions.append(
                Assertion(subject, "references", f"{kind}:{match[0]}", None, match[0], start, end)
            )
    if source.kind == "python" and any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "import_module"
        for n in ast.walk(tree)
    ):
        coverage.append({"locator": locator, "status": "dynamic-reference-unresolved"})
    return Extraction(tuple(assertions), tuple(coverage))
