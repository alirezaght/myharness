"""Test seam for AL code facts (#34): answer `alctl run ... -- facts ...` from this checkout's
AL scripts, without an installed launcher or an AL run."""

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from harness.knowledge.al_facts import ALFacts
from harness.knowledge.al_facts import resolve as real_resolve  # noqa: F401  (before patching)

SCRIPTS = Path(__file__).resolve().parents[1] / "al" / "claude" / "skills" / "al" / "scripts"


def tree_state(root):
    """Capture fixture source, modes, and Git metadata around an operator-only call."""
    root = Path(root)
    result = []
    for path in sorted(root.rglob("*")):
        value = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(value.st_mode):
            content = ("symlink", os.readlink(path))
        elif stat.S_ISDIR(value.st_mode):
            content = ("directory",)
        elif stat.S_ISREG(value.st_mode):
            content = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            content = ("special", value.st_mode)
        identity = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        result.append((relative, stat.S_IMODE(value.st_mode), identity, content))
    return tuple(result)


def environment(index_dir):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_", "AL_"))}
    home = Path(index_dir).parent / "home"
    env["HOME"] = str(home)
    env["HARNESS_HOME"] = str(home / "harness")
    env["AL_DISTRIBUTION_STATE"] = str(home / "registry")
    env["TMPDIR"] = str(Path(index_dir).parent / "tmp")
    env["AL_ANALYSIS_INDEX_DIR"] = str(index_dir)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def owned_installed_launcher(base):
    """Bootstrap the real private launcher with owned Git and installer children."""
    from al.tests import runtime_run_helpers

    base = Path(base)
    base.mkdir(mode=0o700, parents=True)
    owners = []
    old_invoke, old_environment = runtime_run_helpers.invoke, runtime_run_helpers._environment

    def private_environment(home):
        env = old_environment(home)
        env.update(
            HARNESS_HOME=str(home / "harness"),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_0="core.hooksPath",
            GIT_CONFIG_VALUE_0=os.devnull,
            GIT_CONFIG_KEY_1="core.fsmonitor",
            GIT_CONFIG_VALUE_1="false",
            GIT_OPTIONAL_LOCKS="0",
        )
        return env

    runtime_run_helpers.invoke = lambda argv, cwd, env: owned_fixture_command(
        base / "bootstrap-commands", owners, argv, cwd=cwd, env=env
    )
    runtime_run_helpers._environment = private_environment
    try:
        runtime_run_helpers._bootstrap(base)
    finally:
        runtime_run_helpers.invoke = old_invoke
        runtime_run_helpers._environment = old_environment
    source, home, repository = base / "source/al", base / "home", base / "repo"
    return source, home, repository, home / "bin/alctl", private_environment(home), owners


def runner(index_dir, calls=None):
    def run(argv, *, cwd, capture_output, timeout, stdin):
        assert argv[1:3] == ["run", "--repository"] and "--" in argv
        if calls is not None:
            calls.append(argv)
        arguments = argv[argv.index("--") + 1 :]
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "al_state.py"), *arguments],
            cwd=cwd,
            capture_output=capture_output,
            timeout=timeout,
            stdin=stdin,
            env=environment(index_dir),
        )

    return run


def al_facts(repository, index_dir, calls=None):
    return ALFacts(
        repository, "claude", "al-test-run", launcher="alctl", runner=runner(index_dir, calls)
    )


def source_facts(repository, index_dir):
    """AL code facts answered by `al_code_facts.py` itself (faster than the full CLI)."""

    def run(argv, *, cwd, capture_output, timeout, stdin):
        arguments = argv[argv.index("--") + 1 :]
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "al_code_facts.py"), *arguments],
            cwd=cwd,
            capture_output=capture_output,
            timeout=timeout,
            stdin=stdin,
            env=environment(index_dir),
        )

    return ALFacts(repository, "claude", "al-test-run", launcher="alctl", runner=run)


