"""Immutable Git object reads, without checkout, filters or working-tree fallbacks."""

import hashlib
import re
from dataclasses import replace

from harness.knowledge.models import canonical, digest
from harness.knowledge.sources import _SECRET, ExcludedSource, SourceSizeCapped, _git, eligible


def _object_git(root, *args):
    return _git(root, "--no-replace-objects", *args)


_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def resolve_commit(root, selector):
    if not isinstance(selector, str) or not selector or len(selector) > 512:
        raise ValueError("bounded commit selector required")
    result = _object_git(root, "rev-parse", "--verify", "--end-of-options", selector + "^{commit}")
    oid = result.stdout.decode().strip()
    if result.returncode or not _OID.fullmatch(oid):
        raise ValueError("immutable commit unavailable")
    return oid


def commit_inventory(root, request):
    if request.included_paths:
        raise ValueError("commit mode refuses untracked inclusions")
    oid = request.commit
    if not _OID.fullmatch(oid or ""):
        raise ValueError("resolved commit required")
    result = _object_git(root, "ls-tree", "-r", "-z", "--full-tree", oid)
    if result.returncode:
        raise ValueError("commit tree unavailable")
    entries = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        name = name.decode("utf-8")
        if eligible(name):
            entries[name] = {"mode": mode, "type": kind, "blob": blob}
    tree = _object_git(root, "rev-parse", "--verify", oid + "^{tree}")
    if tree.returncode:
        raise ValueError("commit tree unavailable")
    observation = {
        "kind": "immutable-git-commit",
        "mode": "commit",
        "head": oid,
        "tree": tree.stdout.decode().strip(),
        "branch": None,
        "git_entries": entries,
        "inventory_sha256": digest(canonical(entries).encode()),
    }
    return sorted(entries), observation


def read_blob(root, entry, limit):
    if (
        entry.get("file_mode", entry.get("mode")) not in {"100644", "100755"}
        or entry.get("type") != "blob"
    ):
        raise ExcludedSource("symlink or gitlink excluded")
    oid = entry.get("blob", "")
    if not _OID.fullmatch(oid):
        raise ValueError("invalid blob identity")
    size = _object_git(root, "cat-file", "-s", oid)
    if size.returncode:
        raise ValueError("blob unavailable")
    length = int(size.stdout)
    if length > limit:
        raise SourceSizeCapped(limit, length)
    result = _object_git(root, "cat-file", "blob", oid)
    if result.returncode or len(result.stdout) != length:
        raise ValueError("blob unavailable")
    raw = result.stdout
    algorithm = "sha1" if len(oid) == 40 else "sha256"
    if hashlib.new(algorithm, b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != oid:
        raise ValueError("blob object identity differs")
    text = raw.decode("utf-8")
    if "\x00" in text or _SECRET.search(text):
        raise ExcludedSource("sensitive or binary blob excluded")
    return raw


def pin_request(root, request):
    return (
        replace(request, commit=resolve_commit(root, request.commit)) if request.commit else request
    )


def snapshot_observation(conn, ref):
    """Extraction identity is reusable; evidence observation belongs to the selected snapshot."""
    row = conn.execute(
        "SELECT observation_json FROM knowledge_snapshots WHERE snapshot_id="
        "(SELECT snapshot_id FROM temp.k_snapshot)"
    ).fetchone()
    import json

    observation = json.loads(row[0]) if row else {}
    if ref["kind"] in {"python", "text", "lesson"}:
        if observation.get("mode") == "commit":
            entry = observation.get("git_entries", {}).get(ref["locator"])
            if not entry:
                raise ValueError("source absent from pinned commit")
            return {
                **(json.loads(ref["observation_json"]) if ref["kind"] == "lesson" else {}),
                **entry,
                "file_mode": entry["mode"],
                "mode": "commit",
                "commit": observation["head"],
            }
        return (
            dict(
                {
                    k: v
                    for k, v in json.loads(ref["observation_json"]).items()
                    if k not in {"mode", "commit", "blob", "file_mode", "type"}
                },
                mode="working-tree",
            )
            if ref["kind"] == "lesson"
            else {"mode": "working-tree"}
        )
    return json.loads(ref["observation_json"])
