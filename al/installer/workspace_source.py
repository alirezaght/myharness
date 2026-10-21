"""Fresh terminal-approved workspace bytes, separate from tagged releases."""

from pathlib import Path

from .registry import canonical, digest
from .terminal_source import _context, query_environment, terminal_source
from .validator_observation import target_inventory
from .validator_origin import capture, safe


def approval_binding(proof):
    if proof.get("schema_version") != 2 or proof.get("kind") != "terminal-workspace-source-proof":
        raise ValueError("current complete terminal source proof required")
    return {
        name: proof[name]
        for name in ("repository", "session_id", "run_id", "state_sha256", "approval", "source")
    }


def capture_files(root, rows):
    """Capture only current regular bytes already bound by the actual approval."""
    files, contents = {}, {}
    total = 0
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError("incomplete approved workspace membership")
    for row in rows:
        name = row.get("path") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("invalid approved workspace path")
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != name
            or ".git" in path.parts
            or name in files
        ):
            raise ValueError("unsafe or duplicate approved workspace path")
        observed = row.get("worktree")
        if observed == {"type": "deleted"}:
            if (root / path).exists():
                raise ValueError("deleted approved workspace file reappeared")
            files[name] = None
            continue
        if not isinstance(observed, dict) or observed.get("type") != "file":
            raise ValueError("workspace source requires approved regular files")
        raw, state = capture(root / path)
        total += len(raw)
        if total > 256 * 1024 * 1024:
            raise ValueError("approved workspace byte limit")
        if state["sha256"] != observed.get("sha256") or bool(state["mode"] & 0o111) != observed.get(
            "executable"
        ):
            raise ValueError("workspace source differs from approved content: " + name)
        files[name], contents[name] = state, raw
    return files, contents


def capture_source(source, *, session, run, platform):
    root = safe(Path(source).resolve())
    context = _context()
    # The trusted launcher chooses its original source, never a target-supplied path.
    original = context["origin"]["source"]["repository"]["root"]
    if root != Path(original).resolve():
        raise ValueError("workspace source is not the trusted validator origin repository")
    environment = query_environment()
    before = target_inventory(root, environment)
    proof = terminal_source(root, session=session, run=run, platform=platform)
    if proof["repository"] != str(root):
        raise ValueError("foreign workspace terminal source")
    files, contents = capture_files(root, proof["source"]["files"])
    after = target_inventory(root, environment)
    if before != after:
        raise ValueError("workspace membership changed during source capture")
    again, _ = capture_files(root, proof["source"]["files"])
    if again != files:
        raise ValueError("workspace bytes or modes changed during source capture")
    body = {
        "schema_version": 1,
        "kind": "approved-workspace-source-v1",
        "identity": before,
        "platform": platform,
        "approval": approval_binding(proof),
        "files": files,
        "content_digest": digest(canonical(files)),
        "validator_binding": context["expected"],
    }
    return dict(body, digest=digest(canonical(body))), contents


def revalidate_source(source):
    if (
        not isinstance(source, dict)
        or source.get("kind") != "approved-workspace-source-v1"
        or source.get("schema_version") != 1
        or source.get("digest")
        != digest(canonical({k: v for k, v in source.items() if k != "digest"}))
    ):
        raise ValueError("invalid approved workspace source binding")
    approval = source["approval"]
    # Platform is read from a fresh source proof request, not guessed from a saved artifact.
    platform = source.get("platform")
    if platform not in {"codex", "claude"}:
        raise ValueError("workspace source platform missing")
    current, contents = capture_source(
        source["identity"]["root"],
        session=approval["session_id"],
        run=approval["run_id"],
        platform=platform,
    )
    if current != source:
        raise ValueError("approved workspace source changed")
    return contents
