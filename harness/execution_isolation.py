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


def prepare_operation_isolation(
    repository,
    *,
    writable,
    protected,
    hidden,
    directory,
    session_directory=None,
    coordination_lock=None,
    classification=None,
    allow_classified_read=False,
):
    """Scope an operation tree, leaving native authentication infrastructure outside."""
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
        runtime = repository / session_path.parts[len(repository.parts)] / "al-runtime"
        if session_path.parent.parent != runtime or session_path.parent.name != "sessions":
            raise ValueError("legacy flat runtime requires migration before operation enrollment")
        session = str(session_path)
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
    )
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
        if coordination_lock:
            rules.append(f"(allow file-write* (literal {_literal(coordination_lock)}))")
        for path in [*hidden, profile.policy]:
            rules.append(f"(deny file-read* (subpath {_literal(path)}))")
        Path(profile.policy).write_text("\n".join(rules) + "\n")
        Path(profile.policy).chmod(0o600)
    elif profile.system == "Linux":
        if not shutil.which("bwrap"):
            raise ValueError("operation containment unavailable: bubblewrap")
    else:
        raise ValueError("operation containment unavailable: unsupported operating system")
    return profile


def operation_argv(profile, argv):
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
    command.extend(["--bind", profile.temporary, profile.temporary])
    for path in profile.protected:
        if Path(path).is_relative_to(profile.temporary):
            if not Path(path).exists():
                raise ValueError(
                    "Linux scratch protected descendant is absent; refusing write grant"
                )
            command.extend(["--ro-bind", path, path])
    command.extend(["--chdir", profile.repository, "--", *argv])
    return command


@contextmanager
def operation_command(profile, argv):
    """Keep the Linux syscall filter descriptor alive until spawn or exec consumes it."""
    if profile.system != "Linux":
        yield operation_argv(profile, argv), ()
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
        command = operation_argv(profile, argv)
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
    with operation_command(profile, argv) as (command, descriptors):
        extra = kwargs.pop("pass_fds", ())
        return subprocess.Popen(command, pass_fds=(*descriptors, *extra), **kwargs)


def operation_exec(profile, argv, environment):
    _validate_scratch(profile)
    with operation_command(profile, argv) as (command, descriptors):
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
    runtime = root / ("." + successor["platform"]) / "al-runtime/sessions" / successor["session_id"]
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
