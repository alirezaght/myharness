"""Closed Go command observations and literal Make recipes; discovery never executes them."""

import re
import shlex
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from al_ci_yaml import Unsupported
from al_repo_go import parse_mod, relative
from al_repository_capture import CaptureDrift, CaptureMixin

MAX_BYTES = 262144
MAX_TARGETS = 256
MAX_COMMANDS = 256
MAX_DEPTH = 32
GO_ENV = [
    "PATH",
    "HOME",
    "TMPDIR",
    "GOENV",
    "GOFLAGS",
    "GOTOOLCHAIN",
    "GOWORK",
    "GOCACHE",
    "GOMODCACHE",
    "GOPATH",
    "GOROOT",
    "GOPROXY",
    "GOSUMDB",
    "GOOS",
    "GOARCH",
    "CGO_ENABLED",
]


def command_candidate(lines, *, require_all=False):
    try:
        matches = [
            shlex.split(line)[:1] in (["go"], ["gotestsum"], ["make"], ["cd"]) for line in lines
        ]
    except ValueError:
        raise Unsupported("malformed literal verification command") from None
    return bool(matches) and (all(matches) if require_all else any(matches))


def observed(tokens, cwd="."):
    args = list(tokens)
    if args[:1] == ["gotestsum"]:
        if "--" not in args:
            return []
        index = args.index("--")
        options = args[1:index]
        # Presentation-only options; output files and selectors are not inferred.
        if any(
            not re.fullmatch(r"--format=(?:standard-verbose|standard-quiet|testname|short|dots)", v)
            for v in options
        ):
            return []
        args = ["go", "test", *args[index + 1 :]]
    if len(args) < 2 or args[0] != "go" or args[1] not in {"test", "vet", "build"}:
        return []
    targets = []
    for arg in args[2:]:
        if arg in {"-race", "-v", "-trimpath"}:
            continue
        if re.fullmatch(r"-count=[1-9][0-9]{0,3}", arg) and args[1] == "test":
            continue
        if re.fullmatch(r"-timeout=[1-9][0-9]*(?:ms|s|m|h)", arg) and args[1] == "test":
            continue
        if arg.startswith("-"):
            return []
        recursive = arg.endswith("/...")
        value = arg[:-4] if recursive else arg
        if value not in {".", "./"} and not value.startswith("./"):
            return []
        if any(c in value for c in "*?[]$`") or ".." in PurePosixPath(value).parts:
            return []
        try:
            path = relative(cwd, value)
        except ValueError:
            return []
        targets.append({"path": path, "kind": "recursive" if recursive else "package"})
    if not targets:
        targets = [{"path": cwd, "kind": "package"}]
    return [
        {
            "tool": "go",
            "classes": ["go"],
            "paths": sorted({v["path"] for v in targets}),
            "go_operation": args[1],
            "go_targets": targets,
            "go_cwd": cwd,
        }
    ]


def covers(unit, record):
    # A package check never proves a whole module, nor does root ./... cross nested modules.
    return (
        record.get("go_operation") in {"test", "vet", "build"}
        and record.get("go_cwd") == unit["path"]
        and any(
            v == {"path": unit["path"], "kind": "recursive"} for v in record.get("go_targets", [])
        )
    )


class Make:
    def __init__(self, read, observe=None):
        self.read = read
        self.observe = observe
        self.visits = 0
        self.targets = {}
        self.files = []
        self.bytes = 0
        self.commands = 0

    def required_bytes(self, path):
        if self.observe and self.observe(path)["state"] != "regular":
            raise Unsupported("missing or unsafe Make input: " + path)
        raw = self.read(path)
        self.bytes += len(raw)
        if self.bytes > MAX_BYTES:
            raise Unsupported("Make source byte limit")
        return raw

    def load(self, path, active=()):
        if path in active or len(active) >= MAX_DEPTH:
            raise Unsupported("Make include cycle/depth limit")
        if path in self.files:
            return
        if len(self.files) >= 64:
            raise Unsupported("Make include count bound")
        raw = self.required_bytes(path)
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise Unsupported("Make source encoding") from None
        self.files.append(path)
        current = None
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if any(c in line for c in ("$", "`", "\\")):
                raise Unsupported("dynamic Make expansion unsupported")
            if line.startswith("\t"):
                if current is None:
                    raise Unsupported("Make recipe without target")
                command = line[1:].strip()
                if command.startswith("@"):
                    command = command[1:]
                if not command or command.startswith(("-", "+")):
                    raise Unsupported("masked/recursive Make recipe")
                self.targets[current]["commands"].append(command)
                continue
            if line.startswith("include "):
                if any(c in line for c in ('"', "'")):
                    raise Unsupported("quoted Make includes are not shell filenames")
                names = line.split()[1:]
                if any(not re.fullmatch(r"[A-Za-z0-9_./-]+", name) for name in names):
                    raise Unsupported("unsupported literal Make include")
                if not names:
                    raise Unsupported("empty Make include")
                for name in names:
                    try:
                        include = relative(".", name)
                    except ValueError:
                        raise Unsupported("unsafe Make include") from None
                    self.load(include, (*active, path))
                current = None
                continue
            if line.startswith(".PHONY:"):
                if not all(
                    not n.startswith(".") and re.fullmatch(r"[A-Za-z0-9_./-]+", n)
                    for n in line.split(":", 1)[1].split()
                ):
                    raise Unsupported("invalid Make phony declaration")
                current = None
                continue
            match = re.fullmatch(r"([A-Za-z0-9_./-]+):\s*([A-Za-z0-9_./ -]*)", line)
            if match and match[1].startswith("."):
                raise Unsupported("unsupported semantic Make special target")
            if not match or match[1] in self.targets:
                raise Unsupported("unsupported/duplicate Make definition")
            if len(self.targets) >= MAX_TARGETS:
                raise Unsupported("Make target bound")
            current = match[1]
            self.targets[current] = {"prerequisites": match[2].split(), "commands": []}

    def expand(self, target, active=(), done=None):
        self.visits += 1
        if self.visits > 4096:
            raise Unsupported("Make expansion work bound")
        if done is None:
            done = set()
        if target in active or len(active) >= MAX_DEPTH:
            raise Unsupported("Make target cycle/depth bound")
        if target in done:
            return []
        if target not in self.targets:
            # A literal prerequisite is retained and checked as a source input.
            self.required_bytes(relative(".", target))
            done.add(target)
            return []
        row = self.targets[target]
        commands = []
        for prerequisite in row["prerequisites"]:
            commands.extend(self.expand(prerequisite, (*active, target), done))
        self.commands += len(row["commands"])
        if self.commands > MAX_COMMANDS:
            raise Unsupported("Make command bound")
        commands.extend(row["commands"])
        done.add(target)
        return commands


