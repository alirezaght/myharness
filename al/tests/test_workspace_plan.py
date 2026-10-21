"""Planning primitives refuse manufactured registration and changed reviewed inputs."""

import copy

import pytest

from al.installer import operations as O
from al.installer import workspace_inputs as I
from al.installer import workspace_plan as P
from al.installer.engine import T
from al.installer.registry import canonical, digest, write_json
from al.installer.targets import resolve_target


def test_request_requires_literal_identity_and_hash_bound_artifacts(tmp_path):
    request = {"home": str(tmp_path), "session": "session", "run": "run", "platform": "codex"}
    assert I.request(request)["reviews"] == {"reference": None, "value": {}}
    for change in (
        {"approved": True},
        {"profile": "ordinary-terminal-v1"},
        {"session": "../foreign"},
        {"platform": "both"},
        {"runtime_roots": "omit"},
        {"reviews": {"reviewed": True}},
    ):
        with pytest.raises(ValueError):
            I.request(dict(request, **change))
    path = tmp_path / "review.json"
    write_json(path, {"instructions": {"reviewed": True, "reason": "Disposable reviewed inputs"}})
    reference = dict(path=str(path), **T.state(path))
    planned = I.request(dict(request, reviews=reference))
    assert planned["reviews"]["reference"] == reference
    assert path.read_bytes() == canonical(planned["reviews"]["value"]) + b"\n"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="bytes or mode changed"):
        I.request(dict(request, reviews=reference))
    path.chmod(0o600)
    path.write_text('{"instructions":{},"instructions":{}}')
    with pytest.raises(ValueError, match="bytes or mode changed"):
        I.request(dict(request, reviews=reference))
    with pytest.raises(ValueError, match="duplicate"):
        I.artifact(dict(path=str(path), **T.state(path)))


def test_preparation_identity_hashes_private_bytes_and_roundtrips(tmp_path):
    raw = b'private_configuration = "not printed"\n'
    bound = I.identity({"configs": {"raw": raw}, "removals": [("path", None)], "root": tmp_path})
    assert bound == {
        "configs": {"raw": {"kind": "prepared-bytes", "sha256": digest(raw), "size": len(raw)}},
        "removals": [["path", None]],
        "root": str(tmp_path),
    }
    assert b"not printed" not in canonical(bound)
    with pytest.raises(ValueError, match="unsupported"):
        I.identity({"float": 1.5})


def test_a_self_consistent_plan_is_not_registered_authority(tmp_path):
    # Registration alone is a byte-identity test, never a manufactured terminal proof.
    body = {key: None for key in P.FIELDS - {"digest"}}
    body.update(schema_version=1, kind="approved-workspace-global", state_root=str(tmp_path))
    value = I.sealed(body)
    with pytest.raises(ValueError, match="not the immutable registered"):
        P.require_registered(value, tmp_path)
    path = P.plan_path(tmp_path, value)
    write_json(path, value)
    assert P.require_registered(value, tmp_path) == value
    changed = I.sealed(dict({k: v for k, v in value.items() if k != "digest"}, approved=True))
    with pytest.raises(ValueError, match="invalid workspace plan"):
        P.require_registered(changed, tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="not the immutable registered"):
        P.require_registered(value, tmp_path)
    for key in ("../outside", "z" * 64, None):
        with pytest.raises(ValueError, match="invalid workspace plan identity"):
            P.plan_path(tmp_path, {"digest": key})


def test_extracted_target_overlap_keeps_identity_and_destination_refusals(tmp_path):
    target = resolve_target(home=tmp_path)
    assert O.check_target_overlap(target, [{"target": target}]) is None
    changed = copy.deepcopy(target)
    changed["target_id"] = "f" * 64
    with pytest.raises(ValueError, match="overlapping installation target identity"):
        O.check_target_overlap(target, [{"target": changed}])
    nested = resolve_target(home=tmp_path / ".codex")
    # A nested home without overlapping managed destinations is a distinct target.
    O.check_target_overlap(target, [{"target": nested}])
    nested["root"] = str(tmp_path / ".codex/skills/al")
    with pytest.raises(ValueError, match="overlapping installation destinations"):
        O.check_target_overlap(target, [{"target": nested}])
