"""Immutable execution-owned draft/generated references, separate from AL authority."""

import fcntl
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from pathlib import Path

from harness.execution_contract import canonical, digest
from harness.execution_handoff import durable_file


def presentation_page(text):
    """Return a raw-prefix cursor and redacted text that survives outer presentation caps."""
    from harness.execution_logs import MAX_TEXT, _redact

    size = min(len(text), MAX_TEXT)
    while size:
        value = _redact(text[:size])
        if len(value) <= MAX_TEXT:
            return size, value
        size //= 2
    return 0, ""


def artifact_limit(record):
    """Only a canonical, guarded gate report receives the dedicated capture bound."""
    if record.get("source") != "guarded-al-gate-manifest":
        return 16 * 1024 * 1024
    from harness.execution_al_reports import REPORT_LIMIT

    provenance = record.get("provenance")
    reference = provenance.get("canonical_reference") if isinstance(provenance, dict) else None
    if (
        record.get("name") != "gate-manifest.json"
        or not isinstance(reference, dict)
        or not isinstance(provenance.get("operation_id"), str)
        or not re.fullmatch(r"al-report-[0-9a-f]{32}", provenance["operation_id"])
        or reference.get("kind") != "gate-manifest"
        or not isinstance(reference.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])
        or reference.get("id") != reference["sha256"]
        or record.get("sha256") != "sha256:" + reference["sha256"]
    ):
        raise ValueError("invalid guarded gate manifest provenance")
    return REPORT_LIMIT


