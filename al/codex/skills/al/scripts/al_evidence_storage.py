"""Managed scratch is optional historical metadata, never arbitrary-path cleanup."""

import os
import shutil
import stat
import tempfile
from pathlib import Path

# Unix-domain socket paths are limited (104 bytes on macOS, 108 on Linux). Tools such as
# Python multiprocessing bind sockets under TMPDIR, so a check's TMPDIR must stay short:
# a private per-run directory directly under the system root, not inside the allocation.
SHORT_TEMPORARY_ROOTS = ("/tmp", "/var/tmp")


def short_temporary(roots=SHORT_TEMPORARY_ROOTS):
    """A fresh private (0700, owned) short directory for one check's TMPDIR."""
    for root in roots if os.name == "posix" else ():
        try:
            if not os.path.isdir(root):
                continue
            path = Path(tempfile.mkdtemp(prefix="al-", dir=root))
        except OSError:
            continue
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            remove_short_temporary(path)
            raise ValueError("short check temporary directory is not private")
        return path
    return None


def remove_short_temporary(path):
    """Remove only the directory this run created; rmtree never follows symlinks."""
    if path is not None and Path(path).name.startswith("al-"):
        shutil.rmtree(path, ignore_errors=True)


def modules():
    from installer import storage_declarations, storage_filesystem, storage_lifecycle

    return storage_declarations, storage_filesystem, storage_lifecycle


def normalize_declaration(value):
    return None if value is None else modules()[0].normalize(value)


def read_declaration(path, argv):
    grammar, fs, _ = modules()
    value = grammar.normalize(fs.read_json(Path(path).absolute()))
    if value is None:
        raise ValueError("explicit managed declaration required")
    grammar.render_argv(argv, value, Path("/__al_storage_operand_validation__"))
    return value


def validate_entry_storage(entry):
    value = normalize_declaration(entry.get("storage"))
    if value is None:
        if any(
            k in entry for k in ("storage_allocation", "storage_outputs", "storage_publication")
        ):
            raise ValueError("managed receipts require a storage declaration")
        return
    modules()[0].render_argv(entry["argv"], value, Path("/__al_storage_operand_validation__"))
    allocation = entry.get("storage_allocation")
    if allocation is not None:
        if (
            not isinstance(allocation, dict)
            or allocation.get("schema_version") != 1
            or allocation.get("declaration_digest") != modules()[0].declaration_digest(value)
            or allocation.get("owner", {}).get("run") != entry.get("run")
            or allocation.get("owner", {}).get("entry") != entry.get("id")
            or allocation.get("owner", {}).get("reservation") != entry.get("reservation")
        ):
            raise ValueError("foreign managed storage allocation")
    if not isinstance(entry.get("storage_outputs", {}), dict):
        raise ValueError("invalid managed storage outputs")


def validate_outputs(entry):
    value = normalize_declaration(entry.get("storage"))
    if value is None:
        return
    _, fs, _ = modules()
    outputs = entry.get("storage_outputs", {})
    for requirement in value["outputs"]:
        actual = outputs.get(requirement["name"])
        if actual is None:
            if requirement["required"]:
                raise ValueError("required managed output is unavailable")
        elif fs.artifact(actual["path"], maximum=requirement["max_bytes"]) != actual:
            raise ValueError("managed output changed after publication")


