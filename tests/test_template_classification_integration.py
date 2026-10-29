"""Trusted registration and genuine contained AL capture over synthetic classified inputs."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.execution_contract import canonical
from harness.execution_isolation import operation_popen
from harness.execution_store import get_execution
from harness.template_classification import (
    register_classification,
    selected_reference,
    validate_reference,
)
from tests.test_execution_operations import operations as operation_fixture
from tests.test_template_classification import git


@pytest.fixture
def operations(controller, monkeypatch, launched, request):
    repo = launched[2]
    (repo / "app").mkdir(exist_ok=True)
    template = repo / "app/.env.example"
    template.write_text("PUBLIC_EXAMPLE=synthetic-placeholder\n")
    template.chmod(0o644)
    ignore = repo / ".gitignore"
    ignore.write_text((ignore.read_text() if ignore.exists() else "") + "\n.env\n")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Synthetic Fixture")
    (repo / ".claude/hooks").mkdir(parents=True)
    (repo / ".claude/hooks/synthetic_hook.py").write_text("# synthetic readable hook source\n")
    (repo / ".codex").mkdir(exist_ok=True)
    (repo / ".codex/AGENTS.md").write_text("Synthetic instruction fixture.\n")
    git(repo, "add", "app/.env.example", ".gitignore", ".claude", ".codex")
    git(repo, "commit", "-qm", "synthetic classified template fixture")
    (repo / ".env").write_text("STAGING_SECRET=NEVER_PRINT_SYNTHETIC_SECRET\n")
    yield from operation_fixture.__wrapped__(controller, monkeypatch, launched, request)


@pytest.fixture
def registered(operations, monkeypatch, tmp_path):
    operation, principal, controller, initialization, package, execution, envelope = operations
    controller.conn.execute(
        "UPDATE executions SET status='Interrupted' WHERE id=?", (execution["id"],)
    )
    monkeypatch.setattr("harness.execution_hosts.managed_launcher", lambda: package["launcher"])
    state = json.loads(Path(initialization["source"]).read_bytes())
    root = Path(envelope["repository"])
    oid = git(root, "rev-parse", "HEAD:app/.env.example")
    baseline = state["baseline_head"]
    inventory = {
        "schema_version": 1,
        "repository": str(root),
        "execution": execution["id"],
        "AL_run": execution["al_run_id"],
        "baseline": baseline,
        "HEAD": git(root, "rev-parse", "HEAD"),
        "files": [
            {
                "path": "app/.env.example",
                "classification": "UNATTESTED",
                "versions": {
                    "baseline": f"100644 blob {oid}",
                    "HEAD": f"100644 blob {oid}",
                    "index": f"100644 {oid} 0",
                    "worktree": {"blob_oid": oid, "mode": "0o644", "symlink": False, "nlink": 1},
                },
            }
        ],
    }
    inventory_file = tmp_path / "inventory.json"
    inventory_file.write_text(canonical(inventory))
    inventory_sha = hashlib.sha256(inventory_file.read_bytes()).hexdigest()
    classification_file = tmp_path / "classification.json"
    classification_file.write_text(
        canonical(
            {
                "question": "Classify these exact synthetic versions?",
                "answer": "These exact synthetic versions are non-secret placeholders.",
                "inventory_sha256": inventory_sha,
                "source": "Synthetic explicit operator fixture",
            }
        )
    )
    request = (
        controller,
        execution["id"],
        inventory_file,
        inventory_sha,
        classification_file,
        hashlib.sha256(classification_file.read_bytes()).hexdigest(),
    )
    result = register_classification(*request)
    assert result["status"] == "Registered" and result["files"] == 1
    current = get_execution(controller.conn, execution["id"])
    reference = selected_reference(current, envelope, initialization)
    return operations, reference, request


def test_genuine_pinned_attribution_and_candidate_capture(registered):
    operation_data, reference, _ = registered
    operation, _, _, initial, _, _, envelope = operation_data
    profile = operation.profile(al=True)
    scripts = Path(initial["pin_root"]) / "payload/codex/skills/al/scripts"
    program = (
        "import sys,json,hashlib;from pathlib import Path;sys.path.insert(0,"
        + repr(str(scripts))
        + ");import al_scope as S,al_task_attribution as A;st=json.loads(Path("
        + repr(initial["source"])
        + ").read_bytes());capture=S._capture_candidate(st);observation"
        "=A.observe(st);names={r['path'] for r in capture['files']};a"
        "ssert 'app/.env.example' in names;assert '.env' not in names"
        ";assert observation['files'];assert len(capture['fingerprint"
        "'])==64;print('canonical capture verified')"
    )
    proc = operation_popen(
        profile,
        [sys.executable, "-I", "-c", program],
        cwd=envelope["repository"],
        env=dict(os.environ, TMPDIR=profile.temporary),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err.decode(errors="replace")
    assert out == b"canonical capture verified\n"
    assert len(validate_reference(reference)) == 1


def test_classified_read_grant_keeps_real_env_and_writes_denied(registered):
    operation_data, reference, _ = registered
    operation, _, _, _, _, _, envelope = operation_data
    root = Path(envelope["repository"])
    profile = operation.profile(["app"], al=True)
    code = (
        "from pathlib import Path\nr=Path("
        + repr(str(root))
        + ")\nassert len((r/'app/.env.example').read_bytes())>0\nfor p,mo"
        "de in [(r/'.env','rb'),(r/'app/.env.example','wb')]:\n try:\n "
        " with p.open(mode): pass\n except OSError as e:\n"
        "  if e.errno not in (1,13,30): raise\n else"
        ": raise SystemExit('protected effect permitted')\nprint('boun"
        "daries verified')"
    )
    proc = operation_popen(
        profile,
        [sys.executable, "-I", "-c", code],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err.decode(errors="replace")
    assert out == b"boundaries verified\n"
    assert len(validate_reference(reference)) == 1
    native_profile = operation.profile()
    assert str(root / "app/.env.example") in native_profile.hidden


def test_stale_or_foreign_classification_fails_before_dispatch(registered):
    operation_data, reference, _ = registered
    operation, _, controller, initial, _, execution, envelope = operation_data
    with pytest.raises(ValueError, match="template classification policy refused"):
        selected_reference(execution | {"id": "foreign-execution"}, envelope, initial)
    with pytest.raises(ValueError, match="template classification policy refused"):
        selected_reference(execution, envelope, initial | {"pin_root": "/different-pin"})
    (Path(envelope["repository"]) / "app/.env.example").write_text("changed synthetic version")
    with pytest.raises(ValueError, match="template classification policy refused"):
        validate_reference(reference)


def test_check_materialization_copy_has_exact_grant_and_revalidates(registered):
    from harness.execution_check_process import check_profile

    operation_data, reference, _ = registered
    operation, _, _, initial, _, execution, envelope = operation_data
    profile = operation.profile(al=True)
    copy = Path(profile.temporary) / "checkout"
    subprocess.run(
        ["git", "clone", "-q", envelope["repository"], str(copy)], capture_output=True, check=True
    )
    context = Path(profile.policy).parent / "check-context.json"
    context.write_text(
        canonical(
            {
                "handoff": execution["handoff_path"],
                "repository": envelope["repository"],
                "temporary": profile.temporary,
                "al_session": str(Path(initial["source"]).parent),
                "protected": list(profile.protected),
                "hidden": list(profile.hidden),
                "classification": reference,
            }
        )
    )
    checked, _ = check_profile(context, copy / "app")
    assert str(copy / "app/.env.example") not in checked.hidden
    assert str(copy / "app/.env.example") in checked.protected
    (copy / "app/.env.example").chmod(0o600)
    with pytest.raises(ValueError, match="template classification policy refused"):
        check_profile(context, copy)


def test_unsettled_attempt_refuses_operator_registration(registered):
    operation_data, _, request = registered
    _, _, controller, _, _, execution, _ = operation_data
    controller.conn.execute(
        "INSERT INTO execution_attempts(id,execution_id,token,mode,st"
        "atus,created_at,updated_at) VALUES ('uncertain',?,'uncertain"
        "','resume','Reserved','now','now')",
        (execution["id"],),
    )
    with pytest.raises(ValueError, match="template classification policy refused"):
        register_classification(*request)


def test_database_parent_enclosing_repo_preserves_writer_and_db_denials(operations):
    operation, _, _, _, _, _, envelope = operations
    root = Path(envelope["repository"])
    database = root.parent / "enclosing-state.db"
    database.write_text("synthetic private state")
    operation.envelope["database"] = str(database)
    denied = [str(database) + suffix for suffix in ("", "-wal", "-shm", "-journal")]
    for path in denied[1:]:
        Path(path).write_text("synthetic private sidecar")
    profile = operation.profile(["app"])
    code = (
        "from pathlib import Path\nPath("
        + repr(str(root / "app/ordinary.txt"))
        + ").write_text('owned writer')\nfor name in "
        + repr(denied)
        + ":\n try: Path(name).write_text('escape')\n except OSError as e:\n"
        "  if e.errno not in (1,13,30): raise\n else: raise SystemExit('database write permitted'"
        ")\nprint('write matrix verified')"
    )
    proc = operation_popen(
        profile,
        [sys.executable, "-I", "-c", code],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err.decode(errors="replace")
    assert out == b"write matrix verified\n"


def _contained(profile, root, code):
    proc = operation_popen(
        profile,
        [sys.executable, "-I", "-c", code],
        cwd=root,
        env=dict(os.environ, TMPDIR=profile.temporary),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err.decode(errors="replace")
    return out


def test_own_scratch_is_writable_inside_protected_execution_and_siblings_denied(operations):
    operation, _, _, initial, _, execution, envelope = operations
    root = Path(envelope["repository"])
    authority = Path(execution["handoff_path"]).parent
    denied = [
        authority / p
        for p in (
            "handoff.json",
            "inbox/deny.json",
            "operations/drafts/deny.txt",
            "template-classifications/deny.json",
            "worker.lock",
            "operations/other/tmp/deny.txt",
        )
    ]
    for path in denied:
        path.parent.mkdir(parents=True, exist_ok=True)
    profile = operation.profile()
    code = (
        "from pathlib import Path\nimport os\nscratch=Path("
        + repr(profile.temporary)
        + ")\n(scratch/'ordinary').write_text('owned scratch')\nfor name in "
        + repr(
            [str(p) for p in denied]
            + [str(root / "app/readonly-denied"), initial["source"], initial["coordination_lock"]]
        )
        + ":\n try: Path(name).open('ab').close()\n except OSError as e:\n"
        "  if e.errno not in (1,13,30): raise\n else: raise SystemExit('authority write permitted')"
        "\nfor target in "
        + repr([execution["handoff_path"], initial["source"]])
        + ":\n try:\n  alias=scratch/'alias';os.link(target,alias);alias."
        "write_text('escape')\n except (PermissionError,OSError): pass"
        "\n else: raise SystemExit('hardlink escape permitted')\nprint("
        "'scratch boundaries verified')"
    )
    assert _contained(profile, root, code) == b"scratch boundaries verified\n"


def test_al_session_atomic_save_and_exact_coordination_grant(operations):
    operation, _, _, initial, _, _, envelope = operations
    profile = operation.profile(al=True)
    session = Path(initial["source"]).parent
    code = (
        "from pathlib import Path\nimport os\ns=Path("
        + repr(str(session))
        + ")\n(s/'.synthetic.tmp').write_text('atomic save')\nos.replace("
        "s/'.synthetic.tmp',s/'synthetic-save.json')\nwith open("
        + repr(initial["coordination_lock"])
        + ",'ab'): pass\nprint('AL session grant verified')"
    )
    assert _contained(profile, envelope["repository"], code) == b"AL session grant verified\n"


def test_scratch_alias_and_reuse_are_refused(operations, tmp_path):
    from harness.execution_isolation import prepare_operation_isolation

    operation, _, _, _, _, _, envelope = operations
    root = Path(envelope["repository"])
    alias = tmp_path / "scratch-alias"
    alias.symlink_to(operation.directory, target_is_directory=True)
    for directory in (alias / "new", root / "model-selected-scratch"):
        with pytest.raises(ValueError, match="scratch"):
            prepare_operation_isolation(
                root, writable=[], protected=[], hidden=[], directory=directory
            )
    profile = operation.profile()
    with pytest.raises(FileExistsError):
        prepare_operation_isolation(
            root, writable=[], protected=[], hidden=[], directory=Path(profile.policy).parent
        )
    operation.directory = tmp_path / "foreign-operations"
    with pytest.raises(ValueError, match="bound execution"):
        operation.profile()


def test_materialized_check_child_cannot_write_classified_or_session(registered):
    from harness.execution_check_process import check_popen

    data, reference, _ = registered
    operation, _, _, initial, _, execution, envelope = data
    parent = operation.profile(al=True)
    copy = Path(parent.temporary) / "checkout"
    subprocess.run(
        ["git", "clone", "-q", envelope["repository"], str(copy)], capture_output=True, check=True
    )
    context = Path(parent.policy).parent / "check-context.json"
    context.write_text(
        canonical(
            {
                "handoff": execution["handoff_path"],
                "repository": envelope["repository"],
                "temporary": parent.temporary,
                "al_session": str(Path(initial["source"]).parent),
                "protected": list(parent.protected),
                "hidden": list(parent.hidden),
                "classification": reference,
            }
        )
    )
    code = (
        "from pathlib import Path\nfor name in "
        + repr(
            [
                str(copy / "app/.env.example"),
                str(Path(envelope["repository"]) / "app/.env.example"),
                initial["source"],
                initial["coordination_lock"],
            ]
        )
        + ":\n try: Path(name).open('ab').close()\n except OSError as e:\n"
        "  if e.errno not in (1,13,30): raise\n else: raise SystemExit('check write permitted')\n"
        "pri"
        "nt('check denial verified')"
    )
    proc = check_popen(
        context,
        [sys.executable, "-I", "-c", code],
        cwd=copy,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err.decode(errors="replace")
    assert out == b"check denial verified\n"


def test_linux_scratch_protected_descendant_effective_policy(tmp_path):
    import platform

    from harness.execution_isolation import prepare_operation_isolation

    if platform.system() != "Linux":
        pytest.skip(
            "Linux bubblewrap matrix requires a Linux host; macOS matrix is tested separately"
        )
    root = tmp_path / "repo"
    root.mkdir()
    profile = prepare_operation_isolation(
        root,
        writable=[],
        protected=[tmp_path / "authority"],
        hidden=[],
        directory=tmp_path / "authority/operations/profile",
    )
    protected = Path(profile.temporary) / "protected-copy"
    protected.write_text("protected synthetic bytes")
    from dataclasses import replace

    profile = replace(profile, protected=(*profile.protected, str(protected)))
    code = (
        "from pathlib import Path\ns=Path("
        + repr(profile.temporary)
        + ")\n(s/'allowed').write_text('scratch')\ntry: (s/'protected-cop"
        "y').write_text('escape')\nexcept OSError as e:\n"
        " if e.errno not in (1,13,30): raise\nelse: "
        "raise SystemExit('protected scratch write permitted')"
    )
    _contained(profile, root, code)


def test_public_operator_registration_command(registered, capsys):
    from harness.cli import build_parser
    from harness.cli_execution import cmd_execution

    _, _, request = registered
    controller, eid, inventory, inventory_sha, classification, classification_sha = request
    saved = get_execution(controller.conn, eid)
    db = controller.conn.execute("PRAGMA database_list").fetchone()["file"]
    parser = build_parser()
    args = parser.parse_args(
        [
            "--db",
            db,
            "execution",
            "classify-templates",
            saved["task_id"],
            eid,
            "--inventory",
            str(inventory),
            "--inventory-sha256",
            inventory_sha,
            "--classification",
            str(classification),
            "--classification-sha256",
            classification_sha,
        ]
    )
    assert cmd_execution(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "Registered" and result["files"] == 1


def test_classified_background_output_withheld_and_invalidated(registered):
    import time

    data, _, _ = registered
    operation, _, _, _, _, _, envelope = data
    root = Path(envelope["repository"])
    template = root / "app/.env.example"
    before = template.read_bytes()
    profile = operation.profile(al=True)
    result = operation.processes.start(
        "synthetic-owner",
        profile,
        [
            sys.executable,
            "-c",
            "import time;print('synthetic pending output',flush=True);time.sleep(.3)",
        ],
        root,
        task=None,
        revision="fixture",
    )
    handle = result["handle"]
    try:
        deadline = time.monotonic() + 5
        while not operation.processes.records[handle]["output"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert operation.processes.poll("synthetic-owner", handle)["output"] == ""
        template.write_text("externally changed synthetic version")
        while (
            operation.processes.records[handle]["status"] == "Running"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        page = operation.processes.poll("synthetic-owner", handle)
        assert page["status"] == "Failed" and page["output"] == ""
        assert "result invalid" in page["error"]
    finally:
        template.write_bytes(before)


def test_check_popen_rejects_materialized_drift_at_completion(registered):
    from harness.execution_check_process import check_popen

    data, reference, _ = registered
    operation, _, _, initial, _, execution, envelope = data
    parent = operation.profile(al=True)
    copy = Path(parent.temporary) / "checkout"
    subprocess.run(
        ["git", "clone", "-q", envelope["repository"], str(copy)], capture_output=True, check=True
    )
    context = Path(parent.policy).parent / "check-context.json"
    context.write_text(
        canonical(
            {
                "handoff": execution["handoff_path"],
                "repository": envelope["repository"],
                "temporary": parent.temporary,
                "al_session": str(Path(initial["source"]).parent),
                "protected": list(parent.protected),
                "hidden": list(parent.hidden),
                "classification": reference,
            }
        )
    )
    proc = check_popen(
        context,
        [sys.executable, "-c", "import time;time.sleep(.1);print('synthetic result')"],
        cwd=copy,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    (copy / "app/.env.example").chmod(0o600)
    with pytest.raises(ValueError, match="template classification policy refused"):
        proc.communicate(timeout=10)


def test_check_channel_rejects_drift_and_releases_owned_process(registered):
    from harness.execution_check_channel import CheckChannel

    data, reference, _ = registered
    operation, _, _, _, _, _, envelope = data
    context = operation.directory / "synthetic-channel-context.json"
    context.write_text("{}")
    template = Path(envelope["repository"]) / "app/.env.example"
    channel = CheckChannel(context, lambda: None)
    channel.classification = reference
    channel.classification_root = Path(envelope["repository"])
    channel.process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    channel.process.wait(timeout=10)
    template.chmod(0o600)
    try:
        with pytest.raises(ValueError, match="template classification policy refused"):
            channel.observe()
        assert channel.settled
    finally:
        template.chmod(0o644)
        with pytest.raises(ValueError, match="template classification policy refused"):
            channel.close()


def test_unclassified_check_keeps_nonrepository_scratch_support(operations):
    from harness.execution_check_process import check_profile

    operation, _, _, initial, _, execution, envelope = operations
    parent = operation.profile(al=True)
    setup = Path(parent.temporary) / "nonrepository-setup"
    setup.mkdir()
    context = Path(parent.policy).parent / "check-context.json"
    context.write_text(
        canonical(
            {
                "handoff": execution["handoff_path"],
                "repository": envelope["repository"],
                "temporary": parent.temporary,
                "al_session": str(Path(initial["source"]).parent),
                "protected": list(parent.protected),
                "hidden": list(parent.hidden),
            }
        )
    )
    profile, _ = check_profile(context, setup)
    assert _contained(profile, setup, "print('setup supported')") == b"setup supported\n"
