"""AL's code facts as seen from the knowledge graph (#34).

Import and endpoint edges, and who consumes a path, module or endpoint, have one analyser: AL's
(`al_blob_analysis` per file, `al_code_facts` for the repository layout). Harness never imports
AL code (its wheel ships only `al.installer`); it asks the protected launcher instead:

    alctl run --repository R --platform P [--session S] -- facts consumers TARGET [--commit C]

That is the plain `run` form the resident AL service (#27) serves from a warm process, so a
query while an AL run is active reads the service's in-memory working-copy tree and fact-store
pack headers. Without the service the launcher runs the same command in process.

Facts are available only where an AL run can be named: an execution's own run, an explicit
session, or the native Claude conversation's run. Otherwise (`resolve` returns None), or when
the launcher fails, the knowledge graph keeps its text and symbol index and reports
`al-facts-unavailable` instead of import edges.
"""

import json
import os
import subprocess

TIMEOUT = 120
MAX_OUTPUT = 32 * 1024 * 1024


class FactsUnavailable(Exception):
    """AL could not answer (no launcher, no run, refusal or malformed output)."""


class ALFacts:
    """One AL run through which the knowledge graph reads code facts."""

    def __init__(self, repository, platform, session=None, *, launcher=None, runner=None):
        self.repository = str(repository)
        self.platform = platform
        self.session = session
        self.launcher = launcher
        self.runner = runner or subprocess.run

    def argv(self, *arguments):
        launcher = self.launcher
        if launcher is None:
            from harness.execution_hosts import managed_launcher

            launcher = managed_launcher()
        # Exactly the options the service client recognises (no --existing-run): served warm.
        return [
            str(launcher),
            "run",
            "--repository",
            self.repository,
            "--platform",
            self.platform,
            *(["--session", self.session] if self.session else []),
            "--",
            "facts",
            *arguments,
        ]

    def query(self, *arguments):
        """The parsed JSON document of `facts ARGUMENTS`; FactsUnavailable on any failure."""
        try:
            argv = self.argv(*arguments)
            result = self.runner(
                argv,
                cwd=self.repository,
                capture_output=True,
                timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise FactsUnavailable(str(error)[:500]) from None
        if result.returncode:
            detail = (result.stderr or b"")[-500:].decode("utf-8", "replace").strip()
            raise FactsUnavailable(detail or f"facts exited {result.returncode}")
        if len(result.stdout) > MAX_OUTPUT:
            raise FactsUnavailable("facts output too large")
        try:
            value = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise FactsUnavailable("facts output is not JSON") from None
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise FactsUnavailable("unsupported facts schema")
        return value


def resolve(repository, *, platform=None, session=None, runner=None, launcher=None):
    """The AL run to query: an explicit session, else the native Claude conversation's run
    (the launcher resolves it), else None (the knowledge graph then has no import edges)."""
    if session:
        return ALFacts(repository, platform or "claude", session, runner=runner, launcher=launcher)
    if (platform in (None, "claude")) and (
        os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    ):
        return ALFacts(repository, "claude", None, runner=runner, launcher=launcher)
    return None