class ArtifactStore:
    def __init__(self, directory, *, create=True):
        self.directory = Path(directory).resolve()
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif not self.directory.is_dir():
            raise ValueError("published artifact store unavailable")

    @contextmanager
    def locked(self, *, read_only=False):
        with (self.directory / "artifacts.lock").open("r" if read_only else "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH if read_only else fcntl.LOCK_EX)
            path = self.directory / "index.json"
            value = (
                json.loads(path.read_bytes()) if path.is_file() else {"names": {}, "records": {}}
            )
            yield value
            if read_only:
                return
            fd, name = tempfile.mkstemp(prefix=".index-", dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(canonical(value).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
                directory = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(name).unlink(missing_ok=True)

    def write_artifact(
        self,
        principal,
        name,
        content,
        expected_graph_revision,
        current_graph_revision,
        expected_sha256=None,
    ):
        if principal["role"] != "orchestrator":
            raise ValueError("only supervised orchestrator may author draft artifacts")
        if (
            type(expected_graph_revision) is not int
            or expected_graph_revision != current_graph_revision
        ):
            raise ValueError("draft graph revision is stale")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.(?:json|md)", name
        ):
            raise ValueError("simple JSON/Markdown draft basename required")
        if not isinstance(content, str) or len(content.encode()) > 65536:
            raise ValueError("authored draft exceeds 64KiB")
        if name.endswith(".json"):
            json.loads(content)
        with self.locked() as index:
            prior = index["names"].get(name)
            if prior and index["records"][prior]["sha256"] != expected_sha256:
                raise ValueError("draft replacement requires current prior content hash")
            if not prior and expected_sha256 is not None:
                raise ValueError("draft prior hash has no current named version")
            record = self._publish(index, name, content.encode(), "authored")
            index["names"][name] = record["artifact_id"]
            return {k: record[k] for k in ("artifact_id", "sha256", "size")}

    def capture_generated(self, name, raw):
        if not isinstance(raw, bytes) or len(raw) > 16 * 1024 * 1024:
            raise ValueError("generated AL artifact exceeds capture bound")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}\.(?:json|md)", name):
            raise ValueError("invalid generated artifact name")
        with self.locked() as index:
            record = self._publish(index, name, raw, "guarded-al-generated")
            return {k: record[k] for k in ("artifact_id", "sha256", "size")}

    def capture_gate_manifest(self, raw, provenance):
        from harness.execution_al_reports import REPORT_LIMIT

        reference = provenance.get("canonical_reference", {})
        if (
            not isinstance(raw, bytes)
            or len(raw) > REPORT_LIMIT
            or reference.get("kind") != "gate-manifest"
            or digest(raw) != "sha256:" + reference.get("sha256", "")
        ):
            raise ValueError("invalid validated gate manifest capture")
        with self.locked() as index:
            prior = next(
                (
                    r
                    for r in index["records"].values()
                    if r.get("provenance", {}).get("operation_id") == provenance["operation_id"]
                ),
                None,
            )
            if prior:
                if prior["sha256"] != digest(raw) or prior["provenance"] != provenance:
                    raise ValueError("gate report operation publication differs")
                record = prior
            else:
                record = self._publish(index, "gate-manifest.json", raw, "guarded-al-gate-manifest")
                record["provenance"] = provenance
            result = {k: record[k] for k in ("artifact_id", "sha256", "size")}
            return result | {
                "resource": "artifact:" + result["artifact_id"] + ":" + result["sha256"]
            }

    def submit_output(
        self,
        principal,
        name,
        content,
        expected_graph_revision,
        current_graph_revision,
        contract=None,
    ):
        from harness.execution_contract import identity
        from harness.native_tool_policy import ROLES

        owner = identity(principal["thread_id"])
        if principal["role"] not in {*ROLES, "orchestrator"}:
            raise ValueError("registered native role required for report output")
        if (
            type(expected_graph_revision) is not int
            or expected_graph_revision != current_graph_revision
        ):
            raise ValueError("report output graph revision is stale")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.(json|md|txt)", name
        ):
            raise ValueError("simple named report output required")
        if not isinstance(content, str) or not content or len(content.encode()) > 262144:
            raise ValueError("report output must be nonempty and at most 256 KiB")
        if name.endswith(".json"):
            json.loads(content)
        if contract is not None:
            from harness.agent_contracts import ContractError, fingerprint, validate

            validate(contract)
            if contract["kind"] != "implementation-result" or contract["payload"][
                "record_digest"
            ] != fingerprint(content):
                raise ContractError("/payload", kind="implementation-result")
        with self.locked() as index:
            record = self._publish(index, name, content.encode(), "native-agent-output")
            if contract is not None:
                record["contract"] = contract
            record.update(
                owner=owner, role=principal["role"], graph_revision=current_graph_revision
            )
            index.setdefault("outputs", {}).setdefault(owner, {})[name] = record["artifact_id"]
            return {
                k: record[k] for k in ("artifact_id", "sha256", "size", "name", "owner", "role")
            }

    def outputs(self):
        with self.locked() as index:
            return {
                owner: {
                    name: {
                        k: index["records"][ref][k]
                        for k in ("artifact_id", "sha256", "size", "role", "contract")
                        if k in index["records"][ref]
                    }
                    for name, ref in names.items()
                }
                for owner, names in index.get("outputs", {}).items()
            }

    def _publish(self, index, name, raw, source):
        artifact_id = "artifact-" + secrets.token_hex(16)
        path = self.directory / (artifact_id + "." + name.rsplit(".", 1)[1])
        durable_file(path, raw)
        record = {
            "artifact_id": artifact_id,
            "sha256": digest(raw),
            "size": len(raw),
            "path": str(path),
            "source": source,
            "name": name,
        }
        index["records"][artifact_id] = record
        return record

    def buffer(self, artifact_id, expected_sha256):
        """Authenticate a bounded ordinary file and return exactly the checked immutable buffer."""
        import stat

        from harness.policy.context import check_deadline

        if not re.fullmatch(r"artifact-[0-9a-f]{32}", artifact_id):
            raise ValueError("invalid execution artifact identity")
        with self.locked(read_only=True) as index:
            record = index["records"].get(artifact_id)
            if not record or record["sha256"] != expected_sha256:
                raise ValueError("execution artifact hash/identity differs")
            maximum = artifact_limit(record)
            path = Path(record["path"])
            if path.parent != self.directory:
                raise ValueError("execution artifact path/link identity differs")
            check_deadline()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or not 0 <= before.st_size <= maximum
                ):
                    raise ValueError("bounded ordinary execution artifact required")
                chunks, size = [], 0
                while size <= maximum:
                    check_deadline()
                    chunk = os.read(fd, min(65536, maximum + 1 - size))
                    check_deadline()
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                after, named = os.fstat(fd), path.lstat()
            finally:
                os.close(fd)

            def version(info):
                return (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_nlink,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )

            raw = b"".join(chunks)
            if (
                len(raw) > maximum
                or version(before) != version(after)
                or version(after) != version(named)
                or digest(raw) != record["sha256"]
                or len(raw) != record["size"]
            ):
                raise ValueError("execution artifact bytes changed")
            check_deadline()
            return raw

    def resolve(self, artifact_id, expected_sha256):
        self.buffer(artifact_id, expected_sha256)
        with self.locked(read_only=True) as index:
            return Path(index["records"][artifact_id]["path"])

    def read(self, artifact_id, expected_sha256, *, offset=0, limit=16384):
        if (
            type(offset) is not int
            or type(limit) is not int
            or offset < 0
            or not 1 <= limit <= 65536
        ):
            raise ValueError("bounded artifact cursor required")
        checked = self.buffer(artifact_id, expected_sha256)
        size = len(checked)
        if offset > size:
            raise ValueError("artifact offset exceeds content")
        raw = checked[offset : offset + min(limit, 16384)]
        if raw and raw[0] & 0xC0 == 0x80:
            raise ValueError("artifact offset splits a UTF-8 character")
        while raw:
            try:
                text = raw.decode("utf-8")
                break
            except UnicodeDecodeError as error:
                if error.end == len(raw) and error.reason == "unexpected end of data":
                    raw = raw[: error.start]
                else:
                    raise ValueError("artifact content is not valid UTF-8") from None
        else:
            if offset < size:
                raise ValueError("artifact page limit is too small for UTF-8")
            text = ""
        consumed, rendered = presentation_page(text)
        advanced = len(text[:consumed].encode())
        return {
            "text": rendered,
            "offset": offset,
            "next_offset": offset + advanced,
            "truncated": offset + advanced < size,
            "artifact_id": artifact_id,
            "sha256": expected_sha256,
            "presentation_redacted": rendered != text[:consumed],
        }


