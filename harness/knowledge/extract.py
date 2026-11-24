"""Deterministic observations from safe bytes; never import or execute indexed code."""

import ast
import re
from pathlib import Path, PurePosixPath

from harness.decisions import DecisionParseError, parse_decision_text
from harness.knowledge.adapters import extract_record
from harness.knowledge.models import Assertion, Extraction, SourceVersion
from harness.knowledge.sources import SCRIPT_SUFFIXES

DESCRIBED_SPAN = 8192
DESCRIBED_TEXT = 2048
_WORD = re.compile(r"[^\W\d][\w$-]{2,}")


def described_text(text: str) -> str:
    """The stored text of a `described-by` assertion (#31): each distinct word of the described
    span once, in first-seen order, at most DESCRIBED_TEXT characters. The assertion's evidence
    still covers the whole span, and explanations read the source bytes themselves; only the
    copy kept for display and full-text search is compact (it was the bulk of the database)."""
    seen, words, size = set(), [], 0
    for match in _WORD.finditer(text):
        word = match.group(0)
        key = word.lower()
        if key in seen:
            continue
        if size + len(word) + 1 > DESCRIBED_TEXT:
            break
        seen.add(key)
        words.append(word)
        size += len(word) + 1
    return " ".join(words)


def _subject(source):
    module = ".".join(PurePosixPath(source.locator).with_suffix("").parts)
    if module.endswith(".__init__"):
        module = module[:-9]
    return f"module:{module}" if source.kind == "python" else f"file:{source.locator}"


def with_code_edges(extraction: Extraction, source: SourceVersion, code_edges) -> Extraction:
    """`extraction` plus AL's code facts for exactly these bytes (#34: `facts edges` rows),
    stored verbatim as the graph's import and endpoint assertions; the graph parses none."""
    if not code_edges:
        return extraction
    subject = _subject(source)
    edges = tuple(
        Assertion(
            subject,
            edge["relation"],
            edge["target"],
            edge["value"],
            edge["text"],
            edge["start"],
            edge["end"],
            origin="static-analysis",
        )
        for edge in code_edges
    )
    return Extraction(extraction.assertions + edges, extraction.coverage)


def extract_source(source: SourceVersion, code_edges=None) -> Extraction:
    """Text, symbols and records of one source, plus AL's `code_edges` when given."""
    if code_edges:
        return with_code_edges(extract_source(source), source, code_edges)
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
            described_text(text[:DESCRIBED_SPAN]),
            0,
            min(len(raw), len(text[:DESCRIBED_SPAN].encode())),
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
        # Symbols only: import and endpoint edges are AL's code facts (#34, `code_edges`), so
        # the knowledge graph and AL have one analyser.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start, end = offsets[node.lineno - 1], offsets[node.end_lineno]
                assertions.append(
                    Assertion(
                        subject,
                        "defines",
                        f"symbol:{module}:{qualified[id(node)]}",
                        None,
                        raw[start:end].decode()[:1024],
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
    elif PurePosixPath(locator).suffix.lower() in SCRIPT_SUFFIXES:
        pass  # text index here; import and endpoint edges are AL's code facts (#34)
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
