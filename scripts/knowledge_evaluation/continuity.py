"""Explicit trusted existing-task admission and exact task-local donor capability."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from harness.controller import HarnessController
from harness.execution_store import get_execution
from harness.knowledge.models import identity

from .package import canonical, canonical_path, closed, disjoint, load, sha, validate

WRAPPER = {"schema_version", "action", "package", "al_task_id", "task_files", "existing", "donor"}
EXISTING = {
    "database",
    "task_id",
    "task_request_sha256",
    "investigation_sha256",
    "specs",
    "approval_id",
    "approval_content_hash",
    "knowledge_binding",
}


def _readonly(database):
    conn = sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def knowledge_empty(conn):
    for table in (
        "knowledge_projects",
        "knowledge_snapshots",
        "knowledge_assertions",
        "knowledge_contributions",
        "knowledge_grants",
        "knowledge_lessons",
    ):
        if conn.execute("SELECT count(*) FROM " + table).fetchone()[0]:
            raise ValueError("B requires a separate never-indexed, unseeded store")


def validate_existing(package, binding):
    closed(binding, EXISTING)
    state = canonical_path(package["state_directory"])
    database, specs = canonical_path(binding["database"]), canonical_path(binding["specs"])
    if (
        not database.is_relative_to(state)
        or not specs.is_relative_to(state)
        or not database.is_file()
    ):
        raise ValueError("existing store/specs must belong to explicit state root")
    if database.stat().st_nlink != 1 or database.stat().st_uid != os.getuid():
        raise ValueError("own unaliased existing store required")
    with closing(_readonly(database)) as conn:
        controller = HarnessController(conn)
        task = controller.get_task(binding["task_id"])
        if sha(task.request.encode()) != binding["task_request_sha256"]:
            raise ValueError("existing task request binding differs")
        investigation = controller.workspace(task.id).latest_checkpoint("investigation")
        if (
            not investigation
            or investigation.payload.get("repo_root") != package["corpus"]["root"]
            or sha(canonical(investigation.payload).encode()) != binding["investigation_sha256"]
        ):
            raise ValueError("existing task observed repository binding differs")
        approval = controller.workspace(task.id).latest_approval()
        if (approval.id if approval else None) != binding["approval_id"] or (
            approval.content_hash if approval else None
        ) != binding["approval_content_hash"]:
            raise ValueError("existing immutable approval binding differs")
        if package["arm"] == "B":
            if binding["knowledge_binding"] is not None:
                raise ValueError("B cannot adopt prestaged knowledge")
            knowledge_empty(conn)
        elif binding["knowledge_binding"] is not None:
            material = binding["knowledge_binding"]
            closed(material, {"project_id", "grant_id", "material_sha256"})
            if material["project_id"] != identity(package["corpus"]["root"]):
                raise ValueError("explicit knowledge project binding differs")
            grant = conn.execute(
                "SELECT * FROM knowledge_grants WHERE grant_id=?", (material["grant_id"],)
            ).fetchone()
            if (
                not grant
                or grant["destination_project"] != material["project_id"]
                or grant["material_sha256"] != material["material_sha256"]
                or grant["revoked_at"] is not None
            ):
                raise ValueError("explicit exact-material destination grant unavailable")
        elif binding["approval_id"] is None:
            # A prestaged public demo must identify its exact knowledge grant/material.
            knowledge_empty(conn)
    return binding


def referenced(reference):
    closed(reference, {"path", "sha256"})
    path = canonical_path(reference["path"])
    value = load(path)
    if sha(path.read_bytes()) != reference["sha256"]:
        raise ValueError("immutable stage receipt hash differs")
    return value


def donor_capability(package, donor):
    closed(donor, {"admission", "completion"})
    admission, completion = referenced(donor["admission"]), referenced(donor["completion"])
    from .method import binding

    if admission.get("evaluation_method") != binding(package) or completion.get(
        "evaluation_method"
    ) != binding(package):
        raise ValueError("cross-method donor capability refused")
    if (
        completion.get("kind") != "evaluation-producer-completion-v1"
        or completion["task_id"] != admission["task_id"]
        or completion["execution_id"] != admission["execution_id"]
        or completion["repository"] != package["corpus"]["root"]
        or admission["provenance"] != package["provenance"]
    ):
        raise ValueError("donor case/project/task/authorization binding differs")
    # Legacy transcript hashes cannot be upgraded into qualified observation.
    from .observer import UNAVAILABLE

    raise ValueError(UNAVAILABLE)


def validate_stage(wrapper):
    closed(wrapper, WRAPPER)
    if (
        type(wrapper["schema_version"]) is not int
        or wrapper["schema_version"] != 1
        or wrapper["action"] not in {"donor", "recipient", "adopt"}
    ):
        raise ValueError("explicit donor/recipient/adopt stage required")
    package = wrapper["package"]
    if package.get("schema_version") == 2:
        from .method import validate_method

        validate_method(package)
        expected = wrapper["action"] if wrapper["action"] != "adopt" else "single"
        if package["participant"] != expected:
            raise ValueError("M2 stage/participant binding differs")
    existing = wrapper["existing"]
    if wrapper["action"] == "donor":
        if existing is not None or wrapper["donor"] is not None:
            raise ValueError("fresh donor requires absent existing/donor references")
        receipt = validate(package)
    else:
        # Validate the original closed case schema/corpus without weakening its absent-root rule.
        probe = dict(package)
        state = canonical_path(package["state_directory"])
        output = canonical_path(package["output_directory"])
        if not state.is_dir() or output.exists():
            raise ValueError("explicit continuation needs existing state and fresh output")
        probe["state_directory"] = str(output.parent / (output.name + "-validation-only"))
        receipt = validate(probe)
        disjoint(
            [state, output, Path(package["corpus"]["root"]), Path(__file__).resolve().parents[2]]
        )
        validate_existing(package, existing)
        if wrapper["action"] == "adopt":
            if existing["approval_id"] is not None or wrapper["donor"] is not None:
                raise ValueError("adoption is only before the first implementation approval")
            with closing(_readonly(existing["database"])) as conn:
                if conn.execute(
                    "SELECT count(*) FROM executions WHERE task_id=?", (existing["task_id"],)
                ).fetchone()[0]:
                    raise ValueError("unapproved adoption cannot reuse executions")
        else:
            capability = donor_capability(package, wrapper["donor"])
            donor_admission = referenced(wrapper["donor"]["admission"])
            if (
                donor_admission["database"] != existing["database"]
                or donor_admission["task_id"] != existing["task_id"]
                or donor_admission["approval"]["id"] != existing["approval_id"]
                or capability["al_task_id"] == wrapper["al_task_id"]
            ):
                raise ValueError("fresh recipient requires same store/task and distinct AL task")
    # Every continuity role is associated with a real bounded AL task.
    from .package import task_files, text

    text(wrapper["al_task_id"])
    scoped = dict(
        package, specification=dict(package["specification"], task_files=wrapper["task_files"])
    )
    task_files(scoped)
    if (
        package["role"] == "al-engineer"
        and package["specification"]["task_files"] != wrapper["task_files"]
    ):
        raise ValueError("Engineer stage scope differs from supplied approved design scope")
    return receipt


def read_donor(adapter, principal, arguments):
    stage = adapter.stage
    if not stage or stage["action"] != "recipient" or principal["role"] != adapter.package["role"]:
        raise ValueError("task-local recipient capability unavailable")
    with closing(_readonly(adapter.operation.database)) as conn:
        execution, _ = adapter.operation.authority(
            HarnessController(conn), principal, operation="read"
        )
    capability = donor_capability(adapter.package, stage["donor"])
    admission = referenced(stage["donor"]["admission"])
    completion = referenced(stage["donor"]["completion"])
    if admission["task_id"] != execution["task_id"] or admission["database"] != str(
        adapter.operation.database
    ):
        raise ValueError("donor capability cannot cross task/store privacy")
    from harness.knowledge.sources import read_artifact

    donor_execution = None
    with closing(_readonly(admission["database"])) as conn:
        donor_execution = get_execution(conn, admission["execution_id"])
    directory = Path(donor_execution["handoff_path"]).parent / "operations/drafts"
    index = load(directory / "index.json")
    record = index["records"][completion["report"]["artifact_id"]]
    raw = read_artifact(directory, record, maximum=262144)
    offset, limit = arguments.get("offset", 0), arguments.get("limit", 16384)
    if not 1 <= limit <= 65536:
        raise ValueError("bounded donor report page required")
    return {
        "text": raw[offset : offset + limit].decode(errors="replace"),
        "offset": offset,
        "next_offset": offset + min(limit, max(0, len(raw) - offset)),
        "truncated": offset + limit < len(raw),
        "sha256": capability["sha256"],
        "owner": capability["owner"],
        "al_task_id": capability["al_task_id"],
        "notice": "Attributed historical donor report; not approval or current verification",
    }
