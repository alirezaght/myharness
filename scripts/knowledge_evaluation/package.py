"""Closed one-case packages and exact, unaliased frozen corpus validation."""

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

BUDGETS = {"delivered_bytes": 98304, "tool_calls": 30, "wall_seconds": 240, "answer_tokens": 1800}
PROVENANCE = (
    "This task operationalizes the previously approved S1–S7/Q30 evaluation scope. "
    "Approval is recorded by the supervising agent under that existing authorization; "
    "the user did not separately review or click approval for this generated specification."
)
FIELDS = {
    "schema_version",
    "case_id",
    "arm",
    "participant",
    "prompt",
    "host",
    "role",
    "model",
    "effort",
    "corpus",
    "allowed_roots",
    "state_directory",
    "output_directory",
    "provenance",
    "budgets",
    "specification",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def closed(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("closed object required: " + ", ".join(sorted(keys)))


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 65536:
        raise ValueError("nonempty bounded text required")
    return value


def canonical_path(value):
    path = Path(text(value))
    if not path.is_absolute() or str(path) != str(path.resolve()):
        raise ValueError("absolute canonical path without symlink aliases required")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("symlink path refused")
    return path


def disjoint(paths):
    for i, left in enumerate(paths):
        for right in paths[i + 1 :]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError("corpus, engine, state and output roots must be disjoint")


def git(root, *args):
    # Never accept executable/config overrides from the invocation environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        ["git", "--no-pager", *args],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=20,
        check=True,
    )
    return result.stdout


def corpus_receipt(root):
    """Read-only observation. The caller must compare this with an independently frozen receipt."""
    root = canonical_path(str(root))
    if git(root, "rev-parse", "--show-toplevel").decode().strip() != str(root):
        raise ValueError("corpus must be a Git root")
    entries = git(root, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0")
    files = {}
    for entry in filter(None, entries):
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("symlink/gitlink/nonregular corpus entry refused")
        name = raw_name.decode("utf-8")
        path = root / name
        if path.resolve() != path or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("corpus path alias refused")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16 * 1024 * 1024:
            raise ValueError("bounded unaliased regular corpus files required")
        raw = path.read_bytes()
        # Compare against object bytes, not filters or index/mtime heuristics.
        algorithm = "sha1" if len(oid) == 40 else "sha256"
        blob = b"blob " + str(len(raw)).encode() + b"\0" + raw
        if hashlib.new(algorithm, blob).hexdigest().encode() != oid:
            raise ValueError("working corpus differs from frozen Git objects")
        files[name] = sha(raw)
    observed = set()
    for current, directories, names in os.walk(root, followlinks=False):
        if Path(current) == root:
            directories[:] = [d for d in directories if d != ".git"]
        for name in [*directories, *names]:
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError("untracked symlink refused")
        observed.update((Path(current) / n).relative_to(root).as_posix() for n in names)
    if observed != set(files):
        raise ValueError("untracked or missing corpus files refused")
    return {
        "head": git(root, "rev-parse", "HEAD").decode().strip(),
        "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "files_sha256": sha(canonical(files).encode()),
        "file_count": len(files),
    }


def validate(package):
    closed(package, FIELDS)
    if type(package["schema_version"]) is not int or package["schema_version"] != 1:
        raise ValueError("package version 1 required")
    for field in ("arm", "participant", "host", "role"):
        text(package[field])
    if package["arm"] not in {"B", "C"} or package["participant"] not in {
        "single",
        "donor",
        "recipient",
    }:
        raise ValueError("one arm and one participant required")
    if package["host"] not in {"codex", "claude"}:
        raise ValueError("qualified native host required")
    if package["role"] not in {"al-architect", "al-designer", "al-scout", "al-engineer"}:
        raise ValueError("an actual approved specialized role is required")
    for field in ("case_id", "prompt", "model", "effort"):
        text(package[field])
    if package["budgets"] != BUDGETS or any(
        type(v) is not int for v in package["budgets"].values()
    ):
        raise ValueError("frozen budgets required")
    provenance = package["provenance"]
    closed(provenance, {"accepted_mission_sha256", "evaluation_authorization_sha256", "statement"})
    if provenance["statement"] != PROVENANCE:
        raise ValueError("supervisor approval provenance statement required")
    for key in ("accepted_mission_sha256", "evaluation_authorization_sha256"):
        if not isinstance(provenance[key], str) or not re.fullmatch(
            "[0-9a-f]{64}", provenance[key]
        ):
            raise ValueError("exact prior authorization hashes required")
    corpus = package["corpus"]
    closed(corpus, {"root", "head", "tree", "files_sha256", "file_count"})
    root = canonical_path(corpus["root"])
    if package["allowed_roots"] != [str(root)]:
        raise ValueError("only the exact frozen corpus root may be agent-readable")
    state = canonical_path(package["state_directory"])
    output = canonical_path(package["output_directory"])
    disjoint([root, state, output, Path(__file__).resolve().parents[2]])
    if not state.parent.is_dir() or not output.parent.is_dir():
        raise ValueError("state/output parents must already exist")
    if state.exists() or output.exists():
        raise ValueError("immutable single attempt: existing state/output refused")
    actual = corpus_receipt(root)
    if actual != {k: v for k, v in corpus.items() if k != "root"}:
        raise ValueError("frozen corpus HEAD/tree/file receipt mismatch")
    spec = package["specification"]
    spec_keys = {"request", "answers", "design", "scenarios"}
    if package["role"] == "al-engineer":
        spec_keys.add("task_files")
    closed(spec, spec_keys)
    if package["role"] == "al-engineer":
        task_files(package)
    text(spec["request"])
    if not isinstance(spec["answers"], dict) or not spec["answers"]:
        raise ValueError("concrete question-text to answer mapping required")
    for question, answer in spec["answers"].items():
        text(question)
        text(answer)
    from harness.design_models import DesignSection

    closed(spec["design"], set(DesignSection))
    for value in spec["design"].values():
        text(value)
    if not isinstance(spec["scenarios"], list) or not spec["scenarios"]:
        raise ValueError("concrete evaluation test scenarios required")
    from harness.testplan_models import TestCategory

    for scenario in spec["scenarios"]:
        closed(scenario, {"category", "text"})
        TestCategory(scenario["category"])
        text(scenario["text"])
    return actual


def task_files(package):
    """Explicit tracked evaluation scope; deny every prospective automatic setup unit."""
    from harness.execution_files import relative_path
    from harness.knowledge.sources import eligible

    root = Path(package["corpus"]["root"])
    scopes = package["specification"]["task_files"]
    if not isinstance(scopes, list) or not scopes or len(scopes) > 100:
        raise ValueError("nonempty bounded explicit task_files required for Engineer")
    tracked = git(root, "ls-tree", "-rz", "--name-only", "HEAD").decode().split("\0")
    for scope in scopes:
        relative_path(text(scope))
        path = root / scope
        if not path.exists() or path.is_symlink() or path.resolve() != path:
            raise ValueError("task scope must be an existing unaliased frozen source")
        if not any(name == scope or name.startswith(scope + "/") for name in tracked if name):
            raise ValueError("task scope must contain frozen tracked sources")
        if not eligible(scope if path.is_file() else scope + "/scope.md"):
            raise ValueError("protected/secret task scope refused")
        unit = path.parent if path.is_file() else path
        for candidate in (unit, unit / "python", unit / "typescript"):
            if (candidate / "uv.lock").exists() or (candidate / "package.json").exists():
                raise ValueError("task scope would trigger automatic dependency setup")
    return scopes


def load(path):
    path = canonical_path(str(path))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144:
            raise ValueError("bounded unaliased regular case package required")
        raw = b""
        while len(raw) <= 262144:
            chunk = os.read(descriptor, min(65536, 262145 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > 262144:
            raise ValueError("case package exceeds limit")
        return json.loads(raw)
    finally:
        os.close(descriptor)


def write_once(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
