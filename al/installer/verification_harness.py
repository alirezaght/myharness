"""Compile the explicit Harness recipe and its immutable paged contracts."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from . import verification_contracts as C
from .storage_filesystem import (
    StorageError,
    canonical,
    digest,
    publish_json,
    read_json,
    read_reference,
)
from .storage_filesystem import (
    artifact as verified_artifact,
)

DOCUMENT_BYTES = 64 * 1024**2
DOCUMENT_PAGES = 128
DOCUMENT_CHUNK = 650000


def write_document(directory, name, value):
    directory = Path(directory).absolute()
    if not C.relative(name) or "/" in name:
        raise StorageError("invalid_document_name")
    raw = canonical(value)
    if len(raw) > DOCUMENT_BYTES:
        raise StorageError("verification_document_limit")
    pages = []
    for index, offset in enumerate(range(0, len(raw), DOCUMENT_CHUNK)):
        filename = name + "-part-" + str(index).zfill(3) + ".json"
        part = raw[offset : offset + DOCUMENT_CHUNK]
        page = {
            "schema_version": 1,
            "part": index,
            "payload": base64.b64encode(part).decode("ascii"),
        }
        ref = publish_json(directory / filename, page)
        pages.append({"name": filename, "sha256": ref["sha256"], "bytes": len(part)})
    if not pages or len(pages) > DOCUMENT_PAGES:
        raise StorageError("verification_document_limit")
    return publish_json(
        directory / name,
        {
            "schema_version": 1,
            "kind": "verification-document-v1",
            "pages": pages,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    )


def read_document(reference, *, root):
    index = read_reference(reference, root=root)
    C.closed(
        index,
        {"schema_version", "kind", "pages", "bytes", "sha256"},
        "invalid_verification_document",
    )
    C.version(index["schema_version"])
    if (
        index["kind"] != "verification-document-v1"
        or not isinstance(index["pages"], list)
        or not 1 <= len(index["pages"]) <= DOCUMENT_PAGES
        or type(index["bytes"]) is not int
        or not 0 <= index["bytes"] <= DOCUMENT_BYTES
    ):
        raise StorageError("invalid_verification_document")
    directory = Path(reference["reference"]).parent
    raw, names = bytearray(), set()
    for number, row in enumerate(index["pages"]):
        C.closed(row, {"name", "sha256", "bytes"}, "invalid_verification_page")
        if (
            not C.relative(row["name"])
            or "/" in row["name"]
            or row["name"] in names
            or type(row["bytes"]) is not int
            or not 0 <= row["bytes"] <= DOCUMENT_CHUNK
        ):
            raise StorageError("invalid_verification_page")
        names.add(row["name"])
        page = read_reference(
            {
                "schema_version": 1,
                "reference": str(directory / row["name"]),
                "sha256": row["sha256"],
            },
            root=root,
        )
        C.closed(page, {"schema_version", "part", "payload"}, "invalid_verification_page")
        C.version(page["schema_version"])
        if (
            type(page["part"]) is not int
            or page["part"] != number
            or not isinstance(page["payload"], str)
        ):
            raise StorageError("invalid_verification_page")
        try:
            part = base64.b64decode(page["payload"], validate=True)
        except (ValueError, TypeError):
            raise StorageError("invalid_verification_page") from None
        if len(part) != row["bytes"]:
            raise StorageError("invalid_verification_page")
        raw.extend(part)
        if len(raw) > DOCUMENT_BYTES:
            raise StorageError("verification_document_limit")
    if len(raw) != index["bytes"] or hashlib.sha256(raw).hexdigest() != index["sha256"]:
        raise StorageError("verification_document_digest_mismatch")
    value = decode_document(bytes(raw))
    return value


def decode_document(raw):
    # Each persisted page remains <= MAX_METADATA; the reconstructed document has its own bound.
    if len(raw) > DOCUMENT_BYTES:
        raise StorageError("verification_document_limit")
    import json

    from .storage_filesystem import _invalid, _pairs

    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _invalid())
    except (ValueError, UnicodeError):
        raise StorageError("invalid_verification_document") from None


def document_materials(reference, *, root):
    read_document(reference, root=root)
    index = read_reference(reference, root=root)
    directory = Path(reference["reference"]).parent
    result = {Path(reference["reference"]).name: canonical(index)}
    for row in index["pages"]:
        result[row["name"]] = canonical(read_json(directory / row["name"]))
    return result


def collect_recipe(recipe, source_binding):
    C.validate_recipe(recipe)
    if (
        not isinstance(source_binding, dict)
        or not C.text(source_binding.get("root"))
        or not C.text(source_binding.get("content_digest"))
    ):
        raise StorageError("invalid_recipe_source_binding")
    return {
        "schema_version": 1,
        "recipe": C.RECIPE,
        "kind": "collection",
        "files": list(recipe["roots"]),
        "recipe_digest": digest(recipe),
        "source_digest": source_binding["content_digest"],
    }


def plan_recipe(recipe, collection_receipt, source_binding):
    C.validate_collection(recipe, collection_receipt, source_binding)
    selected = collection_receipt["selected"]
    all_nodes = collection_receipt["inventory"]
    files = list(dict.fromkeys(n["source"]["path"] for n in selected))
    parents = {p: p for p in files}

    def find(name):
        while parents[name] != name:
            parents[name] = parents[parents[name]]
            name = parents[name]
        return name

    def join(members):
        if not members:
            return
        root = find(members[0])
        for member in members[1:]:
            parents[find(member)] = root

    consumers = {}
    for node in selected:
        file = node["source"]["path"]
        for fixture in node["fixtures"]:
            builtin_safe = (
                fixture["builtin"]
                and fixture["name"] in recipe["grouping"]["safe_builtin_sessions"]
                and fixture["source"]["path"].startswith("external:_pytest/")
            )
            if fixture["dynamic"] and not builtin_safe:
                # An unbounded dynamic fixture reference may couple every test file.
                join(files)
            if fixture["scope"] in ("package", "session") and not builtin_safe:
                key = digest(
                    {k: fixture[k] for k in ("name", "scope", "source", "implementation", "params")}
                )
                consumers.setdefault(key, []).append(file)
    for members in consumers.values():
        join(list(dict.fromkeys(members)))
    groups = {}
    for file in files:
        groups.setdefault(find(file), []).append(file)
    if not 1 <= len(groups) <= C.MAX_GROUPS:
        raise StorageError("recipe_group_capacity_exceeded")
    rows = []
    for index, members in enumerate(groups.values()):
        inventory = [n for n in all_nodes if n["source"]["path"] in members]
        rows.append(
            {
                "id": "group-" + str(index).zfill(4),
                "files": members,
                "selected": [n for n in inventory if "browser" not in n["markers"]],
                "deselected": [n for n in inventory if "browser" in n["markers"]],
            }
        )
    manifest = {
        "schema_version": 1,
        "recipe": C.RECIPE,
        "source_digest": source_binding["content_digest"],
        "recipe_digest": digest(recipe),
        "collection": {
            "selected": [n["nodeid"] for n in selected],
            "deselected": [n["nodeid"] for n in collection_receipt["deselected"]],
            "environment": collection_receipt["environment"],
            "pytest_version": collection_receipt["pytest_version"],
        },
        "groups": rows,
    }
    manifest["manifest_digest"] = digest(manifest)
    return C.validate_group_manifest(manifest)


def reconcile_recipe(manifest, partition_receipts):
    return C.validate_reconciliation(manifest, partition_receipts)


def assignment(
    *,
    kind,
    parent,
    source_digest,
    recipe_digest,
    group_id,
    files,
    inventory,
    environment,
    directory,
):
    C.unique_nodes(inventory)
    reference = write_document(directory, "inventory.json", inventory)
    index = read_reference(reference, root=directory)
    value = {
        "schema_version": 1,
        "recipe": C.RECIPE,
        "kind": kind,
        "run": parent["run"],
        "parent": dict(parent),
        "source_digest": source_digest,
        "recipe_digest": recipe_digest,
        "group_id": group_id,
        "files": files,
        "inventory": {
            "pages": index["pages"],
            "node_count": len(inventory),
            "digest": index["sha256"],
        },
        "environment": environment,
    }
    value["assignment_digest"] = digest(value)
    C.validate_assignment(value)
    reference = publish_json(Path(directory) / "assignment.json", value)
    return value, reference


def read_assignment(reference, *, root):
    value = C.validate_assignment(read_reference(reference, root=root))
    directory = Path(reference["reference"]).parent
    inventory_index = {
        "schema_version": 1,
        "kind": "verification-document-v1",
        "pages": value["inventory"]["pages"],
        "bytes": sum(p["bytes"] for p in value["inventory"]["pages"]),
        "sha256": value["inventory"]["digest"],
    }
    inventory_reference = {
        "schema_version": 1,
        "reference": str(directory / "inventory.json"),
        "sha256": digest(inventory_index),
    }
    if read_reference(inventory_reference, root=root) != inventory_index:
        raise StorageError("assignment_inventory_index_mismatch")
    nodes = read_document(inventory_reference, root=root)
    if len(C.unique_nodes(nodes)) != value["inventory"]["node_count"]:
        raise StorageError("assignment_inventory_count_mismatch")
    return value, nodes


def report_declaration():
    from .storage_declarations import normalize

    return normalize(
        {
            "schema_version": 1,
            "scratch": [{"name": "fixtures", "class": "verification-fixtures"}],
            "outputs": [
                {
                    "name": "report",
                    "filename": "report.json",
                    "required": True,
                    "max_bytes": 1048576,
                },
                {
                    "name": "junit",
                    "filename": "junit.xml",
                    "required": True,
                    "max_bytes": 16 * 1024**2,
                },
            ]
            + [
                {
                    "name": "page" + str(n).zfill(3),
                    "filename": "page-" + str(n).zfill(3) + ".json",
                    "required": False,
                    "max_bytes": 1048576,
                }
                for n in range(C.MAX_PAGES)
            ],
            "operands": [],
        }
    )


def read_report(outputs):
    if "report" not in outputs:
        raise StorageError("missing_recipe_report")
    for artifact in outputs.values():
        if verified_artifact(artifact["path"]) != artifact:
            raise StorageError("recipe_report_artifact_changed")
    index = read_json(outputs["report"]["path"])
    C.closed(
        index, {"schema_version", "kind", "header", "pages", "nodes"}, "invalid_recipe_report_index"
    )
    C.version(index["schema_version"])
    if (
        index["kind"] != "harness-pytest-report-v1"
        or not isinstance(index["pages"], list)
        or len(index["pages"]) > C.MAX_PAGES
        or type(index["nodes"]) is not int
        or not 0 <= index["nodes"] <= C.MAX_NODES
    ):
        raise StorageError("invalid_recipe_report_index")
    items, used = [], set()
    for row in index["pages"]:
        C.closed(row, {"name", "sha256", "nodes"}, "invalid_recipe_report_page")
        if (
            row["name"] in used
            or row["name"] not in outputs
            or not row["name"].startswith("page")
            or outputs[row["name"]]["sha256"] != row["sha256"]
        ):
            raise StorageError("missing_or_changed_recipe_report_page")
        used.add(row["name"])
        page = read_json(outputs[row["name"]]["path"])
        C.closed(page, {"schema_version", "items"}, "invalid_recipe_report_page")
        C.version(page["schema_version"])
        if not isinstance(page["items"], list) or len(page["items"]) != row["nodes"]:
            raise StorageError("invalid_recipe_report_page")
        items.extend(page["items"])
        if len(items) > C.MAX_NODES:
            raise StorageError("recipe_report_node_limit")
    if len(items) != index["nodes"] or {n for n in outputs if n.startswith("page")} != used:
        raise StorageError("incomplete_recipe_report_pages")
    selected, deselected, inventory, reports = [], [], [], {}
    for item in items:
        C.closed(item, {"node", "selected", "reports"}, "invalid_recipe_report_item")
        if type(item["selected"]) is not bool or not isinstance(item["reports"], list):
            raise StorageError("invalid_recipe_report_item")
        inventory.append(item["node"])
        (selected if item["selected"] else deselected).append(item["node"])
        if item["reports"]:
            node = item["node"]["nodeid"]
            if node in reports:
                raise StorageError("duplicate_recipe_report_node")
            reports[node] = item["reports"]
    return index["header"] | {
        "selected": selected,
        "deselected": deselected,
        "inventory": inventory,
        "reports": reports,
    }


CHILD_PACKAGE_FILES = (
    "storage_filesystem.py",
    "storage_declarations.py",
    "verification_contracts.py",
    "verification_harness.py",
    "verification_pytest.py",
    "verification_harness_child.py",
)
CHILD_PACKAGE = "_al_harness_recipe_child_v1"
CHILD_INITIALIZER = b'"""Fixed observation-only Harness verification child."""\n'


def child_source_inventory():
    """Fixed members from this already-selected canonical package; no search fallback."""
    import sys

    from .storage_filesystem import source_bytes

    root = Path(__file__).absolute().parent
    package = sys.modules.get(__package__)
    binding = getattr(package, "__storage_binding__", None)
    context = sys.modules.get("_al_validator_context")
    sources = {}
    for filename in CHILD_PACKAGE_FILES:
        raw = source_bytes(root / filename)
        if binding is not None and binding.get(filename) != hashlib.sha256(raw).hexdigest():
            raise StorageError("canonical_recipe_source_changed")
        if context is not None:
            expected = context.captured["bytes"].get("installer/" + filename)
            if root != Path(context.captured["snapshot"]) / "installer" or raw != expected:
                raise StorageError("canonical_recipe_source_changed")
        sources[filename] = raw
    return sources


def materialize_child(temporary, reference, *, trusted_root, launch_token, interpreter):
    """Materialize only a validated assignment and fixed code within existing check scratch."""
    import os

    from .storage_filesystem import _open_directory

    temporary = Path(temporary).absolute()
    assignment_value, _ = read_assignment(reference, root=trusted_root)
    parent = Path(reference["reference"]).parent
    assignment_index = read_reference(reference, root=trusted_root)
    inventory_index = read_json(parent / "inventory.json")
    materials = {
        "assignment.json": canonical(assignment_index),
        "inventory.json": canonical(inventory_index),
    }
    for page in assignment_value["inventory"]["pages"]:
        materials[page["name"]] = canonical(
            read_reference(
                {
                    "schema_version": 1,
                    "reference": str(parent / page["name"]),
                    "sha256": page["sha256"],
                },
                root=trusted_root,
            )
        )
    directory = temporary / "recipe-input"
    descriptor = _open_directory(directory, create=True)
    try:
        for name, raw in materials.items():
            if not C.relative(name) or "/" in name:
                raise StorageError("invalid_recipe_input_name")
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=descriptor
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sources = child_source_inventory()
    package = directory / CHILD_PACKAGE
    package.mkdir(mode=0o700)
    for filename, raw in {"__init__.py": CHILD_INITIALIZER, **sources}.items():
        path = package / filename
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o400)
    outputs = {
        row["name"]: str(temporary / "outputs" / row["filename"])
        for row in report_declaration()["outputs"]
    }
    scratch = temporary / "scratch/fixtures"
    from .storage_filesystem import canonical as encode

    bootstrap = (
        "import sys;sys.path.insert(0," + repr(str(directory)) + ");"
        "from " + CHILD_PACKAGE + ".verification_harness_child import main;raise SystemExit(main())"
    )
    argv = [
        interpreter,
        "-I",
        "-B",
        "-c",
        bootstrap,
        str(directory / "assignment.json"),
        launch_token,
        str(scratch),
        encode(outputs).decode(),
    ]
    # Every byte copied for execution is re-observed; child mutation grants no authority.
    from .storage_filesystem import source_bytes

    if any(source_bytes(package / name) != raw for name, raw in sources.items()):
        raise StorageError("materialized_recipe_code_changed")
    if (
        read_assignment(
            {
                "schema_version": 1,
                "reference": str(directory / "assignment.json"),
                "sha256": reference["sha256"],
            },
            root=directory,
        )[0]
        != assignment_value
    ):
        raise StorageError("materialized_recipe_assignment_changed")
    return argv


def logical_argv(kind):
    if kind not in ("collection", "partition"):
        raise StorageError("invalid_recipe_child_kind")
    return ["python", "--al-harness-recipe-v1", kind]


def document_artifacts(reference, *, root):
    read_document(reference, root=root)
    index = read_reference(reference, root=root)
    directory = Path(reference["reference"]).parent
    return [
        verified_artifact(reference["reference"]),
        *(verified_artifact(directory / p["name"]) for p in index["pages"]),
    ]


def validate_xml(outputs, result, *, kind):
    import xml.etree.ElementTree as ET

    if "junit" not in outputs:
        raise StorageError("missing_recipe_junit")
    expected = outputs["junit"]
    if verified_artifact(expected["path"], maximum=16 * 1024**2) != expected:
        raise StorageError("recipe_junit_changed")
    try:
        tree = ET.parse(expected["path"])
    except (OSError, ET.ParseError):
        raise StorageError("invalid_recipe_junit") from None
    cases = list(tree.getroot().iter("testcase"))
    outcomes = result["outcomes"]
    if kind == "collection":
        if cases:
            raise StorageError("collection_junit_has_executed_cases")
    else:
        if len(cases) != len(outcomes):
            raise StorageError("recipe_junit_count_mismatch")
        failed = sum(bool(c.findall("failure") or c.findall("error")) for c in cases)
        skipped = sum(bool(c.findall("skipped")) for c in cases)
        if failed != sum(v in {"failed", "error"} for v in outcomes.values()) or skipped != sum(
            v in {"skipped", "xfailed"} for v in outcomes.values()
        ):
            raise StorageError("recipe_junit_outcome_mismatch")
    if verified_artifact(expected["path"], maximum=16 * 1024**2) != expected:
        raise StorageError("recipe_junit_changed")


def result_summary(result):
    return {
        k: result[k]
        for k in (
            "accounting_complete",
            "selected_success",
            "structural_errors",
            "outcomes",
            "counts",
        )
    } | {
        "producer_closed": result.get("producer_closed") is True,
        "capture_state": "protected_capture",
    }


def process_binding(launch_token, completion):
    return {
        "schema_version": 2,
        "producer": C.settlement_observation(completion),
        "launch_token": launch_token,
        "exit": completion.get("exit"),
        "complete": completion.get("complete") is True,
        "group_absent": completion.get("group_absent") is True,
        "errors": list(completion.get("errors") or []),
        "unresolved": bool(completion.get("unresolved")),
    }


def validate_published_child(entry, reference, *, root, completion=None, launch_token=None):
    from .storage_declarations import evidence_binding
    from .storage_filesystem import evidence_log_digest

    C.closed(
        entry["recipe_assignment"],
        {"schema_version", "parent", "assignment", "assignment_digest", "kind", "group_id"},
        "invalid_recipe_assignment_binding",
    )
    binding = entry["recipe_assignment"]
    assignment_value, nodes = read_assignment(binding["assignment"], root=root)
    if (
        assignment_value["parent"] != binding["parent"]
        or assignment_value["assignment_digest"] != binding["assignment_digest"]
        or assignment_value["kind"] != binding["kind"]
        or assignment_value["group_id"] != binding["group_id"]
    ):
        raise StorageError("recipe_assignment_binding_changed")
    publication = read_reference(reference, root=root)
    fields = {"schema_version", "binding", "entry", "artifacts", "outputs"}
    if isinstance(publication, dict) and publication.get("schema_version") == 3:
        fields |= {"producer", "capture_state"}
    C.closed(publication, fields, "invalid_recipe_publication")
    if (
        type(publication["schema_version"]) is not int
        or publication["schema_version"] not in (2, 3)
        or publication["entry"] != evidence_binding(entry)
    ):
        raise StorageError("recipe_publication_entry_changed")
    allocation = entry["storage_allocation"]
    if publication["binding"] != {
        "owner": allocation["owner"],
        "allocation_id": allocation["allocation_id"],
        "declaration_digest": allocation["declaration_digest"],
    }:
        raise StorageError("recipe_publication_allocation_changed")
    if (
        publication["outputs"] != entry.get("storage_outputs")
        or not isinstance(publication["artifacts"], list)
        or not 1 <= len(publication["artifacts"]) <= 256
    ):
        raise StorageError("recipe_publication_output_changed")
    for observed in publication["artifacts"]:
        path = Path(observed["path"])
        if not path.is_relative_to(root) and path != Path(entry["log"]):
            raise StorageError("recipe_artifact_outside_publication")
        if verified_artifact(path) != observed:
            raise StorageError("recipe_artifact_changed")
    original_log = verified_artifact(entry["log"])
    log_copy = entry["recipe_log_copy"]
    if (
        original_log not in publication["artifacts"]
        or log_copy not in publication["artifacts"]
        or log_copy["sha256"] != original_log["sha256"]
        or not Path(log_copy["path"]).is_relative_to(root)
        or evidence_log_digest(entry["log"]) != entry["log_sha256"]
    ):
        raise StorageError("recipe_log_provenance_changed")
    recorded_process = entry["recipe_process"]
    process_fields = {
        "schema_version",
        "launch_token",
        "exit",
        "complete",
        "group_absent",
        "errors",
        "unresolved",
    }
    if isinstance(recorded_process, dict) and recorded_process.get("schema_version") == 2:
        process_fields.add("producer")
    C.closed(recorded_process, process_fields, "invalid_recipe_process")
    if type(recorded_process["schema_version"]) is not int or recorded_process[
        "schema_version"
    ] not in (1, 2):
        raise StorageError("invalid_recipe_process")
    if completion is not None and recorded_process != process_binding(launch_token, completion):
        raise StorageError("recipe_process_not_actual_owner")
    if (
        not recorded_process["complete"]
        or not recorded_process["group_absent"]
        or recorded_process["errors"]
        or recorded_process["unresolved"]
    ):
        raise StorageError("recipe_process_unsettled")
    if (
        entry["exit"] != recorded_process["exit"]
        or entry["fp_before"] != entry["fp_after"]
        or entry["fp_before"] != assignment_value["source_digest"]
    ):
        raise StorageError("recipe_source_or_exit_changed")
    report = read_report(publication["outputs"])
    result = C.validate_child_report(
        assignment_value,
        report,
        {
            "returncode": recorded_process["exit"],
            "launch_token": recorded_process["launch_token"],
            "assigned_nodes": nodes if assignment_value["kind"] == "partition" else None,
        },
    )
    validate_xml(publication["outputs"], result, kind=assignment_value["kind"])
    stored = read_document(entry["recipe_result"]["validation"], root=root)
    producer_closed = C.producer_settled(recorded_process.get("producer", {}))
    if entry["recipe_result"].get("schema_version") == 2:
        result.update(producer_closed=producer_closed, capture_state="protected_capture")
    if publication["schema_version"] == 3 and (
        publication["producer"] != recorded_process.get("producer")
        or publication["producer"] != entry.get("storage_settlement")
        or publication["capture_state"] != "protected_capture"
    ):
        raise StorageError("recipe_producer_observation_changed")
    if stored != result or entry["counts"] != result["counts"]:
        raise StorageError("recipe_result_not_derived_from_report")
    expected_status = (
        "PASS"
        if result["selected_success"]
        else "FAIL"
        if result["accounting_complete"]
        else "INFRA"
    )
    if entry["status"] != expected_status:
        raise StorageError("recipe_status_not_derived_from_report")
    result.update(producer_closed=producer_closed, capture_state="protected_capture")
    return result, publication


def reconcile_closure(reference, *, root, entries, completions=None, current_source):
    aggregate = read_document(reference, root=root)
    C.closed(
        aggregate,
        {
            "schema_version",
            "kind",
            "parent",
            "recipe",
            "manifest",
            "bindings",
            "collection",
            "receipts",
            "result",
        },
        "invalid_recipe_aggregate",
    )
    if type(aggregate["schema_version"]) is not int or aggregate["schema_version"] not in (1, 2):
        raise StorageError("unsupported_recipe_aggregate")
    if aggregate["kind"] != (
        "harness-recipe-aggregate-v1"
        if aggregate["schema_version"] == 1
        else "harness-recipe-aggregate-v2"
    ):
        raise StorageError("invalid_recipe_aggregate")
    parent = aggregate["parent"]
    C.closed(parent, {"run", "id", "reservation"}, "invalid_recipe_parent")
    parent_entry = entries.get(parent["id"])
    if (
        not parent_entry
        or any(parent_entry.get(k) != v for k, v in parent.items())
        or not parent_entry.get("recipe")
    ):
        raise StorageError("recipe_parent_entry_changed")
    recipe = C.validate_recipe(aggregate["recipe"])
    manifest = C.validate_group_manifest(read_document(aggregate["manifest"], root=root))
    bindings = read_document(aggregate["bindings"], root=root)
    C.closed(
        bindings,
        {"schema_version", "parent", "manifest_digest", "children"},
        "invalid_recipe_execution_bindings",
    )
    C.version(bindings["schema_version"])
    if (
        bindings["parent"] != parent
        or bindings["manifest_digest"] != manifest["manifest_digest"]
        or manifest["source_digest"] != current_source
        or manifest["recipe_digest"] != digest(recipe)
    ):
        raise StorageError("recipe_closure_source_changed")
    if (
        not isinstance(bindings["children"], list)
        or len(bindings["children"]) != len(manifest["groups"]) + 1
    ):
        raise StorageError("incomplete_recipe_execution_bindings")
    expected_groups = {"collection", *(g["id"] for g in manifest["groups"])}
    observed, verified = set(), {}
    all_receipts = [aggregate["collection"], *aggregate["receipts"]]
    for child in bindings["children"]:
        C.closed(
            child,
            {"group_id", "entry", "assignment", "assignment_digest", "kind"},
            "invalid_recipe_execution_binding",
        )
        group = child["group_id"]
        if group not in expected_groups or group in observed:
            raise StorageError("duplicate_or_foreign_recipe_execution")
        observed.add(group)
        C.closed(child["entry"], {"run", "id", "reservation"}, "invalid_recipe_child_entry")
        entry = entries.get(child["entry"]["id"])
        if not entry or any(entry.get(k) != v for k, v in child["entry"].items()):
            raise StorageError("recipe_child_reservation_changed")
        assignment = entry.get("recipe_assignment", {})
        if assignment.get("parent") != parent or any(
            assignment.get(k) != child[k]
            for k in ("group_id", "assignment", "assignment_digest", "kind")
        ):
            raise StorageError("recipe_child_assignment_changed")
        receipts = [r for r in all_receipts if r.get("group_id") == group]
        if len(receipts) != 1:
            raise StorageError("missing_or_duplicate_recipe_receipt")
        receipt = receipts[0]
        if (
            receipt["entry"] != child["entry"]
            or receipt["assignment_digest"] != child["assignment_digest"]
        ):
            raise StorageError("recipe_receipt_binding_changed")
        process = completions.get(entry["id"]) if completions is not None else None
        if completions is not None and process is None:
            raise StorageError("recipe_owned_completion_missing")
        result, publication = validate_published_child(
            entry,
            receipt["publication"],
            root=root,
            completion=process["completion"] if process else None,
            launch_token=process["launch_token"] if process else None,
        )
        expected_summary = result_summary(result)
        if aggregate["schema_version"] == 1 and "producer_closed" not in receipt["result"]:
            expected_summary = {
                k: expected_summary[k]
                for k in (
                    "accounting_complete",
                    "selected_success",
                    "structural_errors",
                    "outcomes",
                    "counts",
                )
            }
        if expected_summary != receipt["result"]:
            raise StorageError("recipe_receipt_result_changed")
        verified[group] = (result, publication)
    if observed != expected_groups:
        raise StorageError("incomplete_recipe_execution_bindings")
    collected = verified["collection"][0]
    if not collected["selected_success"] or not collected["accounting_complete"]:
        raise StorageError("recipe_collection_unverified")
    receipt = {
        "schema_version": 1,
        "selected": collected["selected"],
        "deselected": collected["deselected"],
        "inventory": collected["inventory"],
        "source_digest": current_source,
        "recipe_digest": digest(recipe),
        "pytest_version": collected["pytest_version"],
        "environment": collected["environment"],
    }
    if plan_recipe(recipe, receipt, {"content_digest": current_source}) != manifest:
        raise StorageError("recipe_group_manifest_not_from_collection")
    actual_receipts = [
        r | {"result": result_summary(verified[r["group_id"]][0])} for r in aggregate["receipts"]
    ]
    result = reconcile_recipe(manifest, actual_receipts)
    published_result = aggregate["result"]
    if aggregate["schema_version"] == 1:
        comparable = (
            "manifest_digest",
            "accounting_complete",
            "selected_success",
            "structural_errors",
            "outcomes",
            "counts",
        )
        if any(result.get(k) != published_result.get(k) for k in comparable):
            raise StorageError("recipe_aggregate_result_changed")
    elif result != published_result:
        raise StorageError("recipe_aggregate_result_changed")
    closure_artifacts = []
    for ref in (reference, aggregate["manifest"], aggregate["bindings"]):
        closure_artifacts.extend(document_artifacts(ref, root=root))
    return {
        "aggregate": aggregate,
        "result": result,
        "artifacts": closure_artifacts,
        "collection_eligible": result["accounting_complete"]
        and collected["selected_success"]
        and collected.get("producer_closed") is True,
    }


def current_parent_recipe(parent, repository):
    identity = C.validate_recipe_identity(parent["recipe"])
    path = Path(repository) / C.RECIPE_PATH
    if verified_artifact(path, maximum=65536)["sha256"] != identity["source_sha256"]:
        raise StorageError("recipe_source_changed")
    recipe = C.validate_recipe(read_json(path))
    if digest(recipe) != identity["recipe_digest"]:
        raise StorageError("recipe_source_changed")
    return recipe
