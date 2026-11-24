"""Whole-process native boundary. Failed executable probes deny dispatch."""

import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from harness.execution_contract import digest


@dataclass(frozen=True)
class IsolationProfile:
    policy: str
    repository: str
    writable: tuple[str, ...]
    protected: tuple[str, ...]
    proxy_port: int
    native_home: str
    temporary: str


def _literal(path):
    return json.dumps(str(Path(path).resolve()))


def prepare_isolation(execution, envelope, initialization, provider):
    repository = Path(envelope["repository"]).resolve(strict=True)
    sealed = Path(execution["handoff_path"]).parent.resolve(strict=True)
    # Trusted material must be outside the repository write capability.
    if sealed.is_relative_to(repository):
        raise ValueError("containment requires handoff/authority storage outside repository")
    boundary = sealed / "containment"
    boundary.mkdir(mode=0o700, exist_ok=True)
    native_home, temporary = boundary / "native", boundary / "tmp"
    native_home.mkdir(mode=0o700, exist_ok=True)
    temporary.mkdir(mode=0o700, exist_ok=True)
    runtime = Path(initialization["source"]).parent
    session = execution["al_session_id"]
    artifacts = runtime / session
    artifacts.mkdir(mode=0o700, exist_ok=True)
    state_lock = runtime / ("." + session + ".lock")
    state_lock.touch(mode=0o600, exist_ok=True)
    database = Path(envelope["database"]).resolve(strict=True)
    protected = [
        str(database),
        str(database) + "-wal",
        str(database) + "-shm",
        str(sealed),
        str(repository / ".codex"),
        str(repository / ".claude"),
        str(repository / ".agents"),
    ]
    writable = [str(native_home), str(temporary), str(artifacts)]
    profile = IsolationProfile(
        str(boundary / "native.sb"),
        str(repository),
        tuple(writable),
        tuple(protected),
        provider["port"],
        str(native_home),
        str(temporary),
    )
    if platform.system() != "Darwin" or not shutil.which("sandbox-exec"):
        raise ValueError(
            "native containment capability unavailable: macOS sandbox-exec required; "
            "Linux isolated provider transport is not available"
        )
    excluded = " ".join(f"(require-not (subpath {_literal(p)}))" for p in protected)
    policy = [
        "(version 1)",
        "(deny default)",
        "(allow file-read*)",
        "(allow process-exec process-fork)",
        "(allow signal (target self))",
        # macOS can admit procargs through either sysctl or process-info rules.
        "(allow sysctl-read)",
        '(deny sysctl-read (sysctl-name-prefix "kern.procargs"))',
        "(deny process-info*)",
        "(allow process-info* (target self))",
        f"(allow file-write* (require-all (subpath {_literal(repository)}) {excluded}))",
    ]
    for path in writable:
        if path == str(native_home):
            native_protected = [
                native_home / "skills",
                native_home / "agents",
                native_home / "config.toml",
                native_home / "settings.json",
            ]
            exclusions = " ".join(
                f"(require-not (subpath {_literal(p)}))" for p in native_protected
            )
            policy.append(
                f"(allow file-write* (require-all (subpath {_literal(path)}) {exclusions}))"
            )
        else:
            policy.append(f"(allow file-write* (subpath {_literal(path)}))")
    for path in (initialization["source"], str(state_lock), initialization["coordination_lock"]):
        policy.append(f"(allow file-write* (literal {_literal(path)}))")
    # AL atomic saves create a randomized filename beside the exact session JSON.
    import re

    pattern = "^" + re.escape(str(runtime / ("." + session + "."))) + r"[^/]+\.tmp$"
    policy.append(f"(allow file-write* (regex {json.dumps(pattern)}))")
    policy.extend(
        [
            '(allow file-write* (literal "/dev/null"))',
            f'(allow network-outbound (remote ip "localhost:{provider["port"]}"))',
        ]
    )
    for path in (
        Path.home() / ".ssh",
        Path.home() / ".aws",
        Path.home() / ".config/gcloud",
        Path.home() / "Library/Keychains",
        Path.home() / ".codex/auth.json",
        Path.home() / ".claude/.credentials.json",
        Path.home() / ".git-credentials",
        Path.home() / ".netrc",
        Path.home() / ".docker/config.json",
        Path.home() / ".config/gh",
        Path.home() / ".azure",
        Path.home() / ".kube",
    ):
        policy.append(f"(deny file-read* (subpath {_literal(path)}))")
    policy.append(
        f"(deny file-read* (subpath {_literal(sealed / 'operations/private-report-spools')}))"
    )
    raw = "\n".join(policy) + "\n"
    destination = Path(profile.policy)
    if destination.exists() and destination.read_text() != raw:
        # Proxy port is execution-attempt specific; the supervisor may replace protected policy.
        destination.write_text(raw)
    elif not destination.exists():
        destination.write_text(raw)
    destination.chmod(0o600)
    return profile


