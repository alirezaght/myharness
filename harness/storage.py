"""Trusted adapters for the single canonical disposable-storage implementation."""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
import stat
import sys
import types
from pathlib import Path

CORE_MODULES = (
    "storage_filesystem.py",
    "storage_lifecycle.py",
    "storage_inventory.py",
    "storage_declarations.py",
    "verification_contracts.py",
    "verification_harness.py",
)
CHILD_MODULES = ("verification_pytest.py", "verification_harness_child.py")
CANONICAL_MODULES = (*CORE_MODULES, *CHILD_MODULES)
CHECK_HANDLE_CAPACITY = 1025
INERT_INITIALIZER = b'"""Sealed canonical storage implementation."""\n'


def harness_home():
    return (
        Path(os.environ["HARNESS_HOME"])
        if os.environ.get("HARNESS_HOME")
        else Path.home() / ".local/share/harness"
    ).absolute()


def _module_bytes(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("storage source alias refused")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 256 * 1024:
        raise ValueError("storage source unavailable")
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("storage source changed")
    return raw


def _layout():
    package = Path(__file__).absolute().parent
    marker = package.parent / "manifest.json"
    if marker.exists():
        if marker.is_symlink() or marker.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("storage bridge manifest unavailable")
        manifest = json.loads(marker.read_bytes())
        directory = package / "_storage_core"
        allowed = {*CANONICAL_MODULES, "__init__.py", "__pycache__"}
        if not directory.is_dir() or any(p.name not in allowed for p in directory.iterdir()):
            raise ValueError("storage bridge inventory changed")
        files = {
            name: _module_bytes(directory / name) for name in (*CANONICAL_MODULES, "__init__.py")
        }
        for name, raw in files.items():
            if (
                manifest.get("files", {}).get("_storage_core/" + name)
                != hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError("storage bridge member changed")
        return directory, files, True
    directory = package.parent / "al" / "installer"
    files = {name: _module_bytes(directory / name) for name in CANONICAL_MODULES}
    files["__init__.py"] = INERT_INITIALIZER
    return directory, files, False


def core_sources():
    """Only fixed paired-package bytes enter normal and prepared sealed snapshots."""
    return _layout()[1]


def child_sources():
    """Fixed child-only source bytes are verified but never imported by the operator."""
    return {name: raw for name, raw in _layout()[1].items() if name in CHILD_MODULES}


def load_core():
    directory, files, sealed = _layout()
    binding = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    name = "_harness_storage_core_" + hashlib.sha256(str(directory).encode()).hexdigest()[:16]
    package = sys.modules.get(name)
    if package is None:
        package = types.ModuleType(name)
        package.__package__ = name
        package.__path__ = [str(directory)]
        package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        package.__storage_binding__ = binding
        package.__storage_directory__ = str(directory)
        sys.modules[name] = package
    elif getattr(package, "__storage_binding__", None) != binding or getattr(
        package, "__storage_directory__", None
    ) != str(directory):
        raise ValueError("storage source changed after import")
    result = {}
    for filename in CORE_MODULES:
        qualified = name + "." + filename.removesuffix(".py")
        module = sys.modules.get(qualified)
        if module is None:
            module = types.ModuleType(qualified)
            module.__package__ = name
            module.__file__ = str(directory / filename)
            module.__spec__ = importlib.machinery.ModuleSpec(qualified, loader=None)
            module.__storage_digest__ = binding[filename]
            sys.modules[qualified] = module
            try:
                exec(compile(files[filename], module.__file__, "exec"), module.__dict__)
            except BaseException:
                sys.modules.pop(qualified, None)
                raise
        if (
            Path(module.__file__).absolute() != directory / filename
            or getattr(module, "__storage_digest__", None) != binding[filename]
        ):
            raise ValueError("storage module origin mismatch")
        result[filename.removesuffix(".py")] = module
    return result


def manager_for_operator(*, root=None, create=False, policy=None):
    core = load_core()
    return core["storage_lifecycle"].StorageManager(
        Path(root).absolute() if root else harness_home() / "managed-storage",
        create=create,
        policy=policy,
    )


class ExecutionStorage:
    """The private channel supplies live authority; requests supply no cleanup path."""

    def __init__(self, context, validate_entry):
        self.context = context
        self.validate_entry = validate_entry
        self.core = load_core()
        self.manager = None
        self.records = {}

    def _manager(self):
        if self.manager is None:

            def owner(record, reference):
                current = self.records.get(record["owner"]["identity"])
                if not current or reference != current.get("publication_reference"):
                    raise ValueError("storage check publication unverified")
                return current["completion"]

            def publication(record, reference):
                current = self.records.get(record["owner"]["identity"])
                if not current or reference != current.get("publication_reference"):
                    raise ValueError("storage check publication unverified")
                return current["attestation"]

            self.manager = self.core["storage_lifecycle"].StorageManager(
                self.context["manager_root"],
                verify_owner=owner,
                verify_publication=publication,
                protected_roots=self.context.get("protected_roots", ()),
            )
        return self.manager

    def begin(self, handle, request, argv):
        grammar = self.core["storage_declarations"]
        if (
            not isinstance(request, dict)
            or type(request.get("schema_version")) is not int
            or not (
                request["schema_version"] == 1
                and set(request) == {"schema_version", "declaration", "entry"}
                or request["schema_version"] == 2
                and set(request) == {"schema_version", "declaration", "entry", "recipe_input"}
            )
            or len(self.records) >= CHECK_HANDLE_CAPACITY
        ):
            raise ValueError("invalid managed check request")
        declaration = grammar.normalize(request["declaration"])
        entry = self.validate_entry(request["entry"], declaration, final=False)
        if entry["argv"] != list(argv):
            raise ValueError("managed check differs from reserved command")
        recipe_input = request.get("recipe_input")
        if recipe_input is not None:
            compiler = self.core["verification_harness"]
            if (
                recipe_input != entry.get("recipe_assignment")
                or declaration != compiler.report_declaration()
            ):
                raise ValueError("recipe check differs from protected assignment")
            assignment, _ = compiler.read_assignment(
                recipe_input["assignment"], root=self.context["publication_root"]
            )
            if (
                assignment["parent"] != recipe_input["parent"]
                or assignment["assignment_digest"] != recipe_input["assignment_digest"]
                or assignment["source_digest"] != entry["fp_before"]
                or argv != compiler.logical_argv(assignment["kind"])
            ):
                raise ValueError("recipe assignment binding mismatch")
        owner = {
            "adapter_kind": "harness",
            "identity": handle,
            "reservation": entry["reservation"],
            "execution": self.context["execution"],
            "epoch": self.context["epoch"],
            "authority_generation": self.context["authority_generation"],
            "principal": self.context["principal"],
            "run": entry["run"],
            "entry": entry["id"],
        }
        allocation = self._manager().allocate(
            owner=owner,
            allocation_class=grammar.allocation_class(declaration),
            declaration_digest=grammar.declaration_digest(declaration),
        )
        operation = Path(allocation["path"]) / "operation"
        temporary = operation / "tmp"
        rendered = grammar.render_argv(argv, declaration, temporary)
        result = {
            "schema_version": 1,
            "pressure": self.core["storage_inventory"].pressure_summary(
                self._manager().last_pressure, self._manager().policy
            ),
            "allocation": allocation,
            "declaration": declaration,
            "entry": request["entry"],
            "operation_directory": str(operation),
        }
        if recipe_input is not None:
            result["recipe_input"] = recipe_input
            result["recipe_root"] = self.context["publication_root"]
            result["launch_token"] = handle
        self.records[handle] = result
        return result, rendered

    def completed(self, handle, completion):
        current = self.records.get(handle)
        if current is not None:
            if (
                current.get("kernel_completion") is not None
                and current["kernel_completion"] != completion
            ):
                raise ValueError("conflicting owned check completion")
            if current.get("kernel_completion") is None:
                current["kernel_completion"] = dict(completion)
                if not self.core["verification_contracts"].producer_settled(completion):
                    self._manager().retain(
                        current["allocation"]["allocation_id"],
                        reason="descendant_settlement_unproven",
                    )

    def started(self, handle, pid):
        current = self.records[handle]
        allocation = current["allocation"]
        self._manager().mark_running(
            allocation["allocation_id"],
            generation=allocation["generation"],
            owner_receipt={"handle": handle, "pid": pid},
        )

    def retain(self, handle, reason):
        current = self.records.get(handle)
        if current and not current.get("cleanup"):
            self._manager().retain(current["allocation"]["allocation_id"], reason=reason)

    def acknowledge(self, handle, reference, completion, *, reconciliation=None):
        current = self.records.get(handle)
        if current is None:
            raise ValueError("managed check handle mismatch")
        if reconciliation is not None and not current.get("recipe_input"):
            raise ValueError("ordinary check has no recipe reconciliation")
        if current.get("recipe_input") is not None:
            return self._acknowledge_recipe(handle, reference, completion, reconciliation)
        entry = self.validate_entry(current["entry"], current["declaration"], final=True)
        if current.get("publication_reference") and current["publication_reference"] != reference:
            raise ValueError("conflicting managed publication")
        fs = self.core["storage_filesystem"]
        publication = fs.read_reference(reference, root=self.context["publication_root"])
        allocation = current["allocation"]
        if entry.get("storage_allocation") != allocation:
            raise ValueError("committed storage allocation mismatch")
        binding = {
            "owner": allocation["owner"],
            "allocation_id": allocation["allocation_id"],
            "declaration_digest": allocation["declaration_digest"],
        }
        if (
            not isinstance(publication, dict)
            or type(publication.get("schema_version")) is not int
            or not (
                publication["schema_version"] == 1
                and set(publication)
                == {"schema_version", "binding", "entry", "artifacts", "outputs"}
                or publication["schema_version"] == 3
                and set(publication)
                == {
                    "schema_version",
                    "binding",
                    "entry",
                    "artifacts",
                    "outputs",
                    "producer",
                    "capture_state",
                }
            )
            or publication["binding"] != binding
            or publication["entry"] != self.core["storage_declarations"].evidence_binding(entry)
        ):
            raise ValueError("managed publication binding mismatch")
        if publication["schema_version"] == 3:
            observed = self.core["verification_contracts"].settlement_observation(completion)
            if (
                publication["producer"] != observed
                or entry.get("storage_settlement") != observed
                or publication["capture_state"] != "protected_capture"
            ):
                raise ValueError("managed producer observation differs from actual owner")
        artifacts = publication["artifacts"]
        if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 64:
            raise ValueError("invalid managed publication artifacts")
        for artifact in artifacts:
            path = Path(artifact["path"])
            if not path.is_relative_to(self.context["publication_root"]) and path != Path(
                entry["log"]
            ):
                raise ValueError("managed publication outside trusted root")
            if fs.artifact(path) != artifact:
                raise ValueError("managed publication artifact changed")
        log = fs.artifact(entry["log"])
        if log not in artifacts:
            raise ValueError("managed publication omits committed log")
        if fs.evidence_log_digest(entry["log"]) != entry.get("log_sha256"):
            raise ValueError("committed managed log digest mismatch")
        outputs = publication["outputs"]
        if (
            not isinstance(outputs, dict)
            or outputs != entry.get("storage_outputs", {})
            or set(outputs) - {r["name"] for r in current["declaration"]["outputs"]}
        ):
            raise ValueError("managed output inventory mismatch")
        for output in current["declaration"]["outputs"]:
            actual = outputs.get(output["name"])
            if (
                output["required"]
                and actual is None
                or actual is not None
                and (
                    actual not in artifacts
                    or not Path(actual["path"]).is_relative_to(self.context["publication_root"])
                    or actual["size"] > output["max_bytes"]
                    or not Path(actual["path"]).name.endswith("-" + output["filename"])
                )
            ):
                raise ValueError("required managed output unavailable")
        if current.get("cleanup") is not None:
            return current["cleanup"]
        successful = entry["status"] == "PASS"
        counts = entry.get("counts", {})
        accounting = (
            entry["kind"] != "test"
            or counts.get("passed", 0) > 0
            and not any(
                counts.get(key, 0) for key in ("failed", "error", "skipped", "todo", "flaky")
            )
        )
        current["completion"] = {
            "outcome": "passed" if successful else "failed",
            "settled": self.core["verification_contracts"].producer_settled(completion)
            and not completion.get("errors")
            and not completion.get("unresolved"),
            "accounting_complete": accounting,
            "source_current": bool(entry.get("fp_before"))
            and entry["fp_before"] == entry.get("fp_after"),
            "classification_current": True,
        }
        current["attestation"] = {"binding": binding, "artifacts": artifacts}
        current["publication_reference"] = reference
        current["cleanup"] = self._manager().finalize(
            allocation["allocation_id"],
            publication_reference=reference,
            completion_reference=reference,
        )
        return current["cleanup"]

    def _acknowledge_recipe(self, handle, reference, completion, reconciliation):
        current = self.records[handle]
        if reconciliation is None:
            raise ValueError("recipe acknowledgement requires complete reconciliation")
        if (
            current.get("publication_reference") is not None
            and current["publication_reference"] != reference
        ):
            raise ValueError("conflicting managed publication")
        if (
            current.get("reconciliation_reference") is not None
            and current["reconciliation_reference"] != reconciliation
        ):
            raise ValueError("conflicting recipe reconciliation")
        if current.get("kernel_completion") != completion:
            raise ValueError("recipe completion does not match actual owned handle")
        compiler = self.core["verification_harness"]
        entry = self.validate_entry(current["entry"], current["declaration"], final=True)
        if (
            entry.get("storage_allocation") != current["allocation"]
            or entry.get("recipe_assignment") != current["recipe_input"]
        ):
            raise ValueError("recipe committed allocation or assignment changed")
        result, publication = compiler.validate_published_child(
            entry,
            reference,
            root=self.context["publication_root"],
            completion=completion,
            launch_token=handle,
        )
        parent_binding = current["recipe_input"]["parent"]
        parent = self.validate_entry(parent_binding, None, final=True)
        if parent.get("recipe_execution", {}).get("aggregate") != reconciliation:
            raise ValueError("recipe reconciliation is not the committed parent result")
        if (
            self.context.get("session")
            and parent["recipe_execution"]["session"] != self.context["session"]
        ):
            raise ValueError("recipe reconciliation belongs to another session")
        compiler.current_parent_recipe(parent, self.context["repository"])
        entries = {parent["id"]: parent}
        completions = {}
        for actual_handle, actual in self.records.items():
            if actual.get("recipe_input", {}).get("parent") != parent_binding:
                continue
            observed = self.validate_entry(actual["entry"], actual["declaration"], final=True)
            entries[observed["id"]] = observed
            if actual.get("kernel_completion") is None:
                raise ValueError("recipe sibling ownership is unresolved")
            completions[observed["id"]] = {
                "completion": actual["kernel_completion"],
                "launch_token": actual_handle,
            }
        if parent["fp_before"] != parent["fp_after"] or parent["fp_after"] != entry["fp_after"]:
            raise ValueError("recipe source changed before acknowledgement")
        closure = compiler.reconcile_closure(
            reconciliation,
            root=self.context["publication_root"],
            entries=entries,
            completions=completions,
            current_source=parent["fp_after"],
        )
        if current.get("cleanup") is not None:
            return current["cleanup"]
        group = current["recipe_input"]["group_id"]
        eligible = (
            closure["collection_eligible"]
            if group == "collection"
            else closure["result"]["eligibility"].get(group) is True
        )
        current["completion"] = {
            "outcome": "passed" if result["selected_success"] else "failed",
            "settled": self.core["verification_contracts"].producer_settled(completion),
            "accounting_complete": eligible,
            "source_current": True,
            "classification_current": True,
        }
        artifacts = publication["artifacts"] + closure["artifacts"]
        current["attestation"] = {"binding": publication["binding"], "artifacts": artifacts}
        current["publication_reference"] = reference
        current["reconciliation_reference"] = reconciliation
        current["cleanup"] = self._manager().finalize(
            current["allocation"]["allocation_id"],
            publication_reference=reference,
            completion_reference=reference,
        )
        return current["cleanup"]
