"""Keyword inspection leads; these do not establish ownership or implementation."""

import re
from pathlib import Path, PurePosixPath

from harness.investigation import FactCategory
from harness.repo_scan import Findings, read_text

STOPWORDS = frozenset(
    {"the", "and", "for", "with", "that", "this", "from", "into", "should", "must", "when"}
    | {"then", "add", "make", "new", "use", "all", "can", "not", "are", "our", "its", "but"}
    | {"also", "have", "will", "need", "want", "please", "fix", "some", "via", "per", "any"}
)
MIN_KEYWORD_LENGTH = 3
MAX_RELEVANT = 20


def keywords(request: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", request.lower())
    usable = [w for w in words if len(w) >= MIN_KEYWORD_LENGTH and w not in STOPWORDS]
    return list(dict.fromkeys(usable))


def relevance(root: Path, files: list[PurePosixPath], request: str) -> Findings:
    out = Findings()
    words = keywords(request)
    if not words:
        out.ask("scope", "Request has no usable keywords; which part of the code does it concern?")
        return out
    scored = []
    for rel in files:
        path = rel.as_posix().lower()
        in_path = [w for w in words if w in path]
        text = (read_text(root, rel) or "").lower()
        in_text = [w for w in words if w in text]
        matched = [w for w in words if w in in_path or w in in_text]
        if matched:
            scored.append((-len(matched), -len(in_path), rel.as_posix(), rel, matched, in_path))
    if not scored:
        text = (
            f"No file matches the request keywords ({', '.join(words)}); is this new"
            " functionality or does it concern a component named differently?"
        )
        out.ask("scope", text)
        return out
    scored.sort()
    for _, _, where, _, matched, in_path in scored[:MAX_RELEVANT]:
        listed = ", ".join(matched)
        evidence = f"keyword match: {listed}" + (
            f" (path: {', '.join(in_path)})" if in_path else ""
        )
        noun = "keyword" if len(matched) == 1 else "keywords"
        text = f"`{where}` matches {len(matched)} request {noun} ({listed})"
        out.fact(FactCategory.FILES, text, evidence)
    return out