def contained_argv(profile, argv):
    if platform.system() != "Darwin":
        raise ValueError("unsupported native containment platform")
    return ["/usr/bin/sandbox-exec", "-f", profile.policy, *argv]


def probe_isolation(profile):
    """Actual child attempts both an admitted edit and protected/outbound effects."""
    allowed = Path(profile.repository) / (".harness-probe-" + os.urandom(8).hex())
    protected = Path(profile.policy)
    before = protected.read_bytes()
    code = """import os,socket,sys,subprocess
from pathlib import Path
allowed,protected=sys.argv[1:3]
Path(allowed).write_text('probe')
Path(allowed).unlink()
try:
    with open(protected,'ab') as stream:stream.write(b'ESCAPE')
except PermissionError:pass
else:raise SystemExit('protected write permitted')
for family,address in [(socket.AF_INET,('127.0.0.1',9)),(socket.AF_UNIX,sys.argv[3])]:
    s=socket.socket(family,socket.SOCK_STREAM);s.settimeout(.2)
    try:s.connect(address)
    except PermissionError:pass
    except OSError as e:
        if e.errno not in (1,13):raise SystemExit('network was not denied by policy: '+str(e))
    else:raise SystemExit('unauthorized network allowed')
    finally:s.close()
child_code="from pathlib import Path;Path(__import__('sys').argv[1]).write_text('escape')"
child=subprocess.run([sys.executable,'-c',child_code,protected],capture_output=True)
if child.returncode==0:raise SystemExit('descendant escaped')
"""
    # Probe an existing endpoint: ENOENT from a missing Docker socket proves
    # nothing about containment and varies between developer and CI hosts.
    with tempfile.TemporaryDirectory(prefix="hprobe-", dir="/tmp") as directory:
        endpoint = str(Path(directory) / "s")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            listener.listen()
            result = subprocess.run(
                contained_argv(
                    profile,
                    [sys.executable, "-I", "-c", code, str(allowed), str(protected), endpoint],
                ),
                cwd=profile.repository,
                capture_output=True,
                timeout=10,
            )
    if result.returncode or protected.read_bytes() != before:
        raise ValueError(
            "native containment capability probe failed: "
            + result.stderr.decode(errors="replace")[:1000]
        )
    return {
        "status": "verified",
        "policy_digest": digest(before),
        "checks": [
            "repository_write",
            "protected_write_denied",
            "tcp_denied",
            "unix_denied",
            "descendant_denied",
        ],
    }


@dataclass(frozen=True)
class OperationIsolation:
    policy: str
    repository: str
    writable: tuple[str, ...]
    protected: tuple[str, ...]
    hidden: tuple[str, ...]
    temporary: str
    session_directory: str | None
    coordination_lock: str | None
    system: str
    classification: dict | None = None
    scratch_identity: tuple[int, int] = ()
    provisioning_network: bool = False
    # Short private TMPDIR for the contained process (AF_UNIX path limit); "" when none.
    process_temporary: str = ""
    # AL's incremental-build paths (#31): exactly these directories are writable and exactly
    # these Unix sockets connectable, in addition to the operation's own grants.
    al_writable: tuple[str, ...] = ()
    al_sockets: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlGrants:
    """What an AL process inside the operation sandbox may reach for its caches (#31).

    `writable`: the worktree's private snapshot directory (`<git dir>/al-snapshot`: private
    index and private snapshot objects), the repository's snapshot refs
    (`<common dir>/refs/al/snapshots`), the action cache (`<common dir>/al-action-cache`) and
    the repository's analysis fact store. `sockets`: the resident AL service's socket for this
    run (macOS only; the Linux seccomp filter keeps refusing Unix stream sockets, so AL falls
    back to its own snapshot there). `environment`: what tells AL to use them."""

    writable: tuple[str, ...] = ()
    sockets: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


