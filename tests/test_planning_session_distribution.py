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
MIGRATIONS = ("0019_investigation_sources.sql", "0020_planning_sessions.sql")


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
        assert all(path.is_file() for path in [*packaged, *migrations]), {
            "artifact": artifact.name,
            "missing": [
                str(path.relative_to(root))
                for path in [*packaged, *migrations]
                if not path.is_file()
            ],
        }
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe, str(root), str(sample_repo), str(no_database)],
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
        assert probe_result["providers"] == ["claude", "codex"]
        assert probe_result["database_created"] is False
        provenance["artifacts"].append({"name": artifact.name, "sha256": _sha256(artifact)})
        provenance["probes"].append(probe_result)
    assert not no_database.exists()
    (tmp_path / "distribution-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