def direct(repository, index_dir, *arguments):
    """AL's analyser run as its own module (not through the knowledge graph's launcher path)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "al_code_facts.py"), "facts", *arguments],
        cwd=repository,
        capture_output=True,
        timeout=120,
        env=environment(index_dir),
        check=True,
    )
    return result.stdout


def owned_launcher_runner(artifacts, owners, environment=None):
    """Run the selected protected launcher and retain its owned-process settlement."""
    from al.installer.owned_process import OwnedProcess

    artifacts = Path(artifacts)
    artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
    environment = dict(os.environ) if environment is None else dict(environment)

    def run(argv, *, cwd, capture_output, timeout, stdin):
        if not capture_output or stdin is not subprocess.DEVNULL:
            raise AssertionError("facts launcher requires captured output and closed stdin")
        directory = artifacts / str(len(owners))
        directory.mkdir(mode=0o700)
        owner = OwnedProcess(directory / "owner", "knowledge-operator-facts")
        owners.append(owner)
        failure = None
        with (
            open(os.devnull, "rb") as input_,
            (directory / "stdout").open("wb") as output,
            (directory / "stderr").open("wb") as error,
        ):
            try:
                process = owner.spawn_streams(
                    argv, cwd=cwd, env=environment, stdin=input_, stdout=output, stderr=error
                )
                process.wait(timeout=timeout)
            except BaseException as exc:
                failure = exc
            finally:
                try:
                    cleanup = owner.cleanup()
                    if not cleanup["complete"]:
                        raise OSError("protected launcher cleanup uncertain: " + str(cleanup))
                    if cleanup["secondary_signals"] and failure is None:
                        failure = KeyboardInterrupt(cleanup["secondary_signals"][0])
                except BaseException as exc:
                    if failure is not None:
                        failure.add_note("protected launcher cleanup: " + str(exc))
                    else:
                        failure = exc
        if failure is not None:
            raise failure
        return SimpleNamespace(
            returncode=process.returncode,
            stdout=(directory / "stdout").read_bytes(),
            stderr=(directory / "stderr").read_bytes(),
        )

    return run


def owned_fixture_command(artifacts, owners, argv, *, cwd, env, timeout=60):
    """Own bootstrap/setup children and return the small CompletedProcess surface they use."""
    from al.installer.owned_process import OwnedProcess

    directory = Path(artifacts) / str(len(owners))
    directory.mkdir(mode=0o700, parents=True)
    owner = OwnedProcess(directory / "owner", "knowledge-fixture-setup")
    owners.append(owner)
    failure = None
    with (directory / "stdout").open("wb") as output, (directory / "stderr").open("wb") as error:
        try:
            process = owner.spawn_streams(
                list(map(str, argv)),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=error,
            )
            process.wait(timeout=timeout)
        except BaseException as exc:
            failure = exc
        finally:
            try:
                cleanup = owner.cleanup()
                if not cleanup["complete"]:
                    raise OSError("fixture setup cleanup uncertain: " + str(cleanup))
                if cleanup["secondary_signals"] and failure is None:
                    failure = KeyboardInterrupt(cleanup["secondary_signals"][0])
            except BaseException as exc:
                if failure is not None:
                    failure.add_note("fixture setup cleanup: " + str(exc))
                else:
                    failure = exc
    if failure is not None:
        raise failure
    return subprocess.CompletedProcess(
        argv,
        process.returncode,
        (directory / "stdout").read_text(errors="replace"),
        (directory / "stderr").read_text(errors="replace"),
    )


def owned_launched(tmp_path_factory, artifacts):
    """Reuse the real same-path launcher template with owned bootstrap/setup children."""
    from al.tests import runtime_run_helpers

    owners = []
    original = runtime_run_helpers.invoke

    def invoke(argv, cwd, env):
        return owned_fixture_command(artifacts, owners, argv, cwd=cwd, env=env)

    runtime_run_helpers.invoke = invoke
    try:
        value = runtime_run_helpers.launched.__wrapped__(tmp_path_factory)
    finally:
        runtime_run_helpers.invoke = original
    return value, owners


def private_git(repository, artifacts, owners, *arguments, data=b""):
    """Run fixture Git with target hooks/fsmonitor disabled and a reaped child receipt."""
    from al.installer.owned_process import OwnedProcess

    directory = Path(artifacts) / str(len(owners))
    directory.mkdir(mode=0o700, parents=True)
    owner = OwnedProcess(directory / "owner", "knowledge-git-fixture")
    owners.append(owner)
    (directory / "input").write_bytes(data)
    failure = None
    with (
        (directory / "input").open("rb") as input_,
        (directory / "stdout").open("wb") as output,
        (directory / "stderr").open("wb") as error,
    ):
        try:
            process = owner.spawn_streams(
                [
                    "/usr/bin/git",
                    "--no-optional-locks",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "gc.auto=0",
                    "-c",
                    "maintenance.auto=false",
                    *arguments,
                ],
                cwd=repository,
                env=environment(Path(artifacts) / "index"),
                stdin=input_,
                stdout=output,
                stderr=error,
            )
            process.wait(timeout=10)
        except BaseException as exc:
            failure = exc
        finally:
            try:
                cleanup = owner.cleanup()
                if not cleanup["complete"]:
                    raise OSError("fixture Git cleanup uncertain: " + str(cleanup))
            except BaseException as exc:
                if failure is not None:
                    failure.add_note("fixture Git cleanup: " + str(exc))
                else:
                    failure = exc
    if failure is not None:
        raise failure
    if process.returncode:
        raise AssertionError((directory / "stderr").read_text(errors="replace")[-2000:])
    return (directory / "stdout").read_bytes()
