"""Prepare an immutable CLI upgrade while retaining exact old launcher ownership."""

from pathlib import Path

from .bootstrap import bootstrap_cli
from .engine import R, T
from .guards import resource_adoption
from .registry import canonical, digest, namespace, read_json
from .validator_origin import capture, decode, snapshot_identity, verify_snapshot
from .validator_boot import launcher

LEGACY_RECEIPT = {
    "schema_version",
    "kind",
    "content_digest",
    "files",
    "source_root",
    "source_commit_if_available",
    "dirty",
}


def legacy_launcher(snapshot):
    return (
        "#!/usr/bin/env python3\nimport runpy, sys\nsys.dont_write_bytecode = True\n"
        "sys.path.insert(0, " + repr(str(snapshot)) + ")\n"
        "runpy.run_path(" + repr(str(snapshot / "alctl.py")) + ", run_name='__main__')\n"
    ).encode()


def owner_path(destination, state_root):
    return (
        namespace(state_root) / "cli" / ("launcher-" + digest(str(destination).encode()) + ".json")
    )


def existing_owner(destination, state_root, adoption=None):
    destination = R.safe_absolute(destination)
    path = owner_path(destination, state_root)
    actual = T.state(destination)
    if not path.exists():
        if actual:
            resource_adoption(destination, actual, adoption)
        return {
            "launcher": actual,
            "owner": None,
            "profile": "unmanaged-reviewed" if actual else "absent",
        }
    owner_raw, owner_state = capture(path)
    record = decode(owner_raw)
    basic = {"schema_version", "destination", "state", "snapshot", "receipt"}
    if (
        not isinstance(record, dict)
        or set(record) not in (basic, basic | {"validator_binding", "bootstrap_observation"})
        or type(record["schema_version"]) is not int
        or record["schema_version"] != 1
        or record["destination"] != str(destination)
        or record["state"] != actual
        or actual is None
        or actual["mode"] != 0o755
        or owner_state["mode"] != 0o600
    ):
        raise ValueError("managed global CLI owner/launcher drift")
    receipt = record["receipt"]
    snapshot = R.safe_absolute(record["snapshot"])
    if snapshot != namespace(state_root) / "cli/snapshots" / receipt["content_digest"]:
        raise ValueError("global CLI snapshot escaped its managed namespace")
    if read_json(snapshot / "receipt.json") != receipt:
        raise ValueError("global CLI receipt changed")
    if "validator_binding" in record:
        captured = verify_snapshot(snapshot, record["validator_binding"])
        expected = {
            "snapshot_identity": snapshot_identity(captured["origin"]),
            "origin_digest": captured["origin"]["origin_digest"],
            "origin_sha256": digest(capture(snapshot / "origin.json")[0]),
            "receipt_sha256": digest(capture(snapshot / "receipt.json")[0]),
        }
        if expected != record["validator_binding"]:
            raise ValueError("protected CLI binding changed")
        request = {
            "mode": "snapshot",
            "root": str(snapshot),
            "expected": expected,
            "interpreter": captured["origin"]["interpreter"]["executable"],
        }
        if destination.read_bytes() != launcher(request):
            raise ValueError("protected global CLI bootstrap shape changed")
        profile = "protected-validator-snapshot-v1"
    else:
        if (
            not isinstance(receipt, dict)
            or set(receipt) != LEGACY_RECEIPT
            or receipt["schema_version"] != 1
            or receipt["kind"] != "development"
            or not isinstance(receipt["files"], dict)
            or not receipt["files"]
            or len(receipt["files"]) > 10000
            or digest(canonical(receipt["files"])) != receipt["content_digest"]
        ):
            raise ValueError("unsupported legacy CLI ownership receipt")
        expected = {"receipt.json"} | set(receipt["files"])
        observed = set()
        for member in snapshot.rglob("*"):
            R.safe_absolute(member)
            if member.is_file():
                observed.add(member.relative_to(snapshot).as_posix())
            elif not member.is_dir():
                raise ValueError("special legacy CLI snapshot member")
        if observed != expected:
            raise ValueError("legacy CLI snapshot membership changed")
        for name, state in receipt["files"].items():
            if R.relative(name).as_posix() != name or T.state(snapshot / name) != state:
                raise ValueError("legacy CLI snapshot resource changed")
        if T.state(snapshot / "receipt.json")[
            "mode"
        ] != 0o600 or destination.read_bytes() != legacy_launcher(snapshot):
            raise ValueError("legacy CLI bootstrap shape changed")
        profile = "legacy-managed-bootstrap-v1"
    if capture(path) != (owner_raw, owner_state) or T.state(destination) != actual:
        raise ValueError("global CLI owner/launcher changed during observation")
    return {"launcher": actual, "owner": owner_state, "profile": profile}


def prepare_cli(source_root, home, state_root, directory, *, adoption=None, held=None):
    """Owned staging only; the workspace operation separately admits source/activity."""
    home = R.safe_absolute(home)
    destination = home / ".local/bin/alctl"
    directory = R.safe_absolute(directory)
    if directory.exists():
        raise ValueError("CLI preparation directory already exists")
    before = existing_owner(destination, state_root, adoption)
    prepared = bootstrap_cli(source_root, directory / "bin/alctl", state_root, held=held)
    raw = Path(prepared["destination"]).read_bytes()
    desired = dict(
        prepared, destination=str(destination), state={"sha256": digest(raw), "mode": 0o755}
    )
    metadata = owner_path(destination, state_root)
    if existing_owner(destination, state_root, adoption) != before:
        raise ValueError("global CLI changed during upgrade preparation")
    return {
        "schema_version": 1,
        "kind": "workspace-cli-preparation-v1",
        "before": before,
        "destination": str(destination),
        "launcher": {"source": prepared["destination"], "state": desired["state"]},
        "owner_path": str(metadata),
        "owner_bytes": canonical(desired) + b"\n",
        "snapshot": prepared["snapshot"],
        "validator_binding": prepared["validator_binding"],
    }
