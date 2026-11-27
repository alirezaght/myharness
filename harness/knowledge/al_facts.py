"""AL's code facts as seen from the knowledge graph (#34).

Import and endpoint edges, and who consumes a path, module or endpoint, have one analyser: AL's
(`al_blob_analysis` per file, `al_code_facts` for the repository layout). Harness never imports
AL code (its wheel ships only `al.installer`); it asks the protected launcher instead:

    alctl run --repository R --platform P [--session S] -- facts consumers TARGET [--commit C]

That is the plain `run` form the resident AL service (#27) serves from a warm process, so a
query while an AL run is active reads the service's in-memory working-copy tree and fact-store
pack headers. Without the service the launcher runs the same command in process.

Run-bound queries keep their explicit or native route. Public operator refresh may select the
verified standalone `alctl facts` route before execution, without creating an AL run. Failure
never changes that selection; unavailable facts retain text/symbol fallback. Advisory facts
confer no execution, writer or approval authority.
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


class OperatorFacts(ALFacts):
    """Advisory facts from the verified installer, without an implementation run."""

    def __init__(self, repository, *, launcher=None, runner=None):
        super().__init__(repository, None, launcher=launcher, runner=runner)

    def argv(self, *arguments):
        launcher = self.launcher
        if launcher is None:
            from harness.execution_hosts import managed_launcher

            launcher = managed_launcher()
        return [str(launcher), "facts", "--repository", self.repository, *arguments]


def resolve(
    repository, *, platform=None, session=None, runner=None, launcher=None, allow_operator=False
):
    """Select once: explicit run, native conversation, then trusted operator opt-in.

    A selected client's refusal never changes its route or grants another authority.
    """
    if platform is not None and platform not in {"codex", "claude"}:
        raise ValueError("invalid AL facts platform")
    if session is not None:
        if (
            not isinstance(session, str)
            or not session.strip()
            or len(session) > 256
            or any(ord(c) < 32 for c in session)
        ):
            raise ValueError("invalid explicit AL facts session")
        return ALFacts(repository, platform or "claude", session, runner=runner, launcher=launcher)
    native = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if native:
        if platform not in (None, "claude"):
            raise ValueError("explicit platform differs from native facts route; supply a session")
        return ALFacts(repository, "claude", None, runner=runner, launcher=launcher)
    if platform is not None:
        raise ValueError("explicit AL facts platform requires a session")
    return OperatorFacts(repository, runner=runner, launcher=launcher) if allow_operator else None