AL_SNAPSHOT_DIRECTORY = "al-snapshot"
AL_SNAPSHOT_REFS = ("refs", "al", "snapshots")
AL_ACTION_CACHE = "al-action-cache"


def _git_locations(repository):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "rev-parse",
            "--path-format=absolute",
            "--git-dir",
            "--git-common-dir",
        ],
        cwd=str(repository),
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode:
        raise ValueError("AL cache grants need a Git repository")
    git_dir, common = (Path(line) for line in result.stdout.decode().splitlines()[:2])
    return git_dir.resolve(strict=True), common.resolve(strict=True)


def _private_directory(path, *, base, mode=0o700):
    """Create `path` below `base` (both real, no symlink on the way) and return it."""
    current = base
    for part in path.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("AL cache grant path is a symlink: " + str(current))
        if not current.exists():
            current.mkdir(mode=mode)
        if not current.is_dir() or current.stat().st_uid != os.getuid():
            raise ValueError("AL cache grant path is not an owned directory: " + str(current))
    return str(current)


def analysis_store(common):
    """AL's analysis fact store of a repository (al_analysis_index.index_root default)."""
    import hashlib

    root = Path.home() / ".local" / "state" / "al-distribution" / "analysis-index"
    return root, root / hashlib.sha256(str(common).encode()).hexdigest()[:32]


