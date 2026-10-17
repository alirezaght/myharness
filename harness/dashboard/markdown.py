"""The Markdown subset the harness writes into spec files, as a JSON block tree (no HTML).

Headings (`#`..`######`), `- ` list items with indented continuation lines, paragraphs, and
inline `code` and **bold**. Everything else stays literal text; the page builds DOM nodes from
the tree with textContent only.
"""

import re
from typing import Any

HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
STRONG = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
ITEM = "- "
CONTINUATION = "  "

Span = dict[str, Any]
Block = dict[str, Any]


def _span(text: str, style: str | None = None) -> Span:
    return {"text": text, "style": style}


def _strong(text: str) -> list[Span]:
    spans, at = [], 0
    for match in STRONG.finditer(text):
        spans += [_span(text[at : match.start()]), _span(match.group(1), "strong")]
        at = match.end()
    return [*spans, _span(text[at:])]


def inline_spans(line: str) -> list[Span]:
    """Code spans first (their content is literal), then bold; adjacent plain text merged."""
    raw, at = [], 0
    for match in CODE.finditer(line):
        raw += [*_strong(line[at : match.start()]), _span(match.group(2), "code")]
        at = match.end()
    raw += _strong(line[at:])
    spans: list[Span] = []
    for span in raw:
        if not span["text"]:
            continue
        if spans and span["style"] is None and spans[-1]["style"] is None:
            spans[-1] = _span(spans[-1]["text"] + span["text"])
        else:
            spans.append(span)
    return spans


def parse_markdown(text: str) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None
    for line in text.splitlines():
        if not line.strip():
            current = None
        elif heading := HEADING.match(line):
            level, title = heading.groups()
            blocks.append({"type": "heading", "level": len(level), "spans": inline_spans(title)})
            current = None
        elif line.startswith(ITEM):
            if current is None or current["type"] != "list":
                current = {"type": "list", "items": []}
                blocks.append(current)
            current["items"].append({"lines": [inline_spans(line[len(ITEM) :])]})
        elif line.startswith(CONTINUATION) and current is not None and current["type"] == "list":
            current["items"][-1]["lines"].append(inline_spans(line.lstrip()))
        else:
            if current is None or current["type"] != "paragraph":
                current = {"type": "paragraph", "lines": []}
                blocks.append(current)
            current["lines"].append(inline_spans(line))
    return blocks
