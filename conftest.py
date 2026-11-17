"""Root pytest configuration.

pytest's rootdir collector probes every child directory with ``is_dir()``
before it consults ``norecursedirs``. In this sandbox the ``.claude/`` runtime
directory is containment-protected, so that probe raises
``PermissionError: Operation not permitted`` and collection aborts before any
test runs. ``collect_ignore`` is evaluated ahead of the probe, so listing the
directory here avoids it. The directory is git-ignored runtime state and holds
no tests, so test selection is unchanged.
"""

collect_ignore = [".claude"]
