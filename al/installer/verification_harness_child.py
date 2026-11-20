"""Fixed observation-only child: no ledger, private channel or cleanup authority."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import verification_contracts as C
from .verification_harness import read_assignment


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        raise ValueError("fixed recipe child requires assignment, token, scratch and outputs")
    path, token, scratch, output_json = args
    assignment_path = Path(path).absolute()
    from .storage_filesystem import artifact

    reference = {
        "schema_version": 1,
        "reference": str(assignment_path),
        "sha256": artifact(assignment_path)["sha256"],
    }
    assignment, nodes = read_assignment(reference, root=assignment_path.parent)
    outputs = json.loads(output_json)
    expected = {"report", "junit", *("page" + str(n).zfill(3) for n in range(C.MAX_PAGES))}
    if (
        not isinstance(outputs, dict)
        or set(outputs) != expected
        or any(not isinstance(v, str) or not Path(v).is_absolute() for v in outputs.values())
    ):
        raise ValueError("invalid fixed recipe outputs")
    if not C.text(token) or not Path(scratch).is_absolute():
        raise ValueError("invalid fixed recipe launch identity")
    os.environ.pop("HARNESS_CHECK_FD", None)
    os.environ.pop("HARNESS_CHECK_CONTEXT", None)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["PYTEST_ADDOPTS"] = ""
    # Product imports are allowed only after all canonical execution code was loaded.
    root = Path.cwd().resolve(strict=True)
    sys.path.insert(0, str(root))
    import pytest

    from . import verification_pytest

    verification_pytest.configure(assignment, nodes, outputs, token)
    argv = [
        "-q",
        "--rootdir",
        str(root),
        "-c",
        str(root / "pyproject.toml"),
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-m",
        "not browser",
        "--basetemp",
        str(Path(scratch) / "pytest-tmp"),
        "--junitxml",
        outputs["junit"],
    ]
    if assignment["kind"] == "collection":
        argv.append("--collect-only")
    argv.extend(assignment["files"])
    return int(pytest.main(argv, plugins=[verification_pytest]))


if __name__ == "__main__":
    raise SystemExit(main())