class PreparedStorage:
    def __init__(self, sid, entry):
        import al_ledger as ledger

        self.grammar, self.fs, self.core = modules()
        self.entry = entry
        self.declaration = self.grammar.normalize(entry["storage"])
        self.directory = (
            ledger.log_dir(sid) / "storage" / (entry["id"] + "-" + entry["reservation"])
        )
        self.directory.mkdir(parents=True, mode=0o700)
        self.remote = bool(os.environ.get("HARNESS_CHECK_CONTEXT"))
        self.manager = self.owner = self.proc = self.cleanup = None
        self.allocation = self.temporary = self.body = self.reference = None
        self.short_temporary = None
        self.recipe_input = entry.get("recipe_assignment")
        self.recipe_result = None
        self.recipe_eligible = False
        self.closure_verifier = None
        self.closure_artifacts = []
        self.reconciliation_reference = None
        self.publication_root = ledger.log_dir(sid) / "storage"

    def launch(self, argv, *, cwd, output, recipe_input=None):
        if recipe_input != self.recipe_input:
            raise ValueError("recipe input differs from protected evidence entry")
        try:
            if self.remote:
                from al_harness_process import OwnedCheck

                request = {
                    "schema_version": 1,
                    "declaration": self.declaration,
                    "entry": {k: self.entry[k] for k in ("run", "id", "reservation")},
                }
                if recipe_input is not None:
                    request["schema_version"] = 2
                    request["recipe_input"] = recipe_input
                self.proc = OwnedCheck(
                    argv, {"cwd": cwd, "stdout": output, "stderr": -2, "managed_storage": request,
                           "process_observation": self.entry.get("process_observation")}
                )
                self.allocation = self.proc.storage["allocation"]
                self.temporary = Path(self.proc.storage["operation_directory"]) / "tmp"
            else:
                from installer.owned_process import OwnedProcess

                def load(record, reference):
                    value = self.fs.read_reference(reference, root=self.directory)
                    if value["binding"] != self.binding(record) or value[
                        "entry"
                    ] != self.grammar.evidence_binding(self.entry):
                        raise ValueError("foreign managed evidence publication")
                    return value

                base = (
                    Path(os.environ["HARNESS_HOME"])
                    if os.environ.get("HARNESS_HOME")
                    else Path.home() / ".local/share/harness"
                )
                self.manager = self.core.StorageManager(
                    base.absolute() / "managed-storage",
                    verify_owner=lambda record, reference: self.completion_facts(
                        load(record, reference)["entry"]
                    ),
                    verify_publication=lambda record, reference: {
                        "binding": load(record, reference)["binding"],
                        "artifacts": load(record, reference)["artifacts"] + self.closure_artifacts,
                    },
                )
                owner = {
                    "adapter_kind": "evidence",
                    "identity": str(self.directory),
                    "reservation": self.entry["reservation"],
                    "run": self.entry["run"],
                    "entry": self.entry["id"],
                }
                self.allocation = self.manager.allocate(
                    owner=owner,
                    allocation_class=self.grammar.allocation_class(self.declaration),
                    declaration_digest=self.grammar.declaration_digest(self.declaration),
                )
                self.temporary = Path(self.allocation["path"]) / "tmp"
                self.temporary.mkdir(mode=0o700)
                rendered = self.grammar.render_argv(
                    argv, self.declaration, self.temporary, create=True
                )
                # TMPDIR is a short private directory (AF_UNIX path limit); declared
                # scratch/outputs and the uv cache stay inside the managed allocation.
                self.short_temporary = short_temporary()
                process_temporary = str(self.short_temporary or self.temporary)
                environment = dict(
                    os.environ,
                    TMPDIR=process_temporary,
                    TMP=process_temporary,
                    TEMP=process_temporary,
                    UV_CACHE_DIR=str(self.temporary / "uv-cache"),
                )
                self.owner = OwnedProcess(self.directory / "process", self.entry["reservation"])
                if recipe_input is not None:
                    import sys

                    from installer import verification_harness

                    assignment, _ = verification_harness.read_assignment(
                        recipe_input["assignment"], root=self.publication_root
                    )
                    if (
                        assignment["assignment_digest"] != recipe_input["assignment_digest"]
                        or assignment["parent"] != recipe_input["parent"]
                        or assignment["source_digest"] != self.entry["fp_before"]
                        or list(argv) != verification_harness.logical_argv(assignment["kind"])
                    ):
                        raise ValueError("recipe input binding changed before launch")
                    rendered = verification_harness.materialize_child(
                        self.temporary,
                        recipe_input["assignment"],
                        trusted_root=self.publication_root,
                        launch_token=self.owner.token,
                        interpreter=sys.executable,
                    )
                self.proc = self.owner.spawn(rendered, cwd=cwd, env=environment, output=output)
                self.manager.mark_running(
                    self.allocation["allocation_id"],
                    generation=self.allocation["generation"],
                    owner_receipt=self.owner.record(),
                )
            if self.remote:
                self.entry["storage_pressure"] = self.proc.storage.get("pressure")
            else:
                from installer.storage_inventory import pressure_summary

                self.entry["storage_pressure"] = pressure_summary(
                    self.manager.last_pressure, self.manager.policy
                )
            if (self.entry.get("storage_pressure") or {}).get("warning") is True:
                print(
                    "AL · storage · soft pressure warning; "
                    "inspect harness storage for retained allocations",
                    flush=True,
                )
            self.entry["storage_allocation"] = self.allocation
            self.entry["storage_operands"] = {
                str(row["index"]): str(self.temporary / "scratch" / row["name"] / row["suffix"])
                if row["kind"] == "scratch"
                else str(
                    self.temporary
                    / "outputs"
                    / next(
                        r["filename"]
                        for r in self.declaration["outputs"]
                        if r["name"] == row["name"]
                    )
                )
                for row in self.declaration["operands"]
            }
            return self.proc

        except BaseException:
            if self.proc is None and self.owner is not None:
                self.proc = self.owner.process
            if self.proc is not None:
                try:
                    self.stop()
                except BaseException:
                    pass
            if self.proc is None:
                self.release_short_temporary()
            self.retain("launch_unverified")
            raise

    def complete(self):
        if self.cleanup is None:
            try:
                if self.remote:
                    self.cleanup = self.proc.completion()
                else:
                    proof = self.owner.cleanup()
                    self.cleanup = {
                        "exit": self.proc.poll(),
                        "complete": proof["complete"],
                        "group_absent": proof["complete"],
                        "errors": proof["errors"],
                        "unresolved": proof["unresolved"],
                        "secondary_signals": proof["secondary_signals"],
                        **{
                            key: proof.get(key)
                            for key in (
                                "leader_reaped",
                                "owned_group_settled",
                                "descendant_scope",
                                "descendants_settled",
                                "settlement_basis",
                                "settlement_evidence",
                            )
                        },
                    }
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                self.cleanup = {
                    "exit": getattr(self.proc, "returncode", None),
                    "complete": False,
                    "group_absent": False,
                    "errors": ["owned_completion_unavailable"],
                    "unresolved": True,
                }
            if self.cleanup.get("complete") and not self.cleanup.get("unresolved"):
                self.release_short_temporary()
        return self.cleanup

    def release_short_temporary(self):
        """Delete the per-run TMPDIR once its process group is settled; an unsettled
        group keeps it (it stays private and the system temp cleaner reclaims it)."""
        remove_short_temporary(self.short_temporary)
        self.short_temporary = None

    def stop(self):
        if self.remote:
            self.proc.cancel()
        return self.complete()

    def binding(self, record=None):
        value = record or self.allocation
        return {
            "owner": value["owner"],
            "allocation_id": value["allocation_id"],
            "declaration_digest": value["declaration_digest"],
        }

    def completion_facts(self, entry):
        from installer import verification_contracts

        counts = entry.get("counts", {})
        known = (
            entry["kind"] != "test"
            or counts.get("passed", 0) > 0
            and not any(counts.get(k, 0) for k in ("skipped", "failed", "error", "todo", "flaky"))
        )
        if self.recipe_input is not None:
            known = self.recipe_eligible is True
        complete = self.cleanup or {}
        return {
            "outcome": "passed" if entry["status"] == "PASS" else "failed",
            "settled": verification_contracts.producer_settled(complete)
            and not complete.get("errors")
            and not complete.get("unresolved"),
            "accounting_complete": known,
            "source_current": bool(entry.get("fp_before"))
            and entry["fp_before"] == entry.get("fp_after"),
            "classification_current": True,
        }

    def before_commit(self, validate=None):
        if (
            not self.cleanup
            or not self.cleanup.get("complete")
            or self.cleanup.get("unresolved")
            or self.cleanup.get("errors")
        ):
            raise ValueError("managed check settlement is incomplete")
        from installer import verification_contracts

        self.entry["storage_settlement"] = verification_contracts.settlement_observation(
            self.cleanup
        )
        self.entry["storage_capture_state"] = "protected_capture"
        if self.manager is not None and not verification_contracts.producer_settled(self.cleanup):
            self.manager.retain(
                self.allocation["allocation_id"], reason="descendant_settlement_unproven"
            )
        outputs = {}
        for requirement in self.declaration["outputs"]:
            path = self.temporary / "outputs" / requirement["filename"]
            if not path.exists() and not path.is_symlink() and not requirement["required"]:
                continue
            outputs[requirement["name"]] = self.fs.capture_output(
                path,
                self.directory / "outputs",
                requirement["filename"],
                maximum=requirement["max_bytes"],
            )
        self.entry["storage_outputs"] = outputs
        extra = []
        if self.recipe_input is not None:
            from installer import verification_harness

            token = self.proc.handle if self.remote else self.owner.token
            self.entry["recipe_process"] = verification_harness.process_binding(token, self.cleanup)
            if validate is None:
                raise ValueError("recipe child needs structured report validation")
            validate(self.entry, outputs, self)
            self.entry["recipe_log_copy"] = self.fs.capture_output(
                self.entry["log"], self.directory / "outputs", "runner.log", maximum=16 * 1024**2
            )
            extra = [
                self.entry["recipe_log_copy"],
                *verification_harness.document_artifacts(
                    self.entry["recipe_result"]["validation"], root=self.publication_root
                ),
            ]
            if self.owner is not None:
                extra.append(self.fs.artifact(self.owner.receipt))
        self.body = {
            "schema_version": 3,
            "producer": self.entry["storage_settlement"],
            "capture_state": "protected_capture",
            "binding": self.binding(),
            "entry": self.grammar.evidence_binding(self.entry),
            "artifacts": [self.fs.artifact(self.entry["log"]), *outputs.values(), *extra],
            "outputs": outputs,
        }
        self.reference = {
            "schema_version": 1,
            "reference": str(self.directory / "publication.json"),
            "sha256": self.fs.digest(self.body),
        }
        self.entry["storage_publication"] = self.reference

    def publish(self, *, committed):
        if not committed or self.reference is None:
            self.retain("ledger_publication_unavailable")
            raise ValueError("managed publication has no committed reservation")
        reference = self.fs.publish_json(self.reference["reference"], self.body)
        if reference != self.reference:
            raise ValueError("managed publication identity changed")
        return reference

    def acknowledge(self, *, reconciliation=None):
        if self.reference is None:
            raise ValueError("managed publication is unavailable")
        if self.recipe_input is not None and reconciliation is None:
            self.retain("recipe_reconciliation_missing")
            return {"state": "RETAINED", "reason": "recipe_reconciliation_missing"}
        if (
            self.reconciliation_reference is not None
            and self.reconciliation_reference != reconciliation
        ):
            raise ValueError("conflicting recipe reconciliation")
        if self.remote:
            result = self.proc.acknowledge_publication(
                self.reference, reconciliation=reconciliation
            )
        else:
            if self.recipe_input is not None:
                if self.closure_verifier is None:
                    raise ValueError("trusted recipe closure verifier unavailable")
                closure = self.closure_verifier(reconciliation)
                group = self.recipe_input["group_id"]
                self.recipe_eligible = (
                    closure["collection_eligible"]
                    if group == "collection"
                    else closure["result"]["eligibility"].get(group) is True
                )
                self.closure_artifacts = closure["artifacts"]
            result = self.manager.finalize(
                self.allocation["allocation_id"],
                publication_reference=self.reference,
                completion_reference=self.reference,
            )
        self.reconciliation_reference = reconciliation
        self.fs.publish_json(
            self.directory / "cleanup.json",
            {
                "schema_version": 1,
                "publication": self.reference,
                "reconciliation": reconciliation,
                "cleanup": result,
            },
        )
        return result

    def finalize(self, *, committed):
        if not committed or self.reference is None:
            self.retain("ledger_publication_unavailable")
            return {"state": "RETAINED", "reason": "ledger_publication_unavailable"}
        self.publish(committed=committed)
        return self.acknowledge()

    def retain(self, reason):
        if self.manager is not None and self.allocation is not None:
            self.manager.retain(self.allocation["allocation_id"], reason=reason)


def default_declaration():
    return modules()[0].default_declaration()
