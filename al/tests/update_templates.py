"""Private same-path installer fixture prefixes; never caches runtime decisions."""

import os
import shutil
import stat
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from al.tests.tree_templates import tree_digest


def _directory_identity(path):
    value = path.lstat()
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.getuid()
        or stat.S_IMODE(value.st_mode) & 0o077
    ):
        raise ValueError("fixture container must be a private owned directory")
    return value.st_dev, value.st_ino, value.st_uid, stat.S_IMODE(value.st_mode)


def _files(root):
    result = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            value = path.lstat()
            if stat.S_ISLNK(value.st_mode) or stat.S_ISDIR(value.st_mode):
                continue
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_uid != os.getuid()
                or value.st_nlink != 1
            ):
                raise ValueError("fixture contains a foreign, aliased or special file")
            result.add((value.st_dev, value.st_ino))
    return result


def _digest(root):
    return stat.S_IMODE(root.lstat().st_mode), tree_digest(root)


def _copy(source, destination):
    source_files = _files(source)
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    if source_files & _files(destination):
        raise ValueError("fixture copies share regular file identities")


def _plain(value, seen=None):
    """Reject live handles/controllers; prerequisite return values are only data."""
    if isinstance(value, (str, bytes, int, float, bool, type(None), Path)):
        return
    if type(value) not in (dict, list, tuple, set, frozenset):
        raise ValueError("fixture prefix returned a live or unsupported object")
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    values = [*value.keys(), *value.values()] if isinstance(value, dict) else value
    for item in values:
        _plain(item, seen)


@dataclass
class Prefix:
    container: Path
    active: Path
    backup: Path
    container_identity: tuple
    active_identity: tuple
    backup_identity: tuple
    digest: tuple
    value: object = field(repr=False)
    poisoned: bool = False


def build(container, factory):
    """Charge genuine construction, complete copying and validation to first use."""
    container = Path(container).absolute()
    owner = _directory_identity(container)
    active, backup = container / "active", container / "pristine"
    if active.exists() or active.is_symlink() or backup.exists() or backup.is_symlink():
        raise ValueError("fixture prefix paths already exist")
    active.mkdir(mode=0o700)
    active_identity = _directory_identity(active)
    value = factory(active)
    if _directory_identity(active) != active_identity:
        raise ValueError("fixture factory replaced its owned directory")
    _plain(value)
    _files(active)
    expected = _digest(active)
    # An incomplete construction/copy never publishes a Prefix to the caller.
    _copy(active, backup)
    if _digest(active) != expected or _digest(backup) != expected:
        raise ValueError("fixture changed during prefix construction")
    return Prefix(
        container,
        active,
        backup,
        owner,
        active_identity,
        _directory_identity(backup),
        expected,
        deepcopy(value),
    )


def restore(prefix):
    """Restore all owned state at the original absolute paths, including caches."""
    if prefix.poisoned:
        raise ValueError("fixture prefix has an unsettled restore")
    if _directory_identity(prefix.container) != prefix.container_identity:
        raise ValueError("fixture container identity changed")
    if _directory_identity(prefix.active) != prefix.active_identity:
        raise ValueError("fixture active directory identity changed")
    if _directory_identity(prefix.backup) != prefix.backup_identity:
        raise ValueError("pristine fixture directory identity changed")
    if _digest(prefix.backup) != prefix.digest:
        raise ValueError("pristine fixture prefix was modified")
    staging = prefix.container / "restoring"
    if staging.exists() or staging.is_symlink():
        raise ValueError("fixture restore staging is unsettled")
    _copy(prefix.backup, staging)
    if _digest(staging) != prefix.digest or _digest(prefix.backup) != prefix.digest:
        raise ValueError("fixture restore differs from pristine prefix")
    prefix.poisoned = True
    shutil.rmtree(prefix.active)
    staging.rename(prefix.active)
    prefix.active_identity = _directory_identity(prefix.active)
    if _digest(prefix.active) != prefix.digest:
        raise ValueError("fixture changed at restore publication")
    prefix.poisoned = False
    return deepcopy(prefix.value)