def resolve(root, tokens, read, cwd=".", *, observe=None):
    """Return faithful direct Go checks or a finite literal Make target expansion."""
    tokens = list(tokens)
    outer_cwd = cwd
    make_command = tokens[:1] == ["make"]
    dependencies = []
    context = SimpleNamespace(root=Path(root), opaque=())
    observe = observe or (lambda path: CaptureMixin.path_observation(context, path))
    selections = []

    def sourced(path):
        raw = read(path)
        if path not in dependencies:
            dependencies.append(path)
        return raw

    if tokens[:1] == ["make"]:
        rest = tokens[1:]
        if rest[:1] == ["-C"] and len(rest) >= 3:
            try:
                cwd = relative(cwd, rest[1])
            except ValueError:
                raise Unsupported("unsafe Make working directory") from None
            rest = rest[2:]
        if (
            len(rest) != 1
            or rest[0].startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", rest[0])
        ):
            raise Unsupported("one literal Make target required")

        def make_read(path):
            return sourced(relative(cwd, path))

        def make_observe(path):
            return observe(relative(cwd, path))

        # The supported default is Makefile. Higher-priority files may not silently override it.
        higher = make_observe("GNUmakefile")
        selections.append({"path": relative(cwd, "GNUmakefile"), "state": higher["state"]})
        if higher["state"] != "absent":
            raise Unsupported("unsupported higher-priority GNUmakefile selection")
        lower = make_observe("makefile")
        primary = make_observe("Makefile")
        alias = (
            lower["state"] == primary["state"] == "regular"
            and lower["identity"] == primary["identity"]
        )
        selections.extend(
            [
                {"path": relative(cwd, "makefile"), "state": lower["state"], "same_source": alias},
                {"path": relative(cwd, "Makefile"), "state": primary["state"], "selected": True},
            ]
        )
        if lower["state"] != "absent" and not alias:
            raise Unsupported("unsupported higher-priority makefile selection")
        if primary["state"] != "regular":
            raise Unsupported("missing or unsafe Makefile")
        make = Make(make_read, make_observe)
        make.load("Makefile")
        lines = make.expand(rest[0])
        if not lines:
            raise Unsupported("Make target has no verification commands")
    else:
        lines = [shlex.join(tokens)]
    records = []
    requirements = []
    effective = []
    for line in lines:
        try:
            args = shlex.split(line)
        except ValueError:
            raise Unsupported("malformed Go recipe") from None
        command_cwd = cwd
        if args[:1] == ["cd"]:
            if len(args) < 5 or args[2] != "&&" or args[1].startswith("-"):
                raise Unsupported("unsupported recipe working directory")
            try:
                command_cwd = relative(cwd, args[1])
            except ValueError:
                raise Unsupported("unsafe recipe working directory") from None
            args = args[3:]
        if any(
            v in {"&&", "||", "|", ";", ">", "<"} or any(c in v for c in "$`\n\r") for v in args
        ):
            raise Unsupported("unsupported Go recipe control flow")
        checks = observed(args, command_cwd)
        if not checks:
            raise Unsupported("unsupported/partial Go verification command")
        manifest = relative(command_cwd, "go.mod")
        if observe(manifest)["state"] != "regular":
            raise Unsupported("missing or unsafe Go module input")
        try:
            parse_mod(sourced(manifest).decode("utf-8"))
        except CaptureDrift:
            raise
        except (ValueError, UnicodeError):
            raise Unsupported("unavailable/unsupported Go execution module") from None
        # Optional lock/workspace files, if present, are bound through the same no-follow reader.
        lock = relative(command_cwd, "go.sum")
        state = observe(lock)["state"]
        if state == "regular":
            sourced(lock)
        elif state != "absent":
            raise Unsupported("unsafe Go lock input")
        parent = PurePosixPath(command_cwd)
        while True:
            for name in ("go.work", "go.work.sum"):
                path = (parent / name).as_posix()
                state = observe(path)["state"]
                if state == "regular":
                    sourced(path)
                elif state != "absent":
                    raise Unsupported("unsafe Go workspace input")
            if parent == PurePosixPath("."):
                break
            parent = parent.parent
        mapped = shlex.join(args)
        if command_cwd != ".":
            mapped = "(cd " + shlex.quote("./" + command_cwd) + " && " + mapped + ")"
        effective.append(mapped)
        records.extend(checks)
        requirements.append(
            {
                "purpose": checks[0]["go_operation"],
                "argv": args,
                "cwd": command_cwd,
                "env_names": GO_ENV,
                "targets": checks,
                "dependencies": list(dependencies),
            }
        )
    return {
        "records": records,
        "requirements": requirements,
        "commands": effective,
        "dependencies": dependencies,
        "next_cwd": outer_cwd if make_command else command_cwd,
        "makefile_selection": selections,
    }