def al_grants(repository, *, service_directory=None, system=None):
    """The exact AL cache grants of one repository (see AlGrants)."""
    git_dir, common = _git_locations(Path(repository).resolve(strict=True))
    writable = [
        _private_directory(git_dir / AL_SNAPSHOT_DIRECTORY, base=git_dir),
        _private_directory(common.joinpath(*AL_SNAPSHOT_REFS), base=common, mode=0o755),
        _private_directory(common / AL_ACTION_CACHE, base=common),
    ]
    root, store = analysis_store(common)
    try:
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        writable.append(_private_directory(store, base=root.parent.resolve(strict=True)))
    except (OSError, ValueError):
        pass  # no fact store: AL computes its facts without one
    environment = [("AL_SNAPSHOT_OBJECTS", "private")]
    sockets = []
    if service_directory is not None and (system or platform.system()) == "Darwin":
        directory = Path(service_directory).resolve(strict=True)
        endpoint = directory / "s"
        info = endpoint.lstat()
        if (
            stat.S_ISSOCK(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(directory.stat().st_mode) == 0o700
        ):
            sockets.append(str(endpoint))
            environment.append(("AL_SERVICE_SNAPSHOT", str(directory)))
    return AlGrants(tuple(writable), tuple(sockets), tuple(environment))


# Unix-domain socket paths are limited to 104 bytes (macOS) / 108 (Linux). Python
# multiprocessing, black, xdist and similar bind sockets under TMPDIR, and operation
# scratch lives deep under the execution directory. Contained processes therefore get a
# fresh private directory directly under /tmp as TMPDIR; the sandbox may write to exactly
# that directory and nothing else in /tmp.
PROCESS_TEMPORARY_ROOT = Path("/tmp")
PROCESS_TEMPORARY_PREFIX = "h-"


def _private_process_temporary(path):
    path = Path(path)
    if (
        not path.is_absolute()
        or path.parent != PROCESS_TEMPORARY_ROOT
        or not path.name.startswith(PROCESS_TEMPORARY_PREFIX)
        or ".." in path.parts
    ):
        raise ValueError("process temporary directory must be a private /tmp/h-* directory")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("process temporary directory is not private")
    return str(path)


def create_process_temporary():
    """A fresh 0700 user-owned /tmp/h-<random> directory (mkdtemp: O_EXCL, unpredictable)."""
    path = Path(tempfile.mkdtemp(prefix=PROCESS_TEMPORARY_PREFIX, dir=PROCESS_TEMPORARY_ROOT))
    try:
        return _private_process_temporary(path)
    except (OSError, ValueError):
        shutil.rmtree(path, ignore_errors=True)
        raise


def release_process_temporary(path):
    """Remove a process temporary directory; rmtree never follows symlinks out of it."""
    if not path:
        return
    path = Path(path)
    if path.parent == PROCESS_TEMPORARY_ROOT and path.name.startswith(PROCESS_TEMPORARY_PREFIX):
        shutil.rmtree(path, ignore_errors=True)


def process_temporary_environment(profile, environment):
    """Point TMPDIR/TMP/TEMP at the profile's short directory, when it has one."""
    if profile.process_temporary:
        environment.update(
            TMPDIR=profile.process_temporary,
            TMP=profile.process_temporary,
            TEMP=profile.process_temporary,
        )
    return environment


def prepare_operation_isolation(
    repository,
    *,
    writable,
    protected,
    hidden,
    directory,
    session_directory=None,
    runtime_binding=None,
    coordination_lock=None,
    classification=None,
    allow_classified_read=False,
    process_temporary=False,
    al_grants=None,
):
    """Scope an operation tree, leaving native authentication infrastructure outside.

    `al_grants` (AlGrants) admits AL's cache paths and service socket, and nothing else.

    `process_temporary` True creates a short private TMPDIR for the contained process;
    a path adopts one its trusted launcher created (and will release); False adds none."""
    repository = Path(repository).resolve(strict=True)
    from harness.template_classification import validate_reference

    classified = validate_reference(classification, root=repository)
    directory = Path(directory).absolute()
    if (
        ".." in directory.parts
        or any(p.is_symlink() for p in (directory, *directory.parents))
        or directory.is_relative_to(repository)
    ):
        raise ValueError("operation scratch must be fresh, unaliased and outside repository writes")
    collisions = [
        Path(p).absolute()
        for p in [*protected, *classified]
        if Path(p).absolute() == directory or Path(p).absolute().is_relative_to(directory)
    ]
    if collisions or any(
        directory == Path(p).absolute() for p in (session_directory, coordination_lock) if p
    ):
        raise ValueError("operation scratch collides with protected authority")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise ValueError("operation scratch ownership is invalid")
    temporary = directory / "tmp"
    temporary.mkdir(mode=0o700)
    scratch_info = temporary.stat()
    paths = []
    for item in writable:
        path = Path(item)
        if not path.is_absolute():
            path = repository / path
        if path.is_symlink() or not path.resolve().is_relative_to(repository):
            raise ValueError("operation write scope escapes repository")
        for parent in path.parents:
            if parent == repository:
                break
            if parent.is_symlink():
                raise ValueError("operation write scope has symlink ancestor")
        paths.append(str(path.resolve()))
    # A preexisting hardlink could expose a protected inode through a repository alias.
    # New hardlinks from masked/denied roots are independently denied by the OS boundary.
    for path in repository.rglob("*"):
        if ".git" in path.relative_to(repository).parts:
            continue
        if path.is_file() and not path.is_symlink() and path.stat().st_nlink != 1:
            raise ValueError("repository hardlink aliases require reconciliation before operations")
    fixed_protected = [repository / name for name in (".git", ".codex", ".claude", ".agents")]
    protected = [str(Path(p).resolve()) for p in [*protected, *fixed_protected, *classified]]
    hidden = [str(Path(p).resolve()) for p in hidden]
    hidden.extend(
        str(p.resolve())
        for p in repository.rglob("*")
        if (
            p.name.startswith(".env")
            or p.name
            in {".netrc", ".git-credentials", ".mcp.json", ".npmrc", ".pypirc", ".ssh", ".aws"}
        )
        and not p.is_symlink()
    )
    if allow_classified_read:
        hidden = [p for p in hidden if p not in classified]
    home = Path.home()
    hidden.extend(
        str(p.resolve())
        for p in (
            home / ".codex",
            home / ".claude",
            home / ".ssh",
            home / ".aws",
            home / ".config/gcloud",
            home / ".config/gh",
            home / ".docker/config.json",
            home / ".azure",
            home / ".kube",
            home / ".git-credentials",
            home / ".netrc",
            home / "Library/Keychains",
        )
    )
    session = None
    if session_directory is not None:
        session_path = Path(session_directory).resolve(strict=True)
        if runtime_binding is not None:
            from harness.execution_runtime_layout import durable_runtime

            if durable_runtime(runtime_binding, str(repository)) != session_path:
                raise ValueError("operation durable runtime differs from protected binding")
            runtime = session_path.parent.parent
        else:
            runtime = repository / session_path.parts[len(repository.parts)] / "al-runtime"
        if runtime_binding is None and (
            session_path.parent.parent != runtime or session_path.parent.name != "sessions"
        ):
            raise ValueError("legacy flat runtime requires migration before operation enrollment")
        session = str(session_path)
    if process_temporary is True:
        process_temporary = create_process_temporary()
    elif process_temporary:
        process_temporary = _private_process_temporary(process_temporary)
    else:
        process_temporary = ""
    profile = OperationIsolation(
        str(directory / "operation.sb"),
        str(repository),
        tuple(paths),
        tuple(protected),
        tuple(hidden),
        str(temporary),
        session,
        coordination_lock,
        platform.system(),
        classification,
        (scratch_info.st_dev, scratch_info.st_ino),
        process_temporary=process_temporary,
        al_writable=tuple(al_grants.writable) if al_grants else (),
        al_sockets=tuple(al_grants.sockets) if al_grants else (),
    )
    try:
        _write_operation_policy(profile, paths, protected, hidden, temporary, session)
    except BaseException:
        release_process_temporary(process_temporary)
        raise
    return profile


def _write_operation_policy(profile, paths, protected, hidden, temporary, session):
    coordination_lock = profile.coordination_lock
    if profile.system == "Darwin":
        if not shutil.which("sandbox-exec"):
            raise ValueError("operation containment unavailable: sandbox-exec")
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow file-read*)",
            "(allow process-exec process-fork)",
            "(allow signal (target self))",
            # Close both admission paths; self-inspection remains available.
            "(allow sysctl-read)",
            '(deny sysctl-read (sysctl-name-prefix "kern.procargs"))',
            "(deny process-info*)",
            "(allow process-info* (target self))",
            '(allow file-write* (literal "/dev/null"))',
        ]
        for path in paths:
            exclusions = " ".join(f"(require-not (subpath {_literal(p)}))" for p in protected)
            rules.append(
                f"(allow file-write* (require-all (subpath {_literal(path)}) {exclusions}))"
            )
        descendants = [p for p in protected if Path(p).is_relative_to(temporary)]
        exclusions = " ".join(f"(require-not (subpath {_literal(p)}))" for p in descendants)
        rules.append(
            f"(allow file-write* (require-all (subpath {_literal(temporary)}) {exclusions}))"
        )
        if session:
            rules.append(f"(allow file-write* (subpath {_literal(session)}))")
        if profile.process_temporary:
            # Exactly this directory; the rest of /tmp stays unwritable. Seatbelt checks
            # Unix-socket bind/connect as network operations, so local IPC sockets
            # (multiprocessing managers) are admitted only inside this private directory.
            private = _literal(profile.process_temporary)
            rules.extend(
                [
                    f"(allow file-write* (subpath {private}))",
                    f"(allow network-bind (local unix-socket (subpath {private})))",
                    f"(allow network-outbound (remote unix-socket (subpath {private})))",
                ]
            )
        if coordination_lock:
            rules.append(f"(allow file-write* (literal {_literal(coordination_lock)}))")
        for path in profile.al_writable:
            rules.append(f"(allow file-write* (subpath {_literal(path)}))")
        for path in profile.al_sockets:
            rules.append(
                f"(allow network-outbound (remote unix-socket (literal {_literal(path)})))"
            )
        for path in [*hidden, profile.policy]:
            rules.append(f"(deny file-read* (subpath {_literal(path)}))")
        Path(profile.policy).write_text("\n".join(rules) + "\n")
        Path(profile.policy).chmod(0o600)
    elif profile.system == "Linux":
        if not shutil.which("bwrap"):
            raise ValueError("operation containment unavailable: bubblewrap")
    else:
        raise ValueError("operation containment unavailable: unsupported operating system")


