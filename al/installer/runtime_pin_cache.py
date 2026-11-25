"""Launcher-only cache of a successful full runtime pin verification (#36).

Every `alctl run` command verifies its protected runtime pin. The full verification
(`runtime_pin.verify_pin`) walks every payload path's ancestors and re-reads and re-hashes
every file. This cache lets the launcher dispatch path skip only that payload re-hash:

* The manifest part of the verification is never skipped. On every command the binding,
  pin storage, manifest bytes (against the binding's digest), manifest schema, pin
  identity, closure digest, interpreter profile, top-level membership and resource
  coverage are checked exactly as the full verification checks them.
* The payload re-hash is skipped only when a record says that a full verification of this
  exact pin (identity and manifest digest, and therefore every file's content hash) passed,
  and every file and directory of the pin still has the quick-check facts (device, inode,
  mode, owner, group, link count, size, mtime and ctime) recorded around that verification.
  Any write, chmod, chown, rename, replacement, ACL or extended-attribute change, added or
  removed entry changes at least one of those facts (the kernel always moves ctime, which
  user code cannot set), so the cached result applies only to the unchanged bytes it
  verified. Anything else is a miss and runs the full verification.
* A record is written only after a successful full verification whose quick-check facts
  were identical before and after it, and never while any entry is younger than the
  timestamp granularity window (a write in the same clock tick could hide). A failed or
  partial verification writes nothing.
* A record that is missing, malformed, for another pin or owner, not a private regular
  file (0600) in a private directory (0700), or that does not match is ignored: the full
  verification runs and decides. The cache can therefore only skip work; it never decides
  acceptance on its own.

install, update, rollback, doctor and every other caller use `runtime_pin.verify_pin`
directly and never consult this cache.
"""

import os
import secrets
import stat
import time
from pathlib import Path

from . import runtime_pin
from .validator_origin import canonical, decode, digest

SCHEMA_VERSION = 1
KIND = "runtime-pin-verification-v1"
DIRECTORY = "pin-verification"
RECORD_LIMIT = 8 * 1024 * 1024
ENTRY_LIMIT = 100_000
# Entries younger than this may share a timestamp with a write that is not yet visible in
# the facts (coarse filesystem clocks); such a pin is verified in full until it is older.
RACY_NS = 2_000_000_000
FIELDS = {
    "schema_version",
    "kind",
    "pin_identity",
    "pin_manifest_digest",
    "owner",
    "files",
    "quick_check",
    "record_digest",
}


class _Unfit(Exception):
    """The pin tree has an entry the quick check does not describe; verify in full."""


def record_path(storage, pin_identity):
    """`<al-run-pins>/pin-verification/<pin>.json`, beside the `pins` store."""
    return Path(storage).parent / DIRECTORY / (pin_identity + ".json")


def _facts(info):
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise _Unfit("special or symbolic link entry")
    return [
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ]


def fingerprint(root):
    """Quick-check facts of the pin directory and every entry below it (lstat only)."""
    root = Path(root)
    facts = {".": _facts(os.lstat(root))}
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                name = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                facts[name] = _facts(info)
                if len(facts) > ENTRY_LIMIT:
                    raise _Unfit("pin tree exceeds the quick-check bound")
                if stat.S_ISDIR(info.st_mode):
                    pending.append((Path(entry.path), name + "/"))
    return facts


def _private_directory(path):
    info = os.lstat(path)
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def _record(storage, binding, manifest):
    """The stored record for this exact pin and manifest, or None (never trusted otherwise)."""
    path = record_path(storage, binding["pin_identity"])
    try:
        if not _private_directory(path.parent):
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or info.st_size > RECORD_LIMIT
            ):
                return None
            raw = b""
            while chunk := os.read(descriptor, RECORD_LIMIT + 1 - len(raw)):
                raw += chunk
                if len(raw) > RECORD_LIMIT:
                    return None
        finally:
            os.close(descriptor)
        record = decode(raw)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None
    if (
        not isinstance(record, dict)
        or set(record) != FIELDS
        or record["schema_version"] != SCHEMA_VERSION
        or record["kind"] != KIND
        or record["pin_identity"] != binding["pin_identity"]
        or record["pin_manifest_digest"] != binding["pin_manifest_digest"]
        or record["owner"] != os.getuid()
        or record["files"] != manifest["complete_file_manifest"]
        or not isinstance(record["quick_check"], dict)
        or record["record_digest"]
        != digest(canonical({k: v for k, v in record.items() if k != "record_digest"}))
    ):
        return None
    return record


def _settled(facts):
    newest = time.time_ns() - RACY_NS
    return all(item[7] <= newest and item[8] <= newest for item in facts.values())


def _store(storage, binding, manifest, facts, *, create):
    """Atomically publish a record; any failure only means no record (never an error)."""
    path = record_path(storage, binding["pin_identity"])
    temporary = None
    try:
        if create:
            try:
                os.mkdir(path.parent, 0o700)
            except FileExistsError:
                pass
        if not _private_directory(path.parent):
            return False
        record = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "pin_identity": binding["pin_identity"],
            "pin_manifest_digest": binding["pin_manifest_digest"],
            "owner": os.getuid(),
            "files": manifest["complete_file_manifest"],
            "quick_check": facts,
        }
        record["record_digest"] = digest(canonical(record))
        data = canonical(record) + b"\n"
        temporary = path.with_name("." + path.name + "." + secrets.token_hex(8) + ".tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
    except (OSError, ValueError):
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def verify_pin_cached(storage, binding, *, readonly=False, captured=None, write=True, create=None):
    """`verify_pin` for the launcher dispatch path, skipping only an unchanged payload re-hash.

    Returns what `verify_pin` returns. On a cache hit "bytes" is `captured` when the caller
    passes bytes it already read from this pin (they are checked against the manifest's
    hashes), otherwise None. Refusals are always the full verification's own refusals.
    `write` publishes a record after a full verification; `create` (default: not
    `readonly`) allows creating the record directory, otherwise a record is written only
    into an existing one."""
    root, manifest = runtime_pin.verified_manifest(storage, binding, readonly=readonly)
    files = manifest["complete_file_manifest"]
    record = _record(storage, binding, manifest)
    if record is not None:
        try:
            hit = fingerprint(root) == record["quick_check"]
        except (OSError, _Unfit):
            hit = False
        if hit and captured is not None:
            hit = (
                isinstance(captured, dict)
                and set(captured) == set(files)
                and all(
                    isinstance(captured[name], bytes)
                    and digest(captured[name]) == files[name]["sha256"]
                    for name in files
                )
            )
        if hit:
            runtime_pin.verify_coverage(manifest)
            return {"root": root, "manifest": manifest, "bytes": captured}
    try:
        before = fingerprint(root)
    except (OSError, _Unfit):
        before = None
    verified = runtime_pin.verify_pin(storage, binding, readonly=readonly)
    if write and before is not None and verified["manifest"] == manifest:
        try:
            after = fingerprint(root)
        except (OSError, _Unfit):
            after = None
        if after == before and _settled(after):
            create = not readonly if create is None else create
            _store(storage, binding, manifest, after, create=create)
    return verified
