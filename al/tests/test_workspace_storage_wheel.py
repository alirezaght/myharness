"""Legacy and paired package builds keep the approved workspace wheel boundary closed."""

import zipfile
from pathlib import Path

import pytest

from al.installer import workspace_tools as T
from al.installer import workspace_wheel as W
from al.installer.registry import digest
from harness.storage import CANONICAL_MODULES

ROOT = Path(__file__).parents[2]


def inputs(*, paired=True):
    contents = {name: (ROOT / name).read_bytes() for name in ("pyproject.toml", "README.md")}
    if not paired:
        contents["pyproject.toml"] = contents["pyproject.toml"].replace(
            b'module-name = ["harness", "al.installer"]\n', b""
        )
    for path in (ROOT / "harness").rglob("*"):
        if (
            path.is_file()
            and not {"__pycache__", ".pytest_cache"} & set(path.parts)
            and path.suffix not in {".pyc", ".pyo"}
        ):
            contents[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    if paired:
        for path in (ROOT / "al/installer").glob("*.py"):
            contents[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    return contents


def test_exact_paired_required_inventory_matches_canonical_distribution():
    assert W.PAIRED_REQUIRED == {
        "al/installer/" + name for name in (*CANONICAL_MODULES, "__init__.py")
    }


@pytest.mark.parametrize("paired", [False, True])
def test_project_accepts_only_complete_supported_layouts(paired):
    assert W.project(inputs(paired=paired))["name"] == "harness"


@pytest.mark.parametrize(
    "missing", ["harness/__init__.py", "harness/cli.py", *sorted(W.PAIRED_REQUIRED)]
)
def test_paired_source_missing_required_member_refuses_before_materialization(tmp_path, missing):
    contents = inputs()
    contents.pop(missing)
    with pytest.raises(ValueError, match="implementation missing"):
        W.build_copy(contents, tmp_path / "source")
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize(
    "replacement",
    [
        '["al.installer", "harness"]',
        '["harness", "al.installer", "other"]',
        '["harness", "al.other"]',
        '["harness", "al.installer", "al.installer"]',
        '"harness"',
    ],
)
def test_backend_package_selection_cannot_expand_or_change(replacement):
    contents = inputs()
    contents["pyproject.toml"] = contents["pyproject.toml"].replace(
        b'["harness", "al.installer"]', replacement.encode()
    )
    with pytest.raises(ValueError, match="configuration"):
        W.project(contents)


@pytest.fixture(scope="module", params=[False, True], ids=["legacy", "paired"])
def public_built(request, tmp_path_factory):
    contents = inputs(paired=request.param)
    directory = tmp_path_factory.mktemp(
        "workspace-storage-" + ("paired" if request.param else "legacy")
    )
    source, metadata = W.build_copy(contents, directory / "source")
    record = T.run_tool(
        T.tool_identity("uv"),
        [
            "build",
            "--wheel",
            "--offline",
            "--no-sources",
            "--no-config",
            "--out-dir",
            str(directory / "dist"),
        ],
        cwd=source,
        env=T.environment(directory / "environment"),
        directory=directory / "tool",
    )
    artifact = W.wheel_output(directory / "dist")
    observed = W.inspect_wheel(artifact, metadata["version"], contents=contents)
    assert record["returncode"] == 0 and record["cleanup"]["leader_reaped"] is True
    assert record["cleanup"]["descendants_settled"] is None
    return request.param, contents, artifact, observed, directory


def mutated_wheel(original, target, *, remove=None, add=None, replace=None):
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(target, "w") as destination:
        for item in source.infolist():
            if item.filename != remove:
                destination.writestr(
                    item,
                    replace[1]
                    if replace and item.filename == replace[0]
                    else source.read(item.filename),
                )
        if add:
            destination.writestr(*add)
    return target


def test_actual_public_workspace_build_copy_owned_tool_and_inspection(public_built):
    paired, contents, artifact, observed, _ = public_built
    assert observed == W.inspect_wheel(artifact, "0.1.0")
    assert observed["sha256"] == digest(artifact.read_bytes())
    for name in W.PAIRED_REQUIRED:
        assert (name in observed["members"]) is paired
    assert observed == W.inspect_wheel(artifact, "0.1.0", contents=contents)


@pytest.mark.parametrize(
    "name",
    [
        "al/other.py",
        "al/__init__.py",
        "al/installer_extra/x.py",
        "al/installer/nested/x.py",
        "al/installer/data.bin",
        "outside/__init__.py",
    ],
)
def test_unrelated_al_or_outside_member_refuses(public_built, tmp_path, name):
    _, _, artifact, _, _ = public_built
    corrupt = mutated_wheel(artifact, tmp_path / artifact.name, add=(name, b"unrelated"))
    with pytest.raises(ValueError, match="unexpected"):
        W.inspect_wheel(corrupt, "0.1.0")


def test_observed_wheel_package_bytes_must_match_approved_source(public_built, tmp_path):
    _, contents, artifact, _, _ = public_built
    corrupt = mutated_wheel(
        artifact, tmp_path / artifact.name, replace=("harness/__init__.py", b"changed")
    )
    with pytest.raises(ValueError, match="approved package source"):
        W.inspect_wheel(corrupt, "0.1.0", contents=contents)


def test_missing_paired_member_never_passes_auto_or_source_bound_inspection(public_built, tmp_path):
    paired, contents, artifact, _, _ = public_built
    corrupt = mutated_wheel(
        artifact,
        tmp_path / artifact.name,
        remove="al/installer/storage_lifecycle.py" if paired else None,
        add=None
        if paired
        else ("al/installer/storage_filesystem.py", b"incomplete paired injection"),
    )
    for supplied in (None, contents):
        with pytest.raises(ValueError, match="incomplete|unexpected"):
            W.inspect_wheel(corrupt, "0.1.0", contents=supplied)


def test_extra_flat_installer_source_requires_exact_approved_membership(public_built, tmp_path):
    paired, contents, artifact, _, _ = public_built
    corrupt = mutated_wheel(
        artifact, tmp_path / artifact.name, add=("al/installer/unapproved.py", b"unapproved")
    )
    with pytest.raises(ValueError, match="approved package source|unexpected"):
        W.inspect_wheel(corrupt, "0.1.0", contents=contents)


def test_missing_build_manifest_is_actionable_before_creating_source(tmp_path):
    with pytest.raises(ValueError, match="configuration missing"):
        W.build_copy({}, tmp_path / "source")
    assert not (tmp_path / "source").exists()