def execution(
    lines, root, read, provider="gitlab", *, shell_starts=(), observe=None, qualify_environment=True
):
    dependencies = {}
    from al_ci_environment import go_environment, go_environment_guard

    try:
        make_context = any(shlex.split(line)[:1] == ["make"] for line in lines)
    except ValueError:
        raise Unsupported("malformed Go verification command") from None
    environment = (
        go_environment(make_context=make_context)
        if qualify_environment
        else {"schema_version": 1, "profile": "unqualified-container-observation-v1", "inputs": {}}
    )
    env_names = sorted(set(GO_ENV) | set(environment["inputs"]))
    selections = []

    def dependency(path):
        raw = read(path)
        import hashlib

        dependencies[path] = {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "job": None}
        return raw

    records = []
    requirements = []
    commands = []
    current_cwd = "."
    for index, line in enumerate(lines):
        if index in shell_starts:
            current_cwd = "."
        if not isinstance(line, str):
            raise Unsupported("literal Go verification command required")
        try:
            tokens = shlex.split(line)
        except ValueError:
            raise Unsupported("malformed Go verification command") from None
        result = resolve(root, tokens, dependency, cwd=current_cwd, observe=observe)
        selections.extend(result["makefile_selection"])
        current_cwd = result["next_cwd"]
        records.extend(result["records"])
        commands.extend(result["commands"])
        requirements.extend(
            {**r, "env_names": env_names, "selector": "script[" + str(index) + "]"}
            for r in result["requirements"]
        )
    chain = {
        "schema_version": 1,
        "provider": provider,
        "workflow": None,
        "job": None,
        "applicability": None,
        "setup": [],
        "requirements": requirements,
        "source_chain": [],
        "complete": True,
        "go_environment": environment,
        "makefile_selection": selections,
    }
    return {
        "verification": records,
        "verification_chain": chain,
        "source_dependencies": list(dependencies.values()),
        "input_scope": sorted(dependencies),
        "argv": [
            "sh",
            "-eu",
            "-c",
            "\n".join(
                ([go_environment_guard(make_context=make_context)] if qualify_environment else [])
                + commands
            ),
        ],
        "cwd": ".",
        "execution_kind": "local",
        "env_names": env_names,
    }


def container_observation(job, root, read, *, observe=None):
    """Identify a sourced container recipe without granting an execution mapping."""
    image = job.get("image", job.get("container"))
    if not isinstance(image, str) or not re.fullmatch(
        r"golang:[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:@sha256:[0-9a-f]{64})?", image
    ):
        return None
    lines = job.get("script")
    shell_starts = []
    if lines is None and isinstance(job.get("steps"), list):
        steps = [step for step in job["steps"] if step != {"uses": "actions/checkout@v4"}]
        if not all(isinstance(step, dict) and set(step) == {"run"} for step in steps):
            return None
        lines = []
        for step in steps:
            if not isinstance(step["run"], str):
                raise Unsupported("unsupported container Go run shape")
            shell_starts.append(len(lines))
            lines.extend(step["run"].splitlines())
    if isinstance(lines, str):
        lines = [lines]
    if not isinstance(lines, list) or not lines or job.get("before_script") not in (None, [], ""):
        return None
    if any(not isinstance(line, str) for line in lines):
        raise Unsupported("unsupported container Go script shape")
    result = execution(
        lines, root, read, shell_starts=shell_starts, observe=observe, qualify_environment=False
    )
    return {
        "schema_version": 1,
        "classification": "sourced-go-container-recipe",
        "image": image,
        "verification": result["verification"],
        "requirements": result["verification_chain"]["requirements"],
        "sources": result["source_dependencies"],
        "execution_status": "unverified",
        "execution_authority": "existing-container-or-remote-route-required",
        "host_go_required": False,
    }
