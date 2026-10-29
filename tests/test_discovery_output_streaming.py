"""Lossless bounded public polling with synthetic output and redaction boundaries."""

import os
import subprocess
import sys
import threading

import pytest

from harness.execution_logs import MAX_TEXT, StreamRedactor
from harness.execution_processes import ProcessManager
from tests.test_execution_operations import operations  # noqa: F401


def test_completed_lines_do_not_lose_tail_after_retained_partial():
    redactor = StreamRedactor()
    prefix = "path/" + "p" * 15000
    assert redactor.feed(prefix) == ""
    tail = "\n" + "next-path\n" * 1800
    assert redactor.feed(tail, final=True) == prefix + tail


@pytest.mark.parametrize(
    "line",
    [
        "api_key=RAW_SYNTHETIC_SECRET\n",
        "Authorization: Bearer RAW_SYNTHETIC_SECRET\n",
        '{"access_token":"RAW_SYNTHETIC_SECRET"}\n',
        "https://user:RAW_SYNTHETIC_SECRET@example.invalid\n",
        "-----BEGIN PRIVATE KEY-----\nRAW_SYNTHETIC_SECRET\n-----END PRIVATE KEY-----\n",
    ],
)
def test_split_credentials_never_escape(line):
    for cut in range(1, len(line)):
        redactor = StreamRedactor()
        result = redactor.feed(line[:cut]) + redactor.feed(line[cut:], final=True)
        assert "RAW_SYNTHETIC_SECRET" not in result


def test_oversized_credential_suppresses_suffix_until_newline():
    redactor = StreamRedactor()
    assert redactor.feed("password=" + "x" * MAX_TEXT).startswith("[oversized")
    assert redactor.feed("RAW_SYNTHETIC_SECRET" * 1000) == ""
    assert redactor.feed("suffix\nordinary-next-path\n", final=True) == "ordinary-next-path\n"


def test_public_dispatch_drains_all_large_pages(operations):  # noqa: F811
    operation, principal, _, _, _, _, _ = operations
    expected = (
        "".join(f"source/uniquely-numbered-{i:05}/package.json\n" for i in range(3500))
        + "apps/web/package.json\n"
    )
    redactor = StreamRedactor()
    chunks = [expected[i : i + 15371] for i in range(0, len(expected), 15371)]
    output = "".join(redactor.feed(chunk) for chunk in chunks) + redactor.feed("", final=True)
    manager = operation.processes
    # Synthetic completed process; public dispatch still applies response redaction.
    manager.records["completed-output"] = {
        "owner": principal["thread_id"],
        "task": None,
        "status": "Exited",
        "output": output,
        "cursor": 0,
        "returncode": 0,
        "error": None,
    }
    returned = []
    try:
        while True:
            page = operation.dispatch(
                principal, "poll_process", {"handle": "completed-output", "limit": 65536}
            )
            assert len(page["output"]) <= MAX_TEXT
            returned.append(page["output"])
            if not page["more"]:
                break
        assert "".join(returned) == expected
        assert sum(page.count("uniquely-numbered-") for page in returned) <= 3500
        assert (
            operation.dispatch(principal, "poll_process", {"handle": "completed-output"})["output"]
            == ""
        )
        with pytest.raises(ValueError, match="owned"):
            operation.dispatch(
                principal | {"thread_id": "other"}, "poll_process", {"handle": "completed-output"}
            )
        with pytest.raises(ValueError, match="bounded"):
            operation.dispatch(
                principal, "poll_process", {"handle": "completed-output", "limit": 0}
            )
    finally:
        del manager.records["completed-output"]


def test_incremental_utf8_actual_drain(monkeypatch):
    import harness.execution_processes as module

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write('路径/é/🦋\\nfinal-tail'.encode())",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    manager = ProcessManager()
    record = {
        "process": proc,
        "cancel": threading.Event(),
        "output": "",
        "error": None,
        "classification": None,
        "classification_root": ".",
    }
    original = os.read
    monkeypatch.setattr(module.os, "read", lambda fd, size: original(fd, min(size, 1)))
    manager._drain(record, 10)
    assert record["status"] == "Exited" and record["output"] == "路径/é/🦋\nfinal-tail"


def test_empty_and_final_unterminated_output():
    redactor = StreamRedactor()
    assert redactor.feed("", final=True) == ""
    assert redactor.feed("last path", final=True) == "last path"


def test_multiline_json_credentials_across_every_chunk_boundary():
    for value in [
        '{"access_token":\n"RAW_SYNTHETIC_SECRET"}\n',
        '{"password":"first\nRAW_SYNTHETIC_SECRET\nlast"}\n',
    ]:
        for cut in range(len(value) + 1):
            redactor = StreamRedactor()
            out = redactor.feed(value[:cut]) + redactor.feed(value[cut:], final=True)
            assert "RAW_SYNTHETIC_SECRET" not in out


def test_real_contained_stream_roundtrips_through_public_dispatch(operations):  # noqa: F811
    import time

    operation, principal, _, _, _, _, _ = operations
    expected = "".join(f"safe/路径-{i:05}/package.json\n" for i in range(4000))
    script = (
        "import os\ns="
        + repr(expected)
        + ".encode()\nfor i in range(0,len(s),15371): os.write(1,s[i:i+15371])"
    )
    result = operation.processes.start(
        principal["thread_id"],
        operation.profile(),
        [sys.executable, "-c", script],
        operation.envelope["repository"],
        task=None,
        revision="fixture",
    )
    parts = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        page = operation.dispatch(
            principal, "poll_process", {"handle": result["handle"], "limit": 65536}
        )
        parts.append(page["output"])
        if page["status"] != "Running" and not page["more"]:
            break
        time.sleep(0.01)
    assert page["status"] == "Exited" and page["returncode"] == 0
    assert "".join(parts) == expected


@pytest.mark.parametrize("boundary", ["cancel", "overflow"])
def test_process_cancellation_and_raw_output_cap_remain_enforced(operations, boundary):  # noqa: F811
    import time

    operation, principal, _, _, _, _, _ = operations
    script = (
        "import time;time.sleep(10)"
        if boundary == "cancel"
        else "import os;os.write(1,b'x'*1100000)"
    )
    result = operation.processes.start(
        principal["thread_id"],
        operation.profile(),
        [sys.executable, "-c", script],
        operation.envelope["repository"],
        task=None,
        revision="fixture",
    )
    if boundary == "cancel":
        operation.dispatch(principal, "cancel_process", {"handle": result["handle"]})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        page = operation.dispatch(principal, "poll_process", {"handle": result["handle"]})
        if page["status"] != "Running":
            break
        time.sleep(0.01)
    if boundary == "cancel":
        # Existing cancellation can finish draining before the cancellation loop observes the flag.
        assert page["status"] in {"Failed", "Exited"} and page["returncode"] != 0
        assert operation.processes.records[result["handle"]]["cancel"].is_set()
        if page["status"] == "Failed":
            assert "cancelled" in page["error"]
    else:
        assert page["status"] == "Failed" and "output limit" in page["error"]
