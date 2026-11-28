"""Owned disposable storage; evidence publication and cleanup are separate facts."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
import time
from pathlib import Path

from .storage_filesystem import (
    StorageError,
    _delete_contents,
    _hash,
    _identifier,
    _open_directory,
    _private,
    _publish_at,
    _read_at,
    _write_at,
    canonical,
    coordinate,
    identity,
    unchanged,
)
from .storage_filesystem import (
    limits as _limits,
)

VERSION = 1
RECORD_VERSION = 2
CLASSES = {"verification-fixtures", "operation-scratch", "build-cache"}
STATES = {"ALLOCATED", "RUNNING", "RETAINED", "ELIGIBLE", "QUARANTINED", "REMOVED"}
SUBDIRECTORIES = ("records", "allocations", "quarantine", "receipts")
DEFAULT_POLICY = {
    "warning_bytes": 10 * 1024**3,
    "minimum_free_bytes": 5 * 1024**3,
    # Scratch that never became eligible (failed runs, unproven descendant
    # settlement on macOS, abandoned allocations) expires after this long
    # without a record change. 0 disables expiry.
    "retention_seconds": 3 * 24 * 3600,
    # A passed, owner-finalized allocation retained only because descendant
    # settlement was unproven at completion becomes reclaimable once a later
    # process observation proves it settled and its record is this old.
    "settlement_grace_seconds": 15 * 60,
    # Finished scratch (verification fixtures, operation scratch) whose owner did not report a
    # pass - failed, unknown or never finalized - is kept for diagnosis this long, not the full
    # retention window: it is most of what accumulates (#164). 0 falls back to retention.
    "failed_retention_seconds": 24 * 3600,
    # Disk pressure: below this many free bytes (or past the soft warning), every expirable
    # allocation older than pressure_retention_seconds is reclaimed, oldest first, still never
    # one whose owner or any process using it is alive (#164).
    "pressure_free_bytes": 15 * 1024**3,
    "pressure_retention_seconds": 3600,
}
EXPIRED = "retention_expired"
SETTLED_LATER = "settlement_proven_later"
LATE_REASONS = {EXPIRED, SETTLED_LATER}
UNPROVEN = "descendant_settlement_unproven"
EXPIRABLE_STATES = {"ALLOCATED", "RUNNING", "RETAINED"}
OPPORTUNISTIC_SECONDS = 2.0
PRESSURE_OPPORTUNISTIC_SECONDS = 10.0
SCRATCH_CLASSES = {"verification-fixtures", "operation-scratch"}


def configured_policy(policy=None):
    result = dict(DEFAULT_POLICY)
    for key, name in (
        ("warning_bytes", "HARNESS_STORAGE_WARNING_BYTES"),
        ("minimum_free_bytes", "HARNESS_STORAGE_MINIMUM_FREE_BYTES"),
        ("retention_seconds", "HARNESS_STORAGE_RETENTION_SECONDS"),
        ("settlement_grace_seconds", "HARNESS_STORAGE_SETTLEMENT_GRACE_SECONDS"),
        ("failed_retention_seconds", "HARNESS_STORAGE_FAILED_RETENTION_SECONDS"),
        ("pressure_free_bytes", "HARNESS_STORAGE_PRESSURE_FREE_BYTES"),
        ("pressure_retention_seconds", "HARNESS_STORAGE_PRESSURE_RETENTION_SECONDS"),
    ):
        value = os.environ.get(name)
        if value is not None:
            if not value.isascii() or not value.isdecimal() or len(value) > 19:
                raise StorageError("invalid_storage_policy_configuration")
            result[key] = int(value)
    if policy is not None:
        if not isinstance(policy, dict) or set(policy) - set(DEFAULT_POLICY):
            raise StorageError("invalid_policy")
        result.update(policy)
    if any(type(v) is not int or not 0 <= v <= 2**63 - 1 for v in result.values()):
        raise StorageError("invalid_policy")
    return result


RECORD_KEYS = {
    "schema_version",
    "allocation_id",
    "generation",
    "state",
    "class",
    "declaration_digest",
    "owner",
    "identity",
    "consumers",
    "owner_reference",
    "publication_reference",
    "completion_reference",
    "retention_reason",
    "transaction",
    "attestation",
    "receipt",
    "terminal_outcome",
}


OWNER_FIELDS = {
    "adapter_kind",
    "identity",
    "reservation",
    "execution",
    "epoch",
    "authority_generation",
    "principal",
    "run",
    "entry",
}


def _valid_owner(owner):
    if not (
        isinstance(owner, dict)
        and not set(owner) - OWNER_FIELDS
        and {"adapter_kind", "identity", "reservation"} <= set(owner)
        and all(
            isinstance(value, str) and 0 < len(value) <= 256
            for key, value in owner.items()
            if key != "identity"
        )
        and owner["adapter_kind"] in {"release", "preflight", "evidence", "harness"}
    ):
        return False
    value = owner["identity"]
    if not isinstance(value, str) or not value or "\0" in value:
        return False
    try:
        # A portable metadata budget; identity remains an exact opaque string.
        return len(value.encode("utf-8")) <= 4096
    except UnicodeEncodeError:
        return False


class _BSDInfo(ctypes.Structure):
    # sys/proc_info.h PROC_PIDTBSDINFO (struct proc_bsdinfo).
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "flags",
            "status",
            "xstatus",
            "pid",
            "ppid",
            "uid",
            "gid",
            "ruid",
            "rgid",
            "svuid",
            "svgid",
            "reserved",
        )
    ]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "pjobc", "tdev", "tpgid")]
    _fields_ += [
        ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64),
        ("start_micros", ctypes.c_uint64),
    ]


class _FDInfo(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_int32), ("type", ctypes.c_uint32)]


# struct vnode_info is 152 bytes; vnode_info_path appends a MAXPATHLEN path.
_VNODE_INFO = 152
_VNODE_PATH = _VNODE_INFO + 1024


def _text(raw):
    return raw.split(b"\0", 1)[0]


def _darwin_processes(note, observed, deadline):
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    count = library.proc_listallpids(None, 0)
    if count <= 0:
        raise StorageError("process_observation_unavailable")
    pids = (ctypes.c_int * (count + 512))()
    count = library.proc_listallpids(pids, ctypes.sizeof(pids))
    if count <= 0 or count >= len(pids):
        raise StorageError("process_observation_unavailable")
    uid = os.getuid()
    paths = ctypes.create_string_buffer(2 * _VNODE_PATH)
    descriptor = ctypes.create_string_buffer(24 + _VNODE_PATH)
    executable = ctypes.create_string_buffer(4096)
    for pid in pids[:count]:
        if time.monotonic() >= deadline:
            raise StorageError("process_observation_incomplete")
        if pid <= 0:
            continue
        info = _BSDInfo()
        ctypes.set_errno(0)
        if library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != (
            ctypes.sizeof(info)
        ):
            code = ctypes.get_errno()
            if code == errno.ESRCH:
                continue
            if code != errno.EPERM:
                raise StorageError("process_observation_unavailable")
            # EPERM: another user's process. It cannot be inspected (and cannot
            # open a private 0700 namespace), but it is still a live pid/group.
            observed["pids"].add(pid)
            with contextlib.suppress(OSError):
                observed["groups"].add(os.getpgid(pid))
                observed["sessions"].add(os.getsid(pid))
            continue
        observed["pids"].add(pid)
        observed["groups"].add(info.pgid)
        with contextlib.suppress(OSError):
            observed["sessions"].add(os.getsid(pid))
        if info.uid != uid or info.status == 5:  # foreign or zombie: no open files
            continue
        ctypes.set_errno(0)
        if library.proc_pidinfo(pid, 9, 0, paths, len(paths)) != len(paths):
            if ctypes.get_errno() == errno.ESRCH:
                continue
            raise StorageError("process_observation_unavailable")
        note(pid, _text(paths.raw[_VNODE_INFO:_VNODE_PATH]))  # cwd
        note(pid, _text(paths.raw[_VNODE_PATH + _VNODE_INFO :]))  # root
        if library.proc_pidpath(pid, executable, len(executable)) > 0:
            note(pid, _text(executable.raw))
        size = library.proc_pidinfo(pid, 1, 0, None, 0)
        if size <= 0:
            continue
        table = (_FDInfo * (size // ctypes.sizeof(_FDInfo) + 64))()
        ctypes.set_errno(0)
        size = library.proc_pidinfo(pid, 1, 0, table, ctypes.sizeof(table))
        if size <= 0:
            if ctypes.get_errno() == errno.ESRCH:
                continue
            raise StorageError("process_observation_unavailable")
        for entry in table[: size // ctypes.sizeof(_FDInfo)]:
            if entry.type != 1:  # PROX_FDTYPE_VNODE
                continue
            ctypes.set_errno(0)
            if library.proc_pidfdinfo(pid, entry.fd, 2, descriptor, len(descriptor)) == len(
                descriptor
            ):
                note(pid, _text(descriptor.raw[24 + _VNODE_INFO :]))
            elif ctypes.get_errno() == errno.EPERM:
                # The kernel withholds paths of data-vault/protected files; those
                # live in system-protected locations, never in user scratch.
                observed["protected_descriptors"] += 1
            elif ctypes.get_errno() not in {0, errno.ESRCH, errno.EBADF}:
                raise StorageError("process_observation_unavailable")


def _linux_processes(note, observed, deadline):
    uid = os.getuid()
    for name in os.listdir("/proc"):
        if time.monotonic() >= deadline:
            raise StorageError("process_observation_incomplete")
        if not name.isdigit():
            continue
        base = "/proc/" + name
        try:
            with open(base + "/stat", "rb") as stream:
                fields = stream.read().rsplit(b")", 1)[1].split()
            owner = os.stat(base).st_uid
        except (FileNotFoundError, ProcessLookupError):
            continue
        pid = int(name)
        observed["pids"].add(pid)
        observed["groups"].add(int(fields[2]))
        observed["sessions"].add(int(fields[3]))
        if owner != uid or fields[0] == b"Z":
            continue
        try:
            for link in ("cwd", "root", "exe"):
                with contextlib.suppress(FileNotFoundError):
                    note(pid, os.readlink(base + "/" + link))
            for fd in os.listdir(base + "/fd"):
                with contextlib.suppress(FileNotFoundError):
                    note(pid, os.readlink(base + "/fd/" + fd))
            with open(base + "/maps", "rb") as stream:
                for line in stream:
                    parts = line.split(None, 5)
                    if len(parts) == 6:
                        note(pid, parts[5].strip())
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            raise StorageError("process_observation_unavailable") from None


def observe_processes(directory, *, deadline):
    """One bounded scan of every visible process (an ``lsof +D`` equivalent).

    Returns the live pids, process groups and sessions, and for each direct
    child name of ``directory`` the pids whose cwd, root, executable, mapped or
    open file lies inside it. Raises StorageError when the observation is
    unsupported, unavailable or incomplete: settlement is then undecidable.
    """
    prefix = os.path.realpath(directory) + os.sep
    observed = {
        "pids": set(),
        "groups": set(),
        "sessions": set(),
        "users": {},
        "protected_descriptors": 0,
    }

    def note(pid, path):
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if path and path.startswith(prefix):
            name = path[len(prefix) :].split(os.sep, 1)[0]
            observed["users"].setdefault(name, set()).add(pid)

    if sys.platform == "darwin":
        _darwin_processes(note, observed, deadline)
    elif sys.platform.startswith("linux"):
        _linux_processes(note, observed, deadline)
    else:
        raise StorageError("process_observation_unsupported")
    if os.getpid() not in observed["pids"]:
        raise StorageError("process_observation_unavailable")
    return observed


def default_root(home=None):
    """HARNESS_HOME/managed-storage, shared by Harness and AL."""
    if os.environ.get("HARNESS_HOME"):
        base = Path(os.environ["HARNESS_HOME"])
    else:
        base = Path(home or Path.home()) / ".local/share/harness"
    return base.absolute() / "managed-storage"


def prune(manager, *, apply=False, seconds=60.0):
    """Explicit prune: dry run by default; applying repeats bounded passes until
    the namespace is complete, a pass makes no progress, or time runs out."""
    deadline = time.monotonic() + seconds
    total = {
        "schema_version": VERSION,
        "apply": bool(apply),
        "passes": 0,
        "results": [],
        "candidates": [],
        "retained_by_reason": {},
        "reclaimed": 0,
        "reclaimed_logical_bytes": 0,
        "reclaimed_allocated_bytes_estimate": 0,
        "records": 0,
        "complete": False,
    }
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        current = manager.reclaim(
            limits={"seconds": min(remaining, 30), "entries": 1000000, "records": 10000},
            apply=apply,
            measure=not apply,
            max_records=1000000,
        )
        total["passes"] += 1
        total["records"] = current["records"]
        total["results"] += current["results"]
        total["candidates"] += current["candidates"]
        total["retained_by_reason"] = current["retained_by_reason"]
        if "observation_error" in current:
            total["observation_error"] = current["observation_error"]
        total["reclaimed"] += sum(r.get("state") == "REMOVED" for r in current["results"])
        total["reclaimed_logical_bytes"] += current["reclaimed_logical_bytes"]
        allocated = current["reclaimed_allocated_bytes_estimate"]
        if total["reclaimed_allocated_bytes_estimate"] is not None:
            total["reclaimed_allocated_bytes_estimate"] = (
                None
                if allocated is None
                else total["reclaimed_allocated_bytes_estimate"] + allocated
            )
        total["complete"] = current["complete"]
        progressed = any(r.get("state") in {"REMOVED", "PARTIAL"} for r in current["results"])
        if not apply or current["complete"] or not progressed:
            break
    if not apply:
        measured = [c["measurement"] for c in total["candidates"] if c.get("measurement")]
        total["measured_candidates"] = len(measured)
        total["candidate_logical_bytes"] = sum(m["logical_bytes"] for m in measured)
        total["candidate_allocated_bytes_estimate"] = (
            None
            if any(m["allocated_bytes_estimate"] is None for m in measured)
            else sum(m["allocated_bytes_estimate"] for m in measured)
        )
    return total


def describe_prune(result):
    """Human-readable lines for a prune result."""
    lines = []
    if result["apply"]:
        lines.append(
            f"Reclaimed {result['reclaimed']} allocation(s): "
            f"{result['reclaimed_logical_bytes']} logical bytes; allocated estimate "
            f"{result['reclaimed_allocated_bytes_estimate']}"
        )
        for row in result["results"]:
            if row.get("state") not in {"REMOVED", "PARTIAL"}:
                lines.append(f"Not reclaimed: {row['allocation_id']} · {row.get('reason', '')}")
    else:
        rules = {}
        for row in result["candidates"]:
            rules[row["rule"]] = rules.get(row["rule"], 0) + 1
        lines.append(
            f"Would reclaim {len(result['candidates'])} allocation(s)"
            + "".join(f"; {count} {rule}" for rule, count in sorted(rules.items()))
        )
        lines.append(
            f"Measured {result['measured_candidates']} of them: "
            f"{result['candidate_logical_bytes']} logical bytes; allocated estimate "
            f"{result['candidate_allocated_bytes_estimate']}"
        )
    for reason, count in sorted(result["retained_by_reason"].items()):
        lines.append(f"Retained: {count} · {reason}")
    if result.get("observation_error"):
        lines.append("Process observation unavailable: " + result["observation_error"])
    if not result["complete"]:
        lines.append("Pass is partial; run prune again to continue.")
    if not result["apply"]:
        lines.append("Dry run: nothing was deleted. Pass --apply to reclaim.")
    return lines


def _owner_present(value, observed):
    """The recorded owner leader, or any process still in its group or session."""
    reference = value.get("owner_reference")
    pid = reference.get("pid") if isinstance(reference, dict) else None
    if type(pid) is not int or pid <= 0:
        return False
    return pid in observed["pids"] or pid in observed["groups"] or pid in observed["sessions"]


def _valid_identity(value):
    return (
        isinstance(value, dict)
        and set(value) == {"device", "inode", "uid", "type"}
        and all(type(v) is int and v >= 0 for v in value.values())
        and value["type"] == stat.S_IFDIR
    )


def _free_bytes(descriptor):
    try:
        observed = os.fstatvfs(descriptor)
        return observed.f_bavail * observed.f_frsize
    except OSError:
        return None


def _space_observation(before, after):
    return {
        "before_bytes": before,
        "after_bytes": after,
        "delta_bytes": after - before if before is not None and after is not None else None,
        "attribution": "filesystem-wide concurrent observation; not recovered bytes",
    }


def _valid_transaction(value):
    transaction = value["transaction"]
    if transaction is None:
        return value["state"] not in {"QUARANTINED", "REMOVED"}
    return (
        isinstance(transaction, dict)
        and set(transaction) == {"id", "phase", "identity"}
        and _identifier(transaction["id"])
        and isinstance(transaction["phase"], str)
        and transaction["phase"] in {"INTENT", "QUARANTINED", "REMOVE_ROOT_READY"}
        and transaction["identity"] == value["identity"]
        and (
            value["state"] == "ELIGIBLE"
            and transaction["phase"] == "INTENT"
            or value["state"] == "QUARANTINED"
            and transaction["phase"] in {"QUARANTINED", "REMOVE_ROOT_READY"}
            or value["state"] == "REMOVED"
            and transaction["phase"] == "REMOVE_ROOT_READY"
        )
    )


class StorageManager:
    def __init__(
        self,
        root,
        *,
        policy=None,
        verify_owner=None,
        verify_publication=None,
        protected_roots=(),
        create=True,
        observe=None,
    ):
        self.root = Path(root).absolute()
        self.policy = configured_policy(policy)
        self.observe = observe or observe_processes
        self.verify_owner = verify_owner
        self.verify_publication = verify_publication
        self.protected_roots = tuple(Path(p).absolute() for p in protected_roots)
        self.namespace_identity = None
        self.directory_identities = {}
        try:
            fd = _open_directory(self.root, create=create)
        except FileNotFoundError:
            if create:
                raise
            return
        try:
            root_identity = _private(fd)
            with coordinate(fd, create=create):
                try:
                    namespace = _read_at(fd, "namespace.json")
                except FileNotFoundError:
                    if not create:
                        raise StorageError("namespace_uninitialized") from None
                    with os.scandir(fd) as iterator:
                        if any(entry.name != "lock" for entry in iterator):
                            raise StorageError("unregistered_namespace") from None
                    identities = {}
                    for name in SUBDIRECTORIES:
                        os.mkdir(name, 0o700, dir_fd=fd)
                        child = os.open(
                            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        try:
                            identities[name] = _private(child)
                        finally:
                            os.close(child)
                    namespace = {
                        "schema_version": VERSION,
                        "identity": root_identity,
                        "directories": identities,
                    }
                    _write_at(fd, "namespace.json", namespace)
                if (
                    not isinstance(namespace, dict)
                    or set(namespace) != {"schema_version", "identity", "directories"}
                    or type(namespace["schema_version"]) is not int
                    or namespace["schema_version"] != VERSION
                    or namespace["identity"] != root_identity
                    or set(namespace["directories"]) != set(SUBDIRECTORIES)
                ):
                    raise StorageError("namespace_replaced")
                self.namespace_identity = root_identity
                self.directory_identities = namespace["directories"]
                opened = self._directories(fd)
                for descriptor in opened.values():
                    os.close(descriptor)
        finally:
            os.close(fd)

    def _directories(self, root):
        result = {}
        try:
            for name in SUBDIRECTORIES:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                result[name] = child
                observed = _private(child)
                if (
                    observed != self.directory_identities[name]
                    or observed["device"] != self.namespace_identity["device"]
                ):
                    raise StorageError("namespace_replaced")
            return result
        except BaseException:
            for child in result.values():
                os.close(child)
            raise

    @contextlib.contextmanager
    def _locked(self):
        if self.namespace_identity is None:
            raise StorageError("namespace_absent")
        root = _open_directory(self.root)
        children = {}
        try:
            if _private(root) != self.namespace_identity:
                raise StorageError("namespace_replaced")
            with coordinate(root, create=False):
                namespace = _read_at(root, "namespace.json")
                if namespace != {
                    "schema_version": VERSION,
                    "identity": self.namespace_identity,
                    "directories": self.directory_identities,
                }:
                    raise StorageError("namespace_replaced")
                children = self._directories(root)
                yield children
        finally:
            for child in children.values():
                os.close(child)
            os.close(root)

    def _record(self, directories, allocation_id):
        if not _identifier(allocation_id):
            raise StorageError("invalid_allocation")
        value = _read_at(directories["records"], allocation_id + ".json")
        if (
            not isinstance(value, dict)
            or set(value) != RECORD_KEYS
            or value["schema_version"] not in {1, RECORD_VERSION}
            or type(value["schema_version"]) is not int
            or value["allocation_id"] != allocation_id
            or not isinstance(value["state"], str)
            or value["state"] not in STATES
            or not isinstance(value["class"], str)
            or value["class"] not in CLASSES
            or type(value["generation"]) is not int
            or value["generation"] < 1
            or not _hash(value["declaration_digest"])
            or not _valid_owner(value["owner"])
            or not _valid_identity(value["identity"])
            or not _valid_transaction(value)
            or not isinstance(value["consumers"], list)
            or value["terminal_outcome"] is not None
            and (
                not isinstance(value["terminal_outcome"], str)
                or value["terminal_outcome"]
                not in {"passed", "failed", "skipped", "cancelled", "unknown", "infra"}
            )
            or len(value["consumers"]) > 128
            or any(
                not isinstance(c, dict)
                or set(c) != {"consumer_id", "owner_reference"}
                or not _identifier(c["consumer_id"])
                or not isinstance(c["owner_reference"], dict)
                for c in value["consumers"]
            )
            or len({c["consumer_id"] for c in value["consumers"]}) != len(value["consumers"])
        ):
            raise StorageError("invalid_allocation_record")
        return value

    def _save(self, directories, value):
        _write_at(directories["records"], value["allocation_id"] + ".json", value)

    def path(self, allocation_id):
        if not _identifier(allocation_id):
            raise StorageError("invalid_allocation")
        return self.root / "allocations" / allocation_id

    def allocate(self, *, owner, allocation_class, declaration_digest):
        if (
            not _valid_owner(owner)
            or not isinstance(allocation_class, str)
            or allocation_class not in CLASSES
            or not _hash(declaration_digest)
        ):
            raise StorageError("invalid_allocation_request")
        from .storage_inventory import pressure

        try:
            self.last_pressure = pressure(
                self,
                requested_class=allocation_class,
                limits={"entries": 256, "records": 128, "seconds": 0.05},
            )
        except (OSError, StorageError):
            self.last_pressure = {
                "warning": None,
                "measurement_complete": False,
                "reason": "storage_observation_unavailable",
            }
        try:
            # Bounded, best-effort: reclaim provably settled or expired scratch
            # before admitting new scratch so the namespace cannot grow without limit.
            seconds = PRESSURE_OPPORTUNISTIC_SECONDS if self.under_pressure() else OPPORTUNISTIC_SECONDS
            self.last_expiry = self.reclaim(limits={"seconds": seconds})
        except (OSError, StorageError):
            self.last_expiry = None
        with self._locked() as directories:
            if (
                os.statvfs(self.root).f_bavail * os.statvfs(self.root).f_frsize
                < self.policy["minimum_free_bytes"]
            ):
                raise StorageError("storage_reserve_unavailable")
            allocation_id = secrets.token_hex(16)
            os.mkdir(allocation_id, 0o700, dir_fd=directories["allocations"])
            os.fsync(directories["allocations"])
            info = os.stat(allocation_id, dir_fd=directories["allocations"], follow_symlinks=False)
            value = {
                "schema_version": RECORD_VERSION,
                "allocation_id": allocation_id,
                "generation": 1,
                "state": "ALLOCATED",
                "class": allocation_class,
                "declaration_digest": declaration_digest,
                "owner": dict(owner),
                "identity": identity(info),
                "consumers": [],
                "owner_reference": None,
                "publication_reference": None,
                "completion_reference": None,
                "retention_reason": None,
                "transaction": None,
                "attestation": None,
                "receipt": None,
                "terminal_outcome": None,
            }
            self._save(directories, value)
            return {
                "schema_version": VERSION,
                "allocation_id": allocation_id,
                "generation": 1,
                "path": str(self.path(allocation_id)),
                "identity": value["identity"],
                "owner": value["owner"],
                "declaration_digest": declaration_digest,
            }

    def mark_running(self, allocation_id, *, generation, owner_receipt):
        with self._locked() as directories:
            value = self._record(directories, allocation_id)
            if value["generation"] != generation or value["state"] != "ALLOCATED":
                raise StorageError("allocation_generation_mismatch")
            if not isinstance(owner_receipt, dict) or len(canonical(owner_receipt)) > 65536:
                raise StorageError("invalid_owner_reference")
            value.update(state="RUNNING", generation=generation + 1, owner_reference=owner_receipt)
            self._save(directories, value)
            return value

    def retain(self, allocation_id, *, generation=None, reason):
        if (
            not isinstance(reason, str)
            or not reason
            or len(reason) > 80
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in reason)
        ):
            raise StorageError("invalid_retention_reason")
        with self._locked() as directories:
            value = self._record(directories, allocation_id)
            if generation is not None and generation != value["generation"]:
                raise StorageError("allocation_generation_mismatch")
            if value["transaction"] is not None or value["state"] == "REMOVED":
                return value
            value.update(
                state="RETAINED", retention_reason=reason, generation=value["generation"] + 1
            )
            self._save(directories, value)
            return value

    def register_consumer(self, allocation_id, *, consumer_id, owner_reference):
        if not _identifier(consumer_id) or not isinstance(owner_reference, dict):
            raise StorageError("invalid_consumer")
        with self._locked() as directories:
            value = self._record(directories, allocation_id)
            if (
                value["state"] not in {"ALLOCATED", "RUNNING", "RETAINED"}
                or len(value["consumers"]) >= 128
            ):
                raise StorageError("consumer_admission_closed")
            if any(c["consumer_id"] == consumer_id for c in value["consumers"]):
                raise StorageError("duplicate_consumer")
            value["consumers"].append(
                {"consumer_id": consumer_id, "owner_reference": owner_reference}
            )
            value["generation"] += 1
            self._save(directories, value)

    def release_consumer(self, allocation_id, *, consumer_id, completion_reference):
        if self.verify_owner is None:
            raise StorageError("trusted_validator_unavailable")
        with self._locked() as directories:
            value = self._record(directories, allocation_id)
            consumer = next(
                (c for c in value["consumers"] if c["consumer_id"] == consumer_id), None
            )
            if consumer is None:
                raise StorageError("unknown_consumer")
            proof = self.verify_owner(value | {"consumer": consumer}, completion_reference)
            if (
                not isinstance(proof, dict)
                or proof.get("outcome") != "passed"
                or any(
                    proof.get(k) is not True
                    for k in (
                        "settled",
                        "accounting_complete",
                        "source_current",
                        "classification_current",
                    )
                )
            ):
                raise StorageError("consumer_unsettled")
            value["consumers"].remove(consumer)
            value["generation"] += 1
            self._save(directories, value)

    def _live_identity(self, directories, value):
        current = os.stat(
            value["allocation_id"], dir_fd=directories["allocations"], follow_symlinks=False
        )
        if identity(current) != value["identity"] or not stat.S_ISDIR(current.st_mode):
            raise StorageError("allocation_replaced")
        path = self.path(value["allocation_id"])
        for protected in self.protected_roots:
            if (
                path == protected
                or path.is_relative_to(protected)
                or protected.is_relative_to(path)
            ):
                raise StorageError("protected_overlap")

    def _attestation(self, value):
        proof = value["attestation"]
        if (
            not isinstance(proof, dict)
            or set(proof) != {"binding", "artifacts"}
            or proof["binding"]
            != {
                "owner": value["owner"],
                "allocation_id": value["allocation_id"],
                "declaration_digest": value["declaration_digest"],
            }
            or not isinstance(proof["artifacts"], list)
            or not 1 <= len(proof["artifacts"]) <= 256
        ):
            raise StorageError("publication_unverified")
        total = 0
        for artifact in proof["artifacts"]:
            if not isinstance(artifact, dict) or set(artifact) != {
                "path",
                "identity",
                "size",
                "sha256",
            }:
                raise StorageError("invalid_publication_artifact")
            path = Path(artifact["path"])
            if (
                not path.is_absolute()
                or path.is_relative_to(self.root)
                or type(artifact["size"]) is not int
                or not 0 <= artifact["size"] <= 256 * 1024**2
            ):
                raise StorageError("publication_overlap")
            total += artifact["size"]
            if total > 256 * 1024**2:
                raise StorageError("publication_limit")
            parent = _open_directory(path.parent)
            descriptor = None
            try:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or identity(info) != artifact["identity"]
                    or info.st_size != artifact["size"]
                    or not _hash(artifact["sha256"])
                ):
                    raise StorageError("publication_changed")
                hasher = hashlib.sha256()
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    for block in iter(lambda: stream.read(65536), b""):
                        hasher.update(block)
                if hasher.hexdigest() != artifact["sha256"] or not unchanged(
                    info, os.fstat(descriptor)
                ):
                    raise StorageError("publication_changed")
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                os.close(parent)

    def finalize(self, allocation_id, *, publication_reference, completion_reference, limits=None):
        if self.verify_owner is None or self.verify_publication is None:
            raise StorageError("trusted_validator_unavailable")
        with self._locked() as directories:
            value = self._record(directories, allocation_id)
            if value["schema_version"] != RECORD_VERSION and value["state"] != "REMOVED":
                return {
                    "state": "RETAINED",
                    "reason": "legacy_settlement_unproven",
                    "allocation_id": allocation_id,
                }
            if (
                value["state"] in {"ELIGIBLE", "REMOVED", "QUARANTINED"}
                and value["retention_reason"] in LATE_REASONS
            ):
                # Reclaimed later (proven settlement or expiry); cleanup is a
                # separate receipt and never changes the evidence result.
                if value["state"] == "REMOVED":
                    return value["receipt"]
                return self._cleanup(directories, value, _limits(limits))
            if value["state"] in {"REMOVED", "QUARANTINED"} and (
                value["publication_reference"] != publication_reference
                or value["completion_reference"] != completion_reference
            ):
                raise StorageError("conflicting_finalization")
            if value["state"] == "REMOVED":
                return value["receipt"]
            if value["state"] == "QUARANTINED":
                return self._cleanup(directories, value, _limits(limits))
            if value["terminal_outcome"] not in {None, "passed"}:
                return {
                    "state": "RETAINED",
                    "reason": "unsuccessful_outcome",
                    "allocation_id": allocation_id,
                }
            owner = self.verify_owner(value, completion_reference)
            required = {
                "outcome",
                "settled",
                "accounting_complete",
                "source_current",
                "classification_current",
            }
            valid = isinstance(owner, dict) and set(owner) == required
            value["terminal_outcome"] = (
                owner["outcome"]
                if valid
                and owner["outcome"]
                in {"passed", "failed", "skipped", "cancelled", "unknown", "infra"}
                else "unknown"
            )
            value["completion_reference"] = completion_reference
            if (
                not valid
                or value["terminal_outcome"] != "passed"
                or any(owner[k] is not True for k in required - {"outcome"})
                or value["consumers"]
            ):
                value.update(
                    state="RETAINED",
                    retention_reason=(
                        "descendant_settlement_unproven"
                        if value["retention_reason"] == "descendant_settlement_unproven"
                        and (not isinstance(owner, dict) or owner.get("settled") is not True)
                        else "owner_unverified"
                    ),
                    generation=value["generation"] + 1,
                )
                self._save(directories, value)
                return {
                    "state": "RETAINED",
                    "reason": value["retention_reason"],
                    "allocation_id": allocation_id,
                }
            publication = self.verify_publication(value, publication_reference)
            value["attestation"] = publication
            self._attestation(value)
            self._live_identity(directories, value)
            value.update(
                state="ELIGIBLE",
                publication_reference=publication_reference,
                completion_reference=completion_reference,
                retention_reason=None,
                generation=value["generation"] + 1,
            )
            self._save(directories, value)
            return self._cleanup(directories, value, _limits(limits))

    def preview(self, *, allocation_ids=None, limits=None, cursor=None):
        bound = _limits(limits)
        if cursor is not None:
            raise StorageError("unsupported_cursor")
        if self.namespace_identity is None:
            return {"schema_version": VERSION, "namespace": None, "entries": [], "complete": True}
        with self._locked() as directories:
            names = []
            complete = True
            deadline = time.monotonic() + bound["seconds"]
            with os.scandir(directories["records"]) as iterator:
                for entry in iterator:
                    if len(names) >= bound["records"] or time.monotonic() >= deadline:
                        complete = False
                        break
                    names.append(entry.name)
            selected = set(allocation_ids) if allocation_ids is not None else None
            entries = []
            for name in sorted(names):
                if time.monotonic() >= deadline:
                    complete = False
                    break
                if not name.endswith(".json") or not _identifier(name[:-5]):
                    raise StorageError("unknown_registry_member")
                if selected is not None and name[:-5] not in selected:
                    continue
                value = self._record(directories, name[:-5])
                entries.append(
                    {
                        k: value[k]
                        for k in (
                            "allocation_id",
                            "generation",
                            "state",
                            "class",
                            "identity",
                            "declaration_digest",
                            "publication_reference",
                            "completion_reference",
                            "retention_reason",
                        )
                    }
                )
            return {
                "schema_version": VERSION,
                "namespace": self.namespace_identity,
                "entries": entries,
                "complete": complete,
            }

    def apply(self, plan, *, limits=None):
        bound = _limits(limits)
        fields = {
            "allocation_id",
            "generation",
            "state",
            "class",
            "identity",
            "declaration_digest",
            "publication_reference",
            "completion_reference",
            "retention_reason",
        }
        if (
            not isinstance(plan, dict)
            or set(plan) != {"schema_version", "namespace", "entries", "complete"}
            or type(plan["schema_version"]) is not int
            or plan["schema_version"] != VERSION
            or plan["namespace"] != self.namespace_identity
            or type(plan["complete"]) is not bool
            or not isinstance(plan["entries"], list)
            or len(plan["entries"]) > bound["records"]
            or any(not isinstance(e, dict) or set(e) != fields for e in plan["entries"])
        ):
            raise StorageError("invalid_cleanup_plan")
        ids = [e["allocation_id"] for e in plan["entries"]]
        if any(not _identifier(i) for i in ids) or len(set(ids)) != len(ids):
            raise StorageError("invalid_cleanup_plan")
        results, complete = [], True
        deadline = time.monotonic() + bound["seconds"]
        remaining = bound["entries"]
        if self.namespace_identity is None and not ids:
            return {"schema_version": VERSION, "results": [], "complete": True}
        with self._locked() as directories:
            for entry in plan["entries"]:
                value = self._record(directories, entry["allocation_id"])
                expected = {k: value[k] for k in fields}
                if entry != expected:
                    if value["state"] == "REMOVED" and entry["identity"] == value["identity"]:
                        results.append(value["receipt"])
                        continue
                    results.append(
                        {
                            "allocation_id": value["allocation_id"],
                            "state": "RETAINED",
                            "reason": "stale_plan",
                        }
                    )
                    continue
                if value["schema_version"] != RECORD_VERSION and value["state"] != "REMOVED":
                    results.append(
                        {
                            "allocation_id": value["allocation_id"],
                            "state": "RETAINED",
                            "reason": "legacy_settlement_unproven",
                        }
                    )
                    complete = False
                    continue
                if value["state"] not in {"ELIGIBLE", "QUARANTINED"}:
                    results.append(
                        {
                            "allocation_id": value["allocation_id"],
                            "state": value["state"],
                            "reason": value["retention_reason"] or "not_eligible",
                        }
                    )
                    continue
                seconds = deadline - time.monotonic()
                if seconds <= 0 or remaining <= 0:
                    complete = False
                    results.append(
                        {
                            "allocation_id": value["allocation_id"],
                            "state": value["state"],
                            "reason": "work_limit",
                        }
                    )
                    continue
                receipt = self._cleanup(
                    directories, value, bound | {"entries": remaining, "seconds": min(seconds, 30)}
                )
                remaining -= receipt.get("processed_entries", 0)
                complete &= receipt["state"] != "PARTIAL"
                results.append(receipt)
        return {"schema_version": VERSION, "results": results, "complete": complete}

    def expire(self, *, limits=None, now=None):
        """One bounded applying reclaim pass (kept for callers of the old name)."""
        return self.reclaim(limits=limits, now=now, apply=True)

    def under_pressure(self):
        """Free space below ``pressure_free_bytes`` or the last soft warning (#164)."""
        try:
            info = os.statvfs(self.root)
            if info.f_bavail * info.f_frsize < self.policy["pressure_free_bytes"]:
                return True
        except OSError:
            pass
        return bool((getattr(self, "last_pressure", None) or {}).get("warning"))

    def _retention(self, value, pressured):
        retention = self.policy["retention_seconds"]
        failed = self.policy["failed_retention_seconds"]
        if (
            failed
            and value["state"] == "RETAINED"
            and value["class"] in SCRATCH_CLASSES
            and value["terminal_outcome"] != "passed"
        ):
            retention = min(retention, failed) if retention else failed
        if pressured and self.policy["pressure_retention_seconds"]:
            pressure = self.policy["pressure_retention_seconds"]
            retention = min(retention, pressure) if retention else pressure
        return retention

    def _reclaim_rule(self, value, age, pressured=False):
        """(rule, blocker) for one record from metadata alone.

        ``settlement_proven_later``: the owner finalized a passed result and only
        descendant settlement was unproven; reclaimable after the grace period
        once a process observation proves nothing still uses it.
        ``retention_expired``: anything else retained or abandoned, after the
        retention window and the same process observation.
        """
        if value["state"] in {"ELIGIBLE", "QUARANTINED"} and value["retention_reason"] in (
            LATE_REASONS
        ):
            return "resume", None
        if value["state"] not in EXPIRABLE_STATES:
            return None, None
        if value["transaction"] is not None:
            return None, "cleanup_transaction_open"
        if value["consumers"]:
            return None, "active_consumer"
        if (
            value["state"] == "RETAINED"
            and value["retention_reason"] == UNPROVEN
            and value["terminal_outcome"] == "passed"
            and value["completion_reference"] is not None
            and value["schema_version"] == RECORD_VERSION
        ):
            if age >= self.policy["settlement_grace_seconds"]:
                return SETTLED_LATER, None
            blocker = "settlement_grace_period"
        else:
            blocker = (
                "owner_unfinalized"
                if value["state"] != "RETAINED"
                else value["retention_reason"] or "owner_unsettled"
            )
        retention = self._retention(value, pressured)
        if retention and age >= retention:
            return EXPIRED, None
        return None, blocker

    def reclaim(self, *, limits=None, now=None, apply=True, measure=False, max_records=None):
        """Bounded, idempotent reclaim of retained scratch that is provably settled.

        A candidate (see ``_reclaim_rule``) is removed only when one process
        observation, taken after the metadata pass, shows that neither its
        recorded owner (leader, process group or session) nor any process with
        its cwd, root, executable, mapped or open file inside the allocation is
        alive. An unavailable or incomplete observation keeps every candidate
        retained as ``settlement_undecidable``. With ``apply=False`` nothing is
        changed: the result lists what would be reclaimed. Partial removal
        continues on the next pass. ``max_records`` lets an explicit prune
        examine more records than the per-call ``records`` limit.
        """
        bound = _limits(limits)
        result = {
            "schema_version": VERSION,
            "apply": bool(apply),
            "results": [],
            "candidates": [],
            "retained": [],
            "retained_by_reason": {},
            "examined": 0,
            "records": 0,
            "reclaimed_logical_bytes": 0,
            "reclaimed_allocated_bytes_estimate": 0,
            "complete": True,
        }
        if self.namespace_identity is None:
            return result
        now = time.time() if now is None else now
        deadline = time.monotonic() + bound["seconds"]
        pressured = self.under_pressure()
        result["pressure"] = pressured

        def keep(allocation_id, reason, listed=True):
            result["retained_by_reason"][reason] = result["retained_by_reason"].get(reason, 0) + 1
            if listed and len(result["retained"]) < 1000:
                result["retained"].append({"allocation_id": allocation_id, "reason": reason})

        candidates = []
        with self._locked() as directories:
            with os.scandir(directories["records"]) as iterator:
                names = sorted(
                    entry.name
                    for entry in iterator
                    if entry.name.endswith(".json") and _identifier(entry.name[:-5])
                )
            result["records"] = len(names)
            if names and pressured:
                # Under pressure the oldest scratch goes first.
                def mtime(name):
                    try:
                        return os.stat(name, dir_fd=directories["records"]).st_mtime
                    except OSError:
                        return 0.0

                names.sort(key=mtime)
            elif names:
                # Rotate so a bounded pass over a large namespace is not stuck on
                # the same first records.
                start = secrets.randbelow(len(names))
                names = names[start:] + names[:start]
            cap = bound["records"] if max_records is None else max_records
            if len(names) > cap:
                names = names[:cap]
                result["complete"] = False
            for name in names:
                if time.monotonic() >= deadline:
                    result["complete"] = False
                    break
                result["examined"] += 1
                try:
                    value = self._record(directories, name[:-5])
                    age = now - os.stat(name, dir_fd=directories["records"]).st_mtime
                except (OSError, StorageError):
                    keep(name[:-5], "record_unreadable")
                    continue
                rule, blocker = self._reclaim_rule(value, age, pressured)
                if rule is None:
                    if blocker is not None:
                        keep(value["allocation_id"], blocker, listed=False)
                    continue
                candidates.append((value["allocation_id"], value["generation"], rule))
        if not candidates:
            return result
        observed, undecidable = None, None
        if any(rule != "resume" for _, _, rule in candidates):
            # The scan runs outside the namespace lock; decisions recheck the
            # record generation under the lock afterwards.
            try:
                observed = self.observe(
                    self.root / "allocations", deadline=max(deadline, time.monotonic() + 1.0)
                )
            except (OSError, StorageError) as error:
                undecidable = "settlement_undecidable"
                result["observation_error"] = getattr(error, "code", None) or type(error).__name__
        remaining = bound["entries"]
        with self._locked() as directories:
            for allocation_id, generation, rule in candidates:
                try:
                    value = self._record(directories, allocation_id)
                except (OSError, StorageError):
                    keep(allocation_id, "record_unreadable")
                    continue
                if value["generation"] != generation:
                    keep(allocation_id, "changed_during_reclaim")
                    continue
                if rule != "resume":
                    if undecidable:
                        keep(allocation_id, undecidable)
                        continue
                    if _owner_present(value, observed):
                        keep(allocation_id, "owner_process_alive")
                        continue
                    if observed["users"].get(allocation_id):
                        keep(allocation_id, "allocation_in_use")
                        continue
                if not apply:
                    row = {
                        "allocation_id": allocation_id,
                        "state": value["state"],
                        "retention_reason": value["retention_reason"],
                        "rule": rule,
                    }
                    if measure:
                        row["measurement"] = self._measure(value, deadline)
                    result["candidates"].append(row)
                    continue
                if time.monotonic() >= deadline or remaining <= 0:
                    result["complete"] = False
                    keep(allocation_id, "work_limit")
                    continue
                try:
                    if rule != "resume":
                        value.update(
                            state="ELIGIBLE",
                            retention_reason=rule,
                            generation=value["generation"] + 1,
                        )
                        try:
                            os.stat(
                                allocation_id,
                                dir_fd=directories["allocations"],
                                follow_symlinks=False,
                            )
                        except FileNotFoundError:
                            # Directory already gone: only the record remains.
                            value.update(
                                state="QUARANTINED",
                                transaction={
                                    "id": secrets.token_hex(16),
                                    "phase": "REMOVE_ROOT_READY",
                                    "identity": value["identity"],
                                },
                            )
                        self._save(directories, value)
                    seconds = deadline - time.monotonic()
                    receipt = self._cleanup(
                        directories,
                        value,
                        bound | {"entries": remaining, "seconds": max(min(seconds, 30), 0.001)},
                    )
                except (OSError, StorageError) as error:
                    result["results"].append(
                        {
                            "allocation_id": allocation_id,
                            "state": "RETAINED",
                            "reason": str(error) or type(error).__name__,
                        }
                    )
                    continue
                remaining -= receipt.get("processed_entries", 0)
                result["reclaimed_logical_bytes"] += receipt.get("logical_bytes") or 0
                if result["reclaimed_allocated_bytes_estimate"] is not None:
                    allocated = receipt.get("allocated_bytes_estimate")
                    result["reclaimed_allocated_bytes_estimate"] = (
                        None
                        if allocated is None
                        else result["reclaimed_allocated_bytes_estimate"] + allocated
                    )
                if receipt["state"] == "PARTIAL":
                    result["complete"] = False
                result["results"].append(receipt)
        return result

    def _measure(self, value, deadline):
        from .storage_inventory import measure_tree

        seconds = deadline - time.monotonic()
        if seconds <= 0:
            return None
        try:
            descriptor = _open_directory(self.path(value["allocation_id"]))
        except (OSError, StorageError):
            return None
        try:
            measured = measure_tree(descriptor, limits={"seconds": min(seconds, 30)})
        except (OSError, StorageError):
            return None
        finally:
            os.close(descriptor)
        return {
            k: measured[k]
            for k in ("logical_bytes", "allocated_bytes_estimate", "entries", "complete")
        }

    def resume_cleanup(self, allocation_id, *, transaction_id, limits=None):
        with self._locked() as directories:
            value = self._record(directories, allocation_id)
            if not value["transaction"] or value["transaction"]["id"] != transaction_id:
                raise StorageError("cleanup_transaction_mismatch")
            if value["state"] == "REMOVED":
                return value["receipt"]
            return self._cleanup(directories, value, _limits(limits))

    def _cleanup(self, directories, value, bound):
        # Late reclaim (proven settlement or expiry) has no publication attestation
        # to recheck; its process observation was the precondition instead.
        expired = value["retention_reason"] in LATE_REASONS
        if value["schema_version"] != RECORD_VERSION and not expired:
            raise StorageError("legacy_settlement_unproven")
        free_before = _free_bytes(directories["allocations"])
        if not expired:
            self._attestation(value)
        if value["consumers"]:
            raise StorageError("active_consumer")
        transaction = value["transaction"]
        if transaction is None:
            self._live_identity(directories, value)
            transaction = {
                "id": secrets.token_hex(16),
                "phase": "INTENT",
                "identity": value["identity"],
            }
            value["transaction"] = transaction
            self._save(directories, value)
        name = transaction["id"]
        if not _identifier(name) or transaction["identity"] != value["identity"]:
            raise StorageError("invalid_cleanup_transaction")
        try:
            info = os.stat(name, dir_fd=directories["quarantine"], follow_symlinks=False)
        except FileNotFoundError:
            if transaction["phase"] == "REMOVE_ROOT_READY":
                try:
                    original = os.stat(
                        value["allocation_id"],
                        dir_fd=directories["allocations"],
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                else:
                    if identity(original) == value["identity"]:
                        raise StorageError("cleanup_location_conflict")
                return self._removed(directories, value, 0)
            if transaction["phase"] != "INTENT":
                raise StorageError("quarantine_missing") from None
            self._live_identity(directories, value)
            os.rename(
                value["allocation_id"],
                name,
                src_dir_fd=directories["allocations"],
                dst_dir_fd=directories["quarantine"],
            )
            os.fsync(directories["allocations"])
            os.fsync(directories["quarantine"])
            info = os.stat(name, dir_fd=directories["quarantine"], follow_symlinks=False)
        if identity(info) != value["identity"] or not stat.S_ISDIR(info.st_mode):
            raise StorageError("quarantine_replaced")
        transaction["phase"] = "QUARANTINED"
        value.update(state="QUARANTINED", generation=value["generation"] + 1)
        self._save(directories, value)
        fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directories["quarantine"]
        )
        progress = {
            "entries": 0,
            "logical_bytes": 0,
            "allocated_bytes": 0,
            "seen": set(),
            "deadline": time.monotonic() + bound["seconds"],
        }
        try:
            if identity(os.fstat(fd)) != value["identity"]:
                raise StorageError("quarantine_replaced")
            complete = _delete_contents(fd, value["identity"]["device"], bound, progress, 0)
        finally:
            os.close(fd)
        if complete:
            transaction["phase"] = "REMOVE_ROOT_READY"
            self._save(directories, value)
            current = os.stat(name, dir_fd=directories["quarantine"], follow_symlinks=False)
            if identity(current) != value["identity"]:
                raise StorageError("quarantine_replaced")
            os.rmdir(name, dir_fd=directories["quarantine"])
            os.fsync(directories["quarantine"])
            return self._removed(directories, value, progress["entries"], free_before, progress)
        receipt = {
            "schema_version": VERSION,
            "allocation_id": value["allocation_id"],
            "transaction_id": name,
            "state": "PARTIAL",
            "reason": "work_limit",
            "processed_entries": progress["entries"],
            "logical_bytes": progress["logical_bytes"],
            "allocated_bytes_estimate": progress["allocated_bytes"],
            "filesystem_free_space": _space_observation(
                free_before, _free_bytes(directories["allocations"])
            ),
        }
        self._publish_receipt(directories, receipt)
        value["receipt"] = receipt
        self._save(directories, value)
        return receipt

    def _removed(self, directories, value, processed, free_before=None, progress=None):
        receipt = {
            "schema_version": VERSION,
            "allocation_id": value["allocation_id"],
            "transaction_id": value["transaction"]["id"],
            "state": "REMOVED",
            "reason": value["retention_reason"]
            if value["retention_reason"] in LATE_REASONS
            else "settled_published",
            "processed_entries": processed,
            # Bytes unlinked by this final step (earlier PARTIAL receipts carry
            # their own); not a promise about recovered free space.
            "logical_bytes": progress["logical_bytes"] if progress else 0,
            "allocated_bytes_estimate": progress["allocated_bytes"] if progress else 0,
            "recovered_bytes": None,
            "filesystem_free_space": _space_observation(
                free_before, _free_bytes(directories["allocations"])
            ),
        }
        self._publish_receipt(directories, receipt)
        value.update(state="REMOVED", receipt=receipt, generation=value["generation"] + 1)
        self._save(directories, value)
        return receipt

    def _publish_receipt(self, directories, receipt):
        receipt["receipt_id"] = secrets.token_hex(16)
        _publish_at(directories["receipts"], receipt["receipt_id"] + ".json", receipt)
