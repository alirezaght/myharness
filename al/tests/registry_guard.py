"""Catch tests that write into the real global AL registry (#37).

Tests must keep AL state under their own temporary roots. This guard snapshots
the bounded top levels of the real `~/.local/state/al-distribution/registry`
(the home of the account, not `$HOME`, which test runners replace) when a test
session starts and compares them when it ends.

A changed run binding or initialization record that names a temporary directory
was written by a test, so the session fails. Other changes may come from real AL
runs of the same user while the tests run; they are reported as a warning only.
"""

import os
import pwd
import tempfile
import warnings
from pathlib import Path

# Directories of al-run-pins listed one level deep; harness-runtimes two levels deep.
FLAT = ("runs", "initializations", "successors", "successor-fences", "successor-active", "pins")
ATTRIBUTABLE = ("runs", "initializations")
READ_LIMIT = 4 * 1024 * 1024


def real_registry():
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ".local" / "state" / "al-distribution" / "registry"


def _entries(directory, depth):
    rows = {}
    try:
        children = list(os.scandir(directory))
    except OSError:
        return rows
    for child in children:
        try:
            info = child.stat(follow_symlinks=False)
        except OSError:
            continue
        rows[child.path] = (info.st_mtime_ns, info.st_size)
        if depth > 1 and child.is_dir(follow_symlinks=False):
            rows.update(_entries(child.path, depth - 1))
    return rows


def snapshot(registry):
    registry = Path(registry)
    rows = _entries(registry, 1)
    pins = registry / "al-run-pins"
    rows.update(_entries(pins, 1))
    for name in FLAT:
        rows.update(_entries(pins / name, 1))
    rows.update(_entries(pins / "harness-runtimes", 2))
    return rows


def changes(before, after):
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def temporary_markers(*extra):
    roots = {tempfile.gettempdir(), *map(str, extra)}
    markers = set()
    for root in roots:
        markers.add(str(root))
        markers.add(os.path.realpath(root))
    return sorted(marker.rstrip("/") + "/" for marker in markers if len(marker) > 1)


def attributable(registry, paths, markers):
    """Changed run bindings and initialization records that name a temporary directory."""
    pins = Path(registry) / "al-run-pins"
    found = []
    for path in paths:
        path = Path(path)
        if path.parent.name not in ATTRIBUTABLE or path.parent.parent != pins:
            continue
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > READ_LIMIT:
                continue
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in markers):
            found.append(str(path))
    return found


class RegistryGuard:
    """Snapshot at session start; `finish()` returns the leaks that must fail the session."""

    def __init__(self, registry=None, markers=None):
        self.registry = Path(registry) if registry is not None else real_registry()
        self.markers = markers if markers is not None else temporary_markers()
        self.before = snapshot(self.registry)

    def finish(self):
        changed = changes(self.before, snapshot(self.registry))
        leaks = attributable(self.registry, changed, self.markers)
        unattributed = sorted(set(changed) - set(leaks))
        if unattributed:
            warnings.warn(
                "the real AL registry changed during this test session; not attributable to a "
                "test (a concurrent real AL run?): " + ", ".join(unattributed[:10]),
                stacklevel=1,
            )
        return leaks


def failure(leaks):
    return (
        "tests wrote AL run state into the real global registry (#37); isolate the AL state "
        "root of the test (AL_DISTRIBUTION_STATE, HARNESS_ALCTL, HOME) and archive the leaked "
        "records with `alctl prune-stale-runs --apply` once their repositories are gone: "
        + ", ".join(leaks)
    )


_ACTIVE = []


def session_guard():
    """Generator body of a session-scoped autouse fixture; one guard per pytest process."""
    if _ACTIVE:
        yield
        return
    guard = RegistryGuard()
    _ACTIVE.append(guard)
    try:
        yield guard
    finally:
        _ACTIVE.remove(guard)
    leaks = guard.finish()
    if leaks:
        raise AssertionError(failure(leaks))
