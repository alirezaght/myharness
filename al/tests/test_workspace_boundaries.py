"""Real filesystem mutation windows and closed uv output do not grant new inputs."""

from pathlib import Path

import pytest

from al.installer import journal as J
from al.installer import workspace_actions as A
from al.installer import workspace_wheel as W
from al.installer.engine import T


def test_uv_output_marker_is_exact_and_other_outputs_refuse(tmp_path):
    wheel = tmp_path / "harness-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"artifact-selection-only; wheel contents separately validated")
    assert W.wheel_output(tmp_path) == wheel
    marker = tmp_path / ".gitignore"
    marker.write_bytes(b"*")
    marker.chmod(0o644)
    assert W.wheel_output(tmp_path) == wheel
    for raw in (b"*\n", b"unrecognized"):
        marker.write_bytes(raw)
        with pytest.raises(ValueError, match="marker"):
            W.wheel_output(tmp_path)
    marker.write_bytes(b"*")
    marker.chmod(0o755)
    with pytest.raises(ValueError, match="marker"):
        W.wheel_output(tmp_path)
    marker.chmod(0o644)
    (tmp_path / "unexpected").write_bytes(b"not a build output")
    with pytest.raises(ValueError, match="exactly one"):
        W.wheel_output(tmp_path)


def test_late_destination_drift_never_becomes_an_adopted_preimage(tmp_path, monkeypatch):
    target = tmp_path / "resource"
    target.write_bytes(b"approved-before")
    preimages = {str(target): T.state(target)}
    desired = {str(target): (b"approved-after", 0o644)}
    target.write_bytes(b"concurrent-user-content")
    with pytest.raises(ValueError, match="planned preimage changed"):
        A.prepare(tmp_path / "stage1", desired, preimages)
    assert target.read_bytes() == b"concurrent-user-content"
    target.write_bytes(b"approved-before")
    original = J.action

    def raced(*args, **kwargs):
        target.write_bytes(b"changed-during-capture")
        return original(*args, **kwargs)

    monkeypatch.setattr(J, "action", raced)
    with pytest.raises(ValueError, match="during action preparation"):
        A.prepare(tmp_path / "stage2", desired, preimages)
    assert target.read_bytes() == b"changed-during-capture"


def test_preimage_membership_is_closed(tmp_path):
    target = str(tmp_path / "resource")
    with pytest.raises(ValueError, match="membership"):
        A.prepare(tmp_path / "stage", {target: (b"new", 0o644)}, {})
