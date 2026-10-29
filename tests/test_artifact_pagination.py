"""Public display cursors consume only text that survives redaction and response bounds."""

import pytest

from harness.execution_artifacts import ArtifactStore
from tests.test_execution_operations import operations  # noqa: F401


def test_utf8_large_pages_reconstruct_through_public_dispatch(operations):  # noqa: F811
    operation, principal, _, _, _, _, _ = operations
    text = "".join(f"路径/🦋/file-{i:05}.json\n" for i in range(10000))
    artifact = operation.artifacts.capture_generated("safe.md", text.encode())
    offset = 0
    parts = []
    while True:
        page = operation.dispatch(
            principal,
            "read_file",
            {
                "path": "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"],
                "offset": offset,
                "limit": 65536,
            },
        )
        assert len(page["text"]) <= 16384 and page["next_offset"] >= offset
        parts.append(page["text"])
        offset = page["next_offset"]
        if not page["truncated"]:
            break
    assert "".join(parts) == text and offset == len(text.encode())
    with pytest.raises(ValueError, match="UTF-8"):
        operation.dispatch(
            principal,
            "read_file",
            {
                "path": "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"],
                "offset": 1,
                "limit": 65536,
            },
        )


def test_expanding_redaction_does_not_consume_undisplayed_tail(operations):  # noqa: F811
    operation, principal, _, _, _, _, _ = operations
    text = "token=x\n" * 8000 + "safe-final-sentinel\n"
    artifact = operation.artifacts.capture_generated("redacted.md", text.encode())
    offset = 0
    parts = []
    consumed = []
    while True:
        page = operation.dispatch(
            principal,
            "read_file",
            {
                "path": "artifact:" + artifact["artifact_id"] + ":" + artifact["sha256"],
                "offset": offset,
                "limit": 65536,
            },
        )
        assert len(page["text"]) <= 16384
        consumed.append(text.encode()[offset : page["next_offset"]])
        parts.append(page["text"])
        offset = page["next_offset"]
        if not page["truncated"]:
            break
    assert b"".join(consumed) == text.encode()
    assert "".join(parts).endswith("safe-final-sentinel\n")
    assert len("".join(parts)) > len(text)


def test_invalid_limits_offsets_and_irregular_utf8_boundaries(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    artifact = store.capture_generated("safe.md", "abc🦋tail".encode())
    for offset, limit in [(4, 3), (-1, 5), (100, 5), (0, True)]:
        with pytest.raises(ValueError):
            store.read(artifact["artifact_id"], artifact["sha256"], offset=offset, limit=limit)
    first = store.read(artifact["artifact_id"], artifact["sha256"], limit=6)
    assert first["text"] == "abc" and first["next_offset"] == 3
    second = store.read(artifact["artifact_id"], artifact["sha256"], offset=3, limit=4)
    assert second["text"] == "🦋" and second["next_offset"] == 7