def provisioning_profile(profile):
    """Only the closed installer broker uses this fresh profile, never run_command."""
    from dataclasses import replace

    if profile.system == "Darwin":
        path = Path(profile.policy)
        # Dependency installers get network/DNS access; their existing source,
        # secret, authority and write boundaries remain exactly the same.
        with path.open("a") as stream:
            stream.write("(allow network-outbound)\n(allow mach-lookup)\n")
    return replace(profile, provisioning_network=True)


def _operation_cwd(profile, cwd):
    path = Path(cwd).absolute()
    repository = Path(profile.repository)
    if (
        ".." in path.parts
        or not path.is_relative_to(repository)
        or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(repository))
        or not path.is_dir()
    ):
        raise ValueError("operation cwd must be an existing unaliased repository directory")
    return str(path.resolve(strict=True))


def operation_argv(profile, argv, *, cwd=None):
    directory = profile.repository if cwd is None else _operation_cwd(profile, cwd)
    if profile.system == "Darwin":
        return ["/usr/bin/sandbox-exec", "-f", profile.policy, *argv]
    command = [
        "bwrap",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-pid",
        "--unshare-net",
        "--new-session",
        "--die-with-parent",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]
    if profile.provisioning_network:
        command.remove("--unshare-net")
    for path in profile.writable:
        if not Path(path).exists():
            raise ValueError("Linux operation write target must exist before containment")
        command.extend(["--bind", path, path])
    for path in profile.protected:
        if Path(path).exists():
            command.extend(["--ro-bind", path, path])
    for path in [*profile.hidden, str(Path(profile.temporary).parent), "/run"]:
        if Path(path).is_dir():
            command.extend(["--tmpfs", path])
        elif Path(path).exists():
            command.extend(["--ro-bind", "/dev/null", path])
    if profile.coordination_lock:
        command.extend(["--bind", profile.coordination_lock, profile.coordination_lock])
    if profile.session_directory:
        command.extend(["--bind", profile.session_directory, profile.session_directory])
    for path in profile.al_writable:
        # After the read-only .git binding, so exactly these subtrees become writable.
        if not Path(path).is_dir():
            raise ValueError("Linux AL cache grant must exist before containment")
        command.extend(["--bind", path, path])
    command.extend(["--bind", profile.temporary, profile.temporary])
    if profile.process_temporary:
        # Exactly this directory; the rest of /tmp stays read-only under --ro-bind /.
        _private_process_temporary(profile.process_temporary)
        command.extend(["--bind", profile.process_temporary, profile.process_temporary])
    for path in profile.protected:
        if Path(path).is_relative_to(profile.temporary):
            if not Path(path).exists():
                raise ValueError(
                    "Linux scratch protected descendant is absent; refusing write grant"
                )
            command.extend(["--ro-bind", path, path])
    command.extend(["--chdir", directory, "--", *argv])
    return command


