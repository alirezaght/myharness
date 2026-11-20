"""Closed runner summaries and launch diagnostics; never infer tools from host PATH."""
import re

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
GO_PREFIX = re.compile(r"^DONE\s+\d+\s+tests?\b")
GO_SUMMARY = re.compile(
    r"^DONE\s+(\d+)\s+tests?((?:,\s*\d+\s+(?:skipped|failures?|errors?))*)"
    r"\s+in\s+(?:\d+(?:\.\d+)?(?:ns|[µμu]s|ms|s|m|h))+\s*$"
)
GO_PART = re.compile(r",\s*(\d+)\s+(skipped|failures?|errors?)")
MISSING_EXECUTABLE = tuple(re.compile(pattern) for pattern in (
    r"^(?:\S*/)?(?:sh|bash|dash|ksh):\s*(?:(?:line\s+)?\d+:\s*)?[^:\n]+:\s*(?:command\s+)?not found\s*$",
    r"^(?:\S*/)?zsh(?::\d+)?:\s*command not found:\s*\S+\s*$",
    r"^make(?:\[\d+\])?:\s*[^:\n]+:\s*(?:No such file or directory|Command not found)\s*$",
    r"^env:\s*[^:\n]+:\s*No such file or directory\s*$",
))


def gotestsum_counts(line):
    """DONE's total includes skips/failures; package build errors are separate."""
    match = GO_SUMMARY.fullmatch(ANSI.sub("", line).strip())
    if not match:
        return None
    if len(match[1]) > 18:
        return None
    total = int(match[1])
    parts = {}
    for value, label in GO_PART.findall(match[2]):
        if len(value) > 18:
            return None
        key = "failed" if label.startswith("failure") else "error" if label.startswith("error") else "skipped"
        if key in parts:
            return None
        parts[key] = int(value)
    passed = total - parts.get("skipped", 0) - parts.get("failed", 0)
    if passed < 0:
        return None
    return {"passed": passed, **parts}


def output_problem(text):
    """Return a fixed diagnostic; never copy a command/path/error into metadata."""
    for original in text.splitlines():
        line = ANSI.sub("", original).strip()
        if any(pattern.fullmatch(line) for pattern in MISSING_EXECUTABLE):
            return "runner output reports an unavailable executable"
        if GO_PREFIX.match(line) and gotestsum_counts(line) is None:
            return "runner reported an invalid gotestsum summary"
    return None


def captured_body(entry, raw):
    """Remove AL's exact authored header/footer before reassessing old receipts.

    Unknown older log formats are not guessed from arbitrary blank lines.
    """
    prefix = "$ " + " ".join(entry.get("argv", ())) + "\n# cwd: " + entry.get("cwd", ".") + "\n# started: "
    if not raw.startswith(prefix):
        return None
    start = raw.find("\n\n", len(prefix))
    if start < 0:
        return None
    body = raw[start + 2:]
    footer = body.rfind("\n# termination: ")
    return body[:footer] if footer >= 0 else body
