"""Black-box resource and no-database checks against built distributions."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SKILL_FILES = ("SKILL.md", "protocol.md", "codex.md", "claude.md")
MIGRATIONS = (
    "0001_tasks.sql",
    "0002_workspace.sql",
    "0003_interview.sql",
    "0004_design.sql",
    "0005_decision_changes.sql",
    "0006_spec_versions.sql",
    "0007_test_plan.sql",
    "0008_approvals.sql",
    "0009_diagram_versions.sql",
    "0010_comments.sql",
    "0011_executions.sql",
    "0012_delivery.sql",
    "0013_browser_validation.sql",
    "0014_execution_epochs.sql",
    "0015_knowledge.sql",
    "0016_knowledge_index.sql",
    "0017_workspace_proposals.sql",
    "0018_authoring.sql",
    "0019_investigation_sources.sql",
    "0020_planning_sessions.sql",
    "0021_reliability_observations.sql",
    "0022_reliability_timeline.sql",
    "0023_runtime_policy.sql",
    "0024_dependency_launch_fencing.sql",
    "0025_incident_catalog.sql",
)
MIGRATION_MANIFEST = "migration_schema_manifest.json"


def _artifact_root(artifact: Path, destination: Path, uv: str) -> Path:
    destination.mkdir()
    if artifact.suffix == ".whl":
        installed = destination / "site-packages"
        result = subprocess.run(
            [uv, "pip", "install", "--no-deps", "--target", str(installed), str(artifact)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, {"stdout": result.stdout, "stderr": result.stderr}
        return installed
    else:
        with tarfile.open(artifact) as archive:
            archive.extractall(destination, filter="data")
    candidates = [path.parent.parent for path in destination.rglob("harness/__init__.py")]
    assert len(candidates) == 1, {"artifact": str(artifact), "candidates": candidates}
    return candidates[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_built_wheel_and_sdist_expose_skills_and_session_project_without_db(tmp_path):
    uv = shutil.which("uv")
    assert uv, "The registered package-resource smoke requires the locked uv build tool."
    output = tmp_path / "distribution"
    output.mkdir()
    build = subprocess.run(
        [uv, "build", "--out-dir", str(output)],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, {"stdout": build.stdout, "stderr": build.stderr}
    artifacts = sorted(output.iterdir())
    wheel = next(path for path in artifacts if path.suffix == ".whl")
    sdist = next(path for path in artifacts if path.name.endswith(".tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        wheel_members = set(archive.namelist())
    assert f"harness/{MIGRATION_MANIFEST}" in wheel_members
    assert all(
        any(member.endswith(f"harness/skills/harness/{name}") for member in wheel_members)
        for name in SKILL_FILES
    )
    assert all(
        any(member.endswith(f"harness/migrations/{name}") for member in wheel_members)
        for name in MIGRATIONS
    )

    probe = r"""
import contextlib, hashlib, io, json, os, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
repo = Path(sys.argv[2]).resolve()
db_path = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(root))
import harness
origin = Path(harness.__file__).resolve()
assert origin.is_relative_to(root), {"root": str(root), "origin": str(origin)}
import al.installer
installer_origin = Path(al.installer.__file__).resolve()
assert installer_origin.is_relative_to(root), {
    "root": str(root), "installer_origin": str(installer_origin)
}
import harness.authoring_native as native
native.NativeAuthor.run = lambda *args, **kwargs: (_ for _ in ()).throw(
    AssertionError("skill/project read invoked a native host")
)
import harness.authoring_policy as policy
policy.availability = lambda *args, **kwargs: (_ for _ in ()).throw(
    AssertionError("skill/project read probed host availability")
)
from harness.cli import main
outputs = {}
for provider in ("codex", "claude"):
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        result = main(["skill", "show", "--provider", provider])
    assert result == 0
    outputs[provider] = stream.getvalue()
    entry = "Claude Code" if provider == "claude" else "Codex"
    assert "# Harness shared-session workflow" in outputs[provider]
    assert "## Session protocol reference" in outputs[provider]
    assert f"## {entry} entry" in outputs[provider]
stream = io.StringIO()
with contextlib.redirect_stdout(stream):
    result = main(["session", "project", "--repo", str(repo)])
assert result == 0
project = json.loads(stream.getvalue())
assert project["repository"] == str(repo)
assert project["project"]
assert not db_path.exists(), str(db_path)
print(json.dumps({"origin": str(origin), "project": project,
                  "installer_origin": str(installer_origin),
                  "providers": sorted(outputs), "database_created": False}))