@contextmanager
def operation_command(profile, argv, *, cwd=None):
    """Keep the Linux syscall filter descriptor alive until spawn or exec consumes it."""
    if profile.system != "Linux":
        yield operation_argv(profile, argv, cwd=cwd), ()
        return
    import struct

    machine = platform.machine()
    numbers = {"x86_64": (0xC000003E, 41, 53), "aarch64": (0xC00000B7, 198, 199)}
    if machine not in numbers:
        raise ValueError("unsupported operation seccomp architecture")
    arch, socket_call, socketpair_call = numbers[machine]
    instructions = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, arch),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
        (0x15, 2, 0, socket_call),
        (0x15, 5, 0, socketpair_call),
        (0x06, 0, 0, 0x7FFF0000),
        (0x20, 0, 0, 16),
        (0x15, 0, 1, 1),
        (0x06, 0, 0, 0x00050001),
        (0x06, 0, 0, 0x7FFF0000),
        (0x20, 0, 0, 16),
        (0x15, 1, 0, 1),
        (0x06, 0, 0, 0x00050001),
        (0x20, 0, 0, 24),
        (0x54, 0, 0, 15),
        (0x15, 1, 0, 1),
        (0x06, 0, 0, 0x00050001),
        (0x20, 0, 0, 32),
        (0x15, 1, 0, 0),
        (0x06, 0, 0, 0x00050001),
        (0x06, 0, 0, 0x7FFF0000),
    ]
    path = Path(profile.temporary).parent / "operation.seccomp"
    path.write_bytes(b"".join(struct.pack("HBBI", *entry) for entry in instructions))
    path.chmod(0o600)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        command = operation_argv(profile, argv, cwd=cwd)
        command[1:1] = ["--seccomp", str(descriptor)]
        yield command, (descriptor,)
    finally:
        os.close(descriptor)