def output_reports(directory):
    """Pure metadata view; status never rewrites indexes or exposes report reasoning."""
    directory = Path(directory)
    path = directory / "index.json"
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("report index symlink refused")
    before = path.read_bytes()
    index = json.loads(before)
    rows = []
    for owner, names in index.get("outputs", {}).items():
        for name, identifier in names.items():
            row = index["records"][identifier]
            content = Path(row["path"])
            if (
                content.parent != directory
                or content.is_symlink()
                or digest(content.read_bytes()) != row["sha256"]
            ):
                raise ValueError("named report provenance changed")
            rows.append(
                {
                    "owner": owner,
                    "name": name,
                    **{k: row[k] for k in ["artifact_id", "sha256", "size", "role"]},
                    "resource": "artifact:" + row["artifact_id"] + ":" + row["sha256"],
                }
            )
    if path.read_bytes() != before:
        raise ValueError("report index changed during status observation")
    return sorted(rows, key=lambda r: (r["owner"], r["name"]))


def recorded_output_directory(conn, execution_id, epoch_id):
    """Server-selected historical store; the recorded epoch is context, not write authority."""
    from harness.execution_epochs import get_epoch

    epoch = get_epoch(conn, execution_id, epoch_id)
    return Path(epoch["identity"]["handoff_path"]).parent / "operations" / "drafts"