"""

    sample_repo = tmp_path / "sample-project"
    sample_repo.mkdir()
    probe_cwd = tmp_path / "isolated-cwd"
    probe_cwd.mkdir()
    no_database = tmp_path / "no-database" / "must-not-be-created.sqlite"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HARNESS_DB": str(no_database),
        "HARNESS_HOME": str(tmp_path / "harness-home"),
    }
    provenance = {"artifacts": [], "probes": []}
    for artifact in (wheel, sdist):
        extracted = (
            tmp_path / f"extracted-{artifact.suffix[1:] or 'sdist'}-{len(provenance['artifacts'])}"
        )
        root = _artifact_root(artifact, extracted, uv)
        resources = root / "harness" / "skills" / "harness"
        packaged = [resources / name for name in SKILL_FILES]
        migrations = [root / "harness" / "migrations" / name for name in MIGRATIONS]
        manifest = root / "harness" / MIGRATION_MANIFEST
        assert all(path.is_file() for path in [*packaged, *migrations, manifest]), {
            "artifact": artifact.name,
            "missing": [
                str(path.relative_to(root))
                for path in [*packaged, *migrations, manifest]
                if not path.is_file()
            ],
        }
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                probe,
                str(root),
                str(sample_repo),
                str(no_database),
            ],
            cwd=probe_cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, {
            "artifact": artifact.name,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        probe_result = json.loads(result.stdout.strip().splitlines()[-1])
        assert Path(probe_result["origin"]).is_relative_to(root)
        assert Path(probe_result["installer_origin"]).is_relative_to(root)
        assert probe_result["providers"] == ["claude", "codex"]
        assert probe_result["database_created"] is False
        provenance["artifacts"].append({"name": artifact.name, "sha256": _sha256(artifact)})
        provenance["probes"].append(probe_result)
    assert not no_database.exists()
    (tmp_path / "distribution-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def test_installed_wheel_connects_from_packaged_migration_resources(tmp_path):
    """Use only the installed wheel, outside the checkout, for a disposable migration run."""
    uv = shutil.which("uv")
    assert uv, "The installed-wheel resource smoke requires the locked uv build tool."
    output = tmp_path / "distribution"
    output.mkdir()
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(output)],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, {"stdout": build.stdout, "stderr": build.stderr}
    wheel = next(output.glob("*.whl"))
    installed = tmp_path / "installed-wheel"
    result = subprocess.run(
        [uv, "pip", "install", "--no-deps", "--target", str(installed), str(wheel)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, {"stdout": result.stdout, "stderr": result.stderr}

    probe = r"""
import hashlib, importlib.resources, json, sys
from pathlib import Path

installed = Path(sys.argv[1]).resolve()
checkout = Path(sys.argv[2]).resolve()
database = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(installed))
import harness
import al.installer
from harness.db import connect

for module in (harness, al.installer):
    origin = Path(module.__file__).resolve()
    assert origin.is_relative_to(installed), {"installed": str(installed), "origin": str(origin)}
    assert not origin.is_relative_to(checkout), str(origin)

resources = importlib.resources.files("harness")
manifest_resource = resources.joinpath("migration_schema_manifest.json")
assert manifest_resource.is_file()
manifest = json.loads(manifest_resource.read_text(encoding="utf-8"))
expected = [
    "0001_tasks.sql", "0002_workspace.sql", "0003_interview.sql", "0004_design.sql",
    "0005_decision_changes.sql", "0006_spec_versions.sql", "0007_test_plan.sql",
    "0008_approvals.sql", "0009_diagram_versions.sql", "0010_comments.sql",
    "0011_executions.sql", "0012_delivery.sql", "0013_browser_validation.sql",
    "0014_execution_epochs.sql", "0015_knowledge.sql", "0016_knowledge_index.sql",
    "0017_workspace_proposals.sql", "0018_authoring.sql",
    "0019_investigation_sources.sql", "0020_planning_sessions.sql",
    "0021_reliability_observations.sql", "0022_reliability_timeline.sql",
    "0023_runtime_policy.sql", "0024_dependency_launch_fencing.sql",
    "0025_incident_catalog.sql",
]
migration_hashes = manifest["migration_hashes"]
assert sorted(migration_hashes) == expected
for name in expected:
    sql_resource = resources.joinpath("migrations", name)
    assert sql_resource.is_file(), name
    actual = hashlib.sha256(sql_resource.read_bytes()).hexdigest()
    assert migration_hashes[name] == actual, name

database.parent.mkdir(parents=True, exist_ok=True)
connection = connect(database)
try:
    rows = connection.execute(
        "SELECT version,name FROM schema_migrations ORDER BY version"
    ).fetchall()
    assert [(row["version"], row["name"]) for row in rows] == [
        (version, name) for version, name in enumerate(expected, 1)
    ]
finally:
    connection.close()
assert database.is_file() and database.resolve().is_relative_to(Path(sys.argv[4]).resolve())
print(json.dumps({
    "harness": str(Path(harness.__file__).resolve()),
    "installer": str(Path(al.installer.__file__).resolve()),
    "database": str(database),
    "migration_count": len(rows),
    "manifest": str(manifest_resource),
}))
"""
    isolated_cwd = tmp_path / "isolated-cwd"
    isolated_cwd.mkdir()
    database_root = tmp_path / "owned-database"
    database = database_root / "state.sqlite"
    environment = {"PATH": os.environ.get("PATH", "")}
    probe_result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            probe,
            str(installed),
            str(REPOSITORY),
            str(database),
            str(database_root),
        ],
        cwd=isolated_cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe_result.returncode == 0, {
        "stdout": probe_result.stdout,
        "stderr": probe_result.stderr,
    }
    observed = json.loads(probe_result.stdout.strip().splitlines()[-1])
    assert observed["migration_count"] == len(MIGRATIONS)
    assert Path(observed["harness"]).is_relative_to(installed.resolve())
    assert Path(observed["installer"]).is_relative_to(installed.resolve())
    assert Path(observed["manifest"]).is_relative_to(installed.resolve())
    assert Path(observed["database"]).is_relative_to(database_root.resolve())