def _validate_scratch(profile):
    path = Path(profile.temporary)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("operation scratch alias refused")
    info = path.stat()
    if (
        (profile.scratch_identity and (info.st_dev, info.st_ino) != profile.scratch_identity)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("operation scratch identity or ownership changed")


def operation_popen(profile, argv, **kwargs):
    _validate_scratch(profile)
    cwd = kwargs.get("cwd")
    if cwd is not None:
        kwargs["cwd"] = _operation_cwd(profile, cwd)
    with operation_command(profile, argv, cwd=cwd) as (command, descriptors):
        extra = kwargs.pop("pass_fds", ())
        return subprocess.Popen(command, pass_fds=(*descriptors, *extra), **kwargs)


def operation_exec(profile, argv, environment):
    _validate_scratch(profile)
    with operation_command(profile, argv, cwd=os.getcwd()) as (command, descriptors):
        for descriptor in descriptors:
            os.set_inheritable(descriptor, True)
        os.execvpe(command[0], command, environment)


def describe_prepared_start(context, resources, notification):
    """Data only. The managed issuer independently reconstructs and validates every grant."""
    from harness.template_classification import validate_prepared_classification

    root = Path(context["repository"])
    classified = validate_prepared_classification(resources["classification_ref"], context)
    hidden = set()
    names = {
        ".netrc",
        ".git-credentials",
        ".mcp.json",
        ".npmrc",
        ".pypirc",
        ".ssh",
        ".aws",
        "auth.json",
        ".credentials.json",
    }
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        hidden.update(
            str(Path(directory) / name)
            for name in [*dirs, *files]
            if name.startswith(".env") or name in names
        )
    home = Path.home()
    hidden.update(
        str(home / p)
        for p in (
            ".codex",
            ".claude",
            ".ssh",
            ".aws",
            ".config/gcloud",
            ".config/gh",
            ".docker/config.json",
            ".azure",
            ".kube",
            ".netrc",
            ".git-credentials",
            "Library/Keychains",
        )
    )
    successor = context["successor"]
    runtime = Path(successor["runtime_directory"])
    return {
        "schema_version": 1,
        "repository": str(root),
        "successor_runtime": str(runtime),
        "scratch": notification["scratch"],
        "protected_paths": notification["protected_paths"],
        "hidden_paths": sorted(hidden - set(classified)),
        "classified_paths": sorted(classified),
        "approved_files": sorted(
            [
                resources["handoff_ref"]["path"],
                resources["package_ref"]["path"],
                notification["lineage_path"],
                notification["requirements_path"],
            ]
        ),
    }
