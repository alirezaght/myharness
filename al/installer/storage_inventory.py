"""Bounded metadata-only accounting for managed allocations and legacy storage."""

from __future__ import annotations

import os
import stat
import time

from .storage_filesystem import StorageError, _open_directory, identity
from .storage_filesystem import limits as _limits


def measure_tree(root_descriptor, *, limits=None, cursor=None, _seen=None):
    bound = _limits(limits)
    if cursor is not None:
        raise StorageError("unsupported_cursor")
    seen = set() if _seen is None else _seen
    deadline = time.monotonic() + bound["seconds"]
    result = {
        "logical_bytes": 0,
        "allocated_bytes_estimate": 0,
        "entries": 0,
        "complete": True,
        "errors": [],
    }
    device = os.fstat(root_descriptor).st_dev

    def account(info):
        inode = (info.st_dev, info.st_ino)
        if inode in seen:
            return
        seen.add(inode)
        result["logical_bytes"] += info.st_size
        if hasattr(info, "st_blocks") and result["allocated_bytes_estimate"] is not None:
            result["allocated_bytes_estimate"] += info.st_blocks * 512
        else:
            result["allocated_bytes_estimate"] = None

    def visit(fd, depth):
        if depth >= bound["depth"]:
            result["complete"] = False
            return
        with os.scandir(fd) as iterator:
            for entry in iterator:
                if result["entries"] >= bound["entries"] or time.monotonic() >= deadline:
                    result["complete"] = False
                    return
                result["entries"] += 1
                try:
                    info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    if info.st_dev != device:
                        result["complete"] = False
                        result["errors"].append("filesystem_boundary")
                        continue
                    account(info)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(
                            entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        try:
                            if identity(os.fstat(child)) != identity(info):
                                raise StorageError("member_replaced")
                            visit(child, depth + 1)
                        finally:
                            os.close(child)
                except (OSError, StorageError):
                    result["complete"] = False
                    if "metadata_unavailable" not in result["errors"]:
                        result["errors"].append("metadata_unavailable")

    account(os.fstat(root_descriptor))
    visit(root_descriptor, 0)
    result["errors"] = sorted(set(result["errors"]))[:16]
    return result


def inspect(manager, *, limits=None, cursor=None, include_legacy=False):
    bound = _limits(limits)
    preview = manager.preview(limits=bound, cursor=cursor)
    result = {
        "schema_version": 1,
        "complete": preview["complete"],
        "allocations": [],
        "legacy": [],
        "retained_by_reason": {},
        "logical_bytes": 0,
        "allocated_bytes_estimate": 0,
        "errors": [],
        "observed_entries": 0,
    }
    seen = set()
    deadline = time.monotonic() + bound["seconds"]

    def measure(path):
        remaining = bound["entries"] - result["observed_entries"]
        seconds = deadline - time.monotonic()
        if remaining <= 0 or seconds <= 0:
            result["complete"] = False
            return {
                "logical_bytes": 0,
                "allocated_bytes_estimate": 0,
                "entries": 0,
                "complete": False,
                "errors": ["work_limit"],
            }
        try:
            descriptor = _open_directory(path)
            try:
                measured = measure_tree(
                    descriptor,
                    limits=bound | {"entries": remaining, "seconds": min(seconds, 30)},
                    _seen=seen,
                )
            finally:
                os.close(descriptor)
        except (OSError, StorageError):
            measured = {
                "logical_bytes": 0,
                "allocated_bytes_estimate": None,
                "entries": 0,
                "complete": False,
                "errors": ["metadata_unavailable"],
            }
        result["observed_entries"] += measured["entries"]
        result["logical_bytes"] += measured["logical_bytes"]
        if measured["allocated_bytes_estimate"] is None:
            result["allocated_bytes_estimate"] = None
        elif result["allocated_bytes_estimate"] is not None:
            result["allocated_bytes_estimate"] += measured["allocated_bytes_estimate"]
        result["complete"] &= measured["complete"]
        return measured

    for entry in preview["entries"]:
        row = {
            k: entry[k]
            for k in ("allocation_id", "generation", "state", "class", "retention_reason")
        }
        if entry["state"] == "REMOVED":
            row["measurement"] = None
        elif entry["state"] == "QUARANTINED":
            # The transaction is read through the manager, not guessed from a basename.
            with manager._locked() as directories:
                record = manager._record(directories, entry["allocation_id"])
                path = manager.root / "quarantine" / record["transaction"]["id"]
            row["measurement"] = measure(path)
        else:
            row["measurement"] = measure(manager.path(entry["allocation_id"]))
        if entry["state"] not in {"ELIGIBLE", "QUARANTINED", "REMOVED"}:
            reason = entry["retention_reason"] or "owner_unsettled"
            result["retained_by_reason"][reason] = result["retained_by_reason"].get(reason, 0) + 1
        result["allocations"].append(row)
    if include_legacy:
        try:
            parent = _open_directory(manager.root.parent)
            try:
                with os.scandir(parent) as iterator:
                    for index, entry in enumerate(iterator):
                        if index >= bound["records"] or time.monotonic() >= deadline:
                            result["complete"] = False
                            break
                        if entry.name == manager.root.name:
                            continue
                        info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                        row = {"name": entry.name, "state": "UNREGISTERED", "deletable": False}
                        row["measurement"] = (
                            measure(manager.root.parent / entry.name)
                            if stat.S_ISDIR(info.st_mode)
                            else None
                        )
                        result["legacy"].append(row)
            finally:
                os.close(parent)
        except (OSError, StorageError):
            result["complete"] = False
            result["errors"].append("legacy_inventory_unavailable")
    result["largest_observed_allocations"] = sorted(
        result["allocations"],
        key=lambda row: (row["measurement"] or {}).get("logical_bytes", 0),
        reverse=True,
    )[:10]
    free = None
    try:
        target = manager.root if manager.namespace_identity is not None else manager.root.parent
        descriptor = _open_directory(target)
        try:
            if (
                manager.namespace_identity is not None
                and identity(os.fstat(descriptor)) != manager.namespace_identity
            ):
                raise StorageError("namespace_replaced")
            filesystem = os.fstatvfs(descriptor)
            free = filesystem.f_bavail * filesystem.f_frsize
        finally:
            os.close(descriptor)
    except (OSError, StorageError):
        pass
    measured = result["allocated_bytes_estimate"]
    result["pressure"] = {
        "free_bytes": free,
        "reserve_met": free >= manager.policy["minimum_free_bytes"] if free is not None else None,
        "warning": None
        if measured is None or not result["complete"] and measured < manager.policy["warning_bytes"]
        else measured >= manager.policy["warning_bytes"],
        "measurement_complete": result["complete"],
        **manager.policy,
    }
    result["limitations"] = [
        "metadata estimate; partial scans are not totals",
        "allocated blocks do not predict recovered free space",
    ]
    return result


def pressure(manager, *, requested_class, limits=None):
    bound = _limits(limits)
    cleanup = manager.apply(manager.preview(limits=bound), limits=bound)
    observed = inspect(manager, limits=bound)
    return {
        "schema_version": 1,
        **observed["pressure"],
        "cleanup": cleanup,
        "requested_class": requested_class,
        "retained_by_reason": observed["retained_by_reason"],
    }


def pressure_summary(value, policy):
    return {
        key: value.get(key)
        for key in ("free_bytes", "reserve_met", "warning", "measurement_complete")
    } | dict(policy)
