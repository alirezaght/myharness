"""Real guarded old-issuer authority approves a disposable full installer source."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

from al.installer.owned_process import execute

from al.installer.bootstrap import bootstrap_cli
from al.installer.registry import canonical, digest, read_json
from al.tests.old_issuer_workflow import ROOT, Workflow


class WorkspaceJourney(Workflow):
    def __init__(self, directory, platform):
        super().__init__(directory, platform)
        # Source approval is queried by the actual candidate copied into this repository.
        self.boot = bootstrap_cli(
            self.repo / "al",
            directory / "workspace-bin/alctl",
            directory / "workspace-validator-state",
        )
        self.global_home = directory / "global-home"
        self.global_home.mkdir()
        self.global_state = directory / "global-state"

    def create_project(self):
        super().create_project()
        ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache")
        # Keep the complete installer/runtime source. Embedded example repositories are
        # test data, not applications of this disposable Python project: their package
        # manifests would otherwise declare unrelated contract-generation obligations.
        shutil.copytree(ROOT / "al", self.repo / "al", ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc", ".ruff_cache", "fixtures"))
        shutil.copytree(ROOT / "harness", self.repo / "harness", ignore=ignore)
        for name in (".claude/commands/harness.md", "scripts/install_global.py"):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
        self.put(
            "pyproject.toml",
            """[project]
name = "harness"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []
[project.scripts]
harness = "harness.cli:main"
[build-system]
requires = ["uv_build>=0.10,<0.11"]
build-backend = "uv_build"
[dependency-groups]
dev = ["pytest>=8"]
[tool.uv]
package = false
environments = ["sys_platform != 'win32'"]
find-links = ["fixture-wheels"]
[tool.uv.build-backend]
module-root = ""
[tool.ruff]
target-version = "py313"
line-length = 100
extend-exclude = ["al"]
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
[tool.pytest.ini_options]
testpaths = ["tests"]
""",
        )
        quality = (self.repo / ".github/workflows/quality.yml").read_text()
        self.put(
            ".github/workflows/quality.yml",
            quality.replace("ruff check .", "ruff check harness").replace(
                "ruff format --check .", "ruff format --check harness"
            ),
        )
        # Retain the actual resolved tool policy as diagnostic input, not a gate receipt.
        self.command([self.directory / "tools/ruff", "check", "--show-settings", "harness"])
        self.command(["uv", "lock", "--offline"])
        self.command(["git", "add", "."])
        self.command(["git", "add", "-f", ".claude/commands/harness.md"])
        self.command(["git", "commit", "-qm", "disposable complete workspace inputs"])

    def workspace(self, command, file):
        argv = list(
            map(
                str,
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    self.repo / "scripts/install_global.py",
                    command,
                    "--state-root",
                    self.global_state,
                    "--file",
                    file,
                    "--sha256",
                    digest(Path(file).read_bytes()),
                    *(["--source", self.repo] if command == "plan" else []),
                ],
            )
        )
        directory = self.artifacts / ("workspace-" + command)
        directory.mkdir()
        log = directory / "output.log"
        # This full-source diagnostic is not a <=360s suite result. Ownership,
        # rather than subprocess.run's parent-only timeout, covers descendants.
        with log.open("wb") as output:
            owner = execute(
                argv,
                cwd=self.repo,
                env=self.env,
                output=output,
                directory=directory / "ownership",
                assignment=digest(canonical(argv)),
                timeout=600,
            )
        journal = read_json(directory / "ownership/process-journal.json")
        assert journal["status"] == "FINISHED" and journal["cleanup"]["complete"]
        result = subprocess.CompletedProcess(argv, owner.process.returncode, log.read_text(), "")
        self.transcript.append(
            {
                "argv": argv,
                "exit": result.returncode,
                "stdout": str(log),
                "stdout_sha256": digest(log.read_bytes()),
                "ownership_journal": str(directory / "ownership/process-journal.json"),
            }
        )
        (self.artifacts / "transcript.json").write_text(json.dumps(self.transcript, indent=2))
        assert result.returncode == 0, (command, result.stdout[-2500:])
        return result

    def plan(self):
        request = self.artifacts / "workspace-request.json"
        request.write_text(
            json.dumps(
                {
                    "home": str(self.global_home),
                    "session": self.session,
                    "run": self.run,
                    "platform": self.platform,
                }
            )
        )
        result = self.workspace("plan", request)
        value = json.loads(result.stdout)
        path = self.artifacts / "workspace-plan.json"
        path.write_text(result.stdout)
        return value, path
