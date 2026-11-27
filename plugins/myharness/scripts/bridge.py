#!/usr/bin/env python3
"""myharness bridge: keep the global Harness + AL installation at the newest release tag.

The plugin carries no copy of the Harness or AL skills and agents. On session start the hook makes a
fast, non-blocking check; when a check is due it starts a detached worker that installs through the
existing, supported routes only:

  * Harness: `uv tool install --force <checkout of the release tag>`
  * AL:      `alctl install --global --source <source>` on a clean machine,
             else `alctl update --global` for a target that follows releases
  * alctl:   `al/alctl.py bootstrap` from the tag, only on a clean machine or once AL is at that tag
  * the global Harness skill files, from `harness skill show` where absent or bridge-written

Harness and alctl are managed only when absent or provably installed by this bridge (uv receipt,
launcher digest); `apply --take-over` is the explicit opt-in for existing ones. Every installer
guard stays authoritative: a pinned target, active runs, required reviews or drift are reported
with the exact operator command, never forced. The release source is the recorded one (default:
GitHub); only an explicit `apply --source` changes it. Runs on Python 3.9+, standard library only.
"""

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

try:
    import fcntl
except ImportError:  # not a POSIX host: the plugin stays inert
    fcntl = None

SCHEMA = 1
DEFAULT_SOURCE = "https://github.com/alirezaght/myharness"
DEFAULT_INTERVAL = 3600
# alctl's own stable tag grammar (al/installer/source.py): no leading zeros.
TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
HOSTS = ("claude", "codex")
LOG_LIMIT = 1 << 20


def interval():
    try:
        return max(0, int(os.environ.get("MYHARNESS_BRIDGE_INTERVAL", DEFAULT_INTERVAL)))
    except ValueError:
        return DEFAULT_INTERVAL


def home():
    return Path.home()


def state_dir():
    return home() / ".local/state/myharness-bridge"


def src_root():
    return state_dir() / "src"


def status_path():
    return state_dir() / "status.json"


def now():
    return time.time()


def load():
    try:
        value = json.loads(status_path().read_text())
        if isinstance(value, dict) and value.get("schema_version") == SCHEMA:
            return value
    except (OSError, ValueError):
        pass
    return {"schema_version": SCHEMA, "notices": []}


def save(value):
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".status.", dir=str(path.parent))
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


@contextlib.contextmanager
def lock():
    """One writer at a time across hosts and sessions; yields False when another holds it."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / "lock", "a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()


def trim(path):
    try:
        if path.stat().st_size > LOG_LIMIT:
            path.write_bytes(path.read_bytes()[-LOG_LIMIT // 2 :])
    except OSError:
        pass


def log(message):
    path = state_dir() / "bridge.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    trim(path)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017 (3.9)
    with open(path, "a") as handle:
        handle.write(stamp + " " + message.rstrip() + "\n")


def notice(state, key, text, level="info"):
    """Record a notice: action notices repeat until resolved, info notices show once."""
    previous = [n for n in state.get("notices", []) if n.get("id") == key]
    if previous and previous[0].get("text") == text and previous[0].get("level") == level:
        return
    state["notices"] = [n for n in state.get("notices", []) if n.get("id") != key]
    state["notices"].append({"id": key, "text": text, "level": level, "shown": False})


def resolve(state, key):
    state["notices"] = [n for n in state.get("notices", []) if n.get("id") != key]


def run(argv, timeout, cwd=None):
    log("$ " + " ".join(shlex.quote(str(a)) for a in argv))
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            [str(a) for a in argv],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("! " + repr(exc))
        return 127, "", str(exc)
    for name, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        if text.strip():
            log(name + ": " + text.strip()[-4000:])
    log("exit " + str(result.returncode))
    return result.returncode, result.stdout, result.stderr


def details(stdout, stderr, limit=300):
    """Tool output quoted as data: it reaches the model's context, never as instructions."""
    lines = (stderr.strip() or stdout.strip()).splitlines()
    text = " ".join(lines[-3:])[-limit:] if lines else "no output"
    return 'details: "' + text.replace('"', "'") + '"'


def latest_release(source):
    """(tag, commit) of the newest stable tag, or (None, error)."""
    code, out, err = run(["git", "ls-remote", "--tags", source], timeout=60)
    if code:
        return None, details(out, err)
    objects, peeled = {}, {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\trefs/tags/")
        if not ref:
            continue
        if ref.endswith("^{}"):
            peeled[ref[:-3]] = sha
        else:
            objects[ref] = sha
    tags = [name for name in objects if TAG.match(name)]
    if not tags:
        return None, "no stable vMAJOR.MINOR.PATCH tag at " + source
    tag = max(tags, key=lambda name: tuple(int(x) for x in TAG.match(name).groups()))
    return (tag, peeled.get(tag, objects[tag])), None


def checkout(source, tag, commit):
    """A shallow clone of exactly this tag at this commit, keyed by both."""
    root = src_root()
    target = root / (tag + "-" + commit[:12])
    root.mkdir(parents=True, exist_ok=True)
    for leftover in root.glob(".clone-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    if not (target / "al/alctl.py").is_file():
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        temporary = Path(tempfile.mkdtemp(prefix=".clone-", dir=str(root)))
        clone = [
            "git",
            "-c",
            "advice.detachedHead=false",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            tag,
            source,
            temporary / "repo",
        ]
        code, out, err = run(clone, timeout=600)
        if code:
            shutil.rmtree(temporary, ignore_errors=True)
            return None, details(out, err)
        os.replace(temporary / "repo", target)
        shutil.rmtree(temporary, ignore_errors=True)
    code, out, err = run(["git", "-C", target, "rev-parse", "HEAD"], timeout=60)
    if code or out.strip() != commit:
        shutil.rmtree(target, ignore_errors=True)
        return None, "the checkout of " + tag + " is not the published commit " + commit
    # Keep this checkout and the most recent other one (the previous release).
    others = sorted(
        (p for p in root.iterdir() if p.is_dir() and p != target and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
    )
    for path in others[:-1]:
        shutil.rmtree(path, ignore_errors=True)
    return target, None


def uv_tool_paths():
    code, out, _ = run(["uv", "tool", "dir"], timeout=60)
    tools = Path(out.strip()) if code == 0 and out.strip() else home() / ".local/share/uv/tools"
    code, out, _ = run(["uv", "tool", "dir", "--bin"], timeout=60)
    binaries = Path(out.strip()) if code == 0 and out.strip() else home() / ".local/bin"
    return tools, binaries


def harness_install():
    """(present, owned, checkout): owned when the uv receipt names a bridge checkout."""
    tools, binaries = uv_tool_paths()
    receipt = tools / "harness/uv-receipt.toml"
    present = receipt.is_file() or (binaries / "harness").is_file() or bool(shutil.which("harness"))
    directory = None
    if receipt.is_file():
        match = re.search(r'\bdirectory\s*=\s*"([^"]+)"', receipt.read_text(errors="replace"))
        directory = match.group(1) if match else None
    owned = bool(directory) and Path(directory).parent == src_root()
    return present, owned, directory


def harness_command():
    _, binaries = uv_tool_paths()
    candidate = binaries / "harness"
    return str(candidate) if candidate.is_file() else shutil.which("harness")


def alctl_path():
    default = home() / ".local/bin/alctl"
    return str(default) if default.is_file() else shutil.which("alctl")


def file_digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def bootstrap_python():
    """The existing alctl launcher's interpreter when usable, else a uv-provided CPython 3.13."""
    launcher = alctl_path()
    if launcher:
        try:
            for line in Path(launcher).read_text(errors="replace").splitlines()[:5]:
                if line.startswith("'''exec'"):
                    words = shlex.split(line[3:])
                    if len(words) > 1 and Path(words[1]).is_file():
                        return words[1]
        except (OSError, ValueError):
            pass
    code, out, _ = run(["uv", "python", "find", "3.13"], timeout=60)
    if code:
        run(["uv", "python", "install", "3.13"], timeout=900)
        code, out, _ = run(["uv", "python", "find", "3.13"], timeout=60)
    return out.strip() if code == 0 and out.strip() else None


def bootstrap(state, tree, tag):
    python = bootstrap_python()
    if not python:
        notice(
            state, "alctl", "alctl " + tag + " not bootstrapped: no Python 3.13 found.", "action"
        )
        return False
    code, out, err = run([python, tree / "al/alctl.py", "bootstrap"], timeout=600)
    if code:
        notice(
            state, "alctl", "alctl " + tag + " bootstrap refused; " + details(out, err), "action"
        )
        return False
    state["alctl_tag"], state["alctl_sha256"] = tag, file_digest(alctl_path())
    resolve(state, "alctl")
    save(state)
    return True


def global_target():
    launcher = alctl_path()
    if not launcher:
        return None, "alctl is not installed"
    code, out, err = run([launcher, "list", "--json"], timeout=120)
    if code:
        return None, details(out, err)
    try:
        rows = json.loads(out).get("installations", [])
    except (ValueError, AttributeError):
        return None, "unreadable alctl list output"
    for row in rows if isinstance(rows, list) else []:
        target = row.get("target") if isinstance(row, dict) else None
        if isinstance(target, dict) and target.get("scope") == "global":
            if target.get("root") == str(home()):
                return row, None
    return {}, None


def sync_harness_skills(state, tag):
    """Write each host's global Harness skill from the installed package, never over other text."""
    harness = harness_command()
    if not harness:
        return
    owned = state.setdefault("skills", {})
    for provider, base in (("claude", home() / ".claude"), ("codex", home() / ".codex")):
        if not base.is_dir():
            continue
        code, out, err = run([harness, "skill", "show", "--provider", provider], timeout=120)
        if code:
            notice(
                state,
                "skill:" + provider,
                "Harness skill for " + provider + " not refreshed; " + details(out, err),
                "action",
            )
            continue
        path = base / "skills/harness/SKILL.md"
        new = out.encode()
        current = path.read_bytes() if path.is_file() else None
        if current is None or current == new or file_digest(path) == owned.get(str(path)):
            if current != new:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(".SKILL.md.bridge")
                temporary.write_bytes(new)
                os.replace(temporary, path)
            owned[str(path)] = hashlib.sha256(new).hexdigest()
            resolve(state, "skill:" + provider)
        else:
            notice(
                state,
                "skill:" + provider,
                "Left " + str(path) + " as is for " + tag + ": it differs from the installed "
                "package and was not written by myharness. Delete it to let myharness manage it.",
            )


def update(state, source, take_over):
    missing = [tool for tool in ("git", "uv") if not shutil.which(tool)]
    if missing:
        notice(
            state,
            "prerequisites",
            "myharness needs " + " and ".join(missing) + " on PATH to install or update Harness "
            "and AL (uv: https://docs.astral.sh/uv/).",
            "action",
        )
        return 1
    resolve(state, "prerequisites")
    release, error = latest_release(source)
    if not release:
        notice(state, "source", "Could not read releases from " + source + "; " + error, "action")
        return 1
    resolve(state, "source")
    tag, commit = release
    version, state["latest"] = tag[1:], tag
    tree, error = checkout(source, tag, commit)
    if not tree:
        notice(state, "fetch", "Could not fetch " + tag + "; " + error, "action")
        return 1
    resolve(state, "fetch")

    present, owned, installed = harness_install()
    if take_over or owned or not present:
        if installed != str(tree):
            code, out, err = run(["uv", "tool", "install", "--force", tree], timeout=1800)
            if code == 0:
                state["harness_tag"] = tag
                resolve(state, "harness")
                notice(state, "harness-updated", "Harness updated to " + tag + ".")
                save(state)
            else:
                notice(
                    state,
                    "harness",
                    "Harness " + tag + " install failed; " + details(out, err),
                    "action",
                )
    else:
        notice(state, "take-over:" + tag, take_over_text(tag, "Harness"))

    launcher = alctl_path()
    alctl_owned = bool(launcher) and file_digest(launcher) == state.get("alctl_sha256")
    manage_alctl = take_over or alctl_owned or not launcher
    if not launcher and manage_alctl and not bootstrap(state, tree, tag):
        return 1
    row, error = global_target()
    if row is None:
        notice(state, "al", "AL status unavailable; " + error, "action")
        return 1
    if not row:
        code, out, err = run(
            [alctl_path(), "install", "--global", "--source", source], timeout=3600
        )
        if code:
            notice(
                state,
                "al",
                "AL install needs attention; " + details(out, err) + " Run `alctl install "
                "--global --source " + source + "` (see al/INSTALL.md).",
                "action",
            )
            return 1
        row, _ = global_target()
        row = row or {}
        if row.get("version") == version:
            notice(
                state, "al-updated", "AL " + version + " installed globally for Claude and Codex."
            )
    elif row.get("version") != version and row.get("policy") != "follow":
        notice(
            state,
            "al-pinned:" + version,
            "AL is pinned at " + str(row.get("version")) + "; " + version + " is available. To "
            "receive releases automatically run `alctl update --global --policy follow`.",
        )
    elif row.get("version") != version:
        code, out, err = run([alctl_path(), "update", "--global"], timeout=3600)
        if code:
            notice(
                state,
                "al",
                "AL "
                + version
                + " is available but the update was deferred; "
                + details(out, err)
                + " Run `alctl update --global` when ready (see al/INSTALL.md).",
                "action",
            )
            row = {}
        else:
            row, _ = global_target()
            row = row or {}
            if row.get("version") == version:
                notice(
                    state,
                    "al-updated",
                    "AL updated to " + version + " (runs in progress keep their pinned runtime).",
                )
            else:
                recorded = (row.get("source") or {}).get("identity")
                notice(
                    state,
                    "al-source:" + version,
                    "`alctl update --global` left AL at " + str(row.get("version")) + ": the "
                    "target follows " + str(recorded) + ", which has no " + tag + ".",
                )
    if row:
        state["al_version"], state["al_policy"] = row.get("version"), row.get("policy")
    if row.get("version") == version:
        resolve(state, "al")
        # The coordinator moves only together with AL: never under a pinned or deferred target.
        if manage_alctl and state.get("alctl_tag") != tag:
            bootstrap(state, tree, tag)
    if not manage_alctl and state.get("alctl_tag") != tag:
        notice(state, "take-over-alctl:" + tag, take_over_text(tag, "alctl"))
    return 0


def take_over_text(tag, name):
    return (
        name + " was installed outside myharness, so it stays as is (" + tag + " is available). "
        "To let myharness keep it at the newest release, run `python3 "
        + str(Path(__file__).resolve())
        + " apply --take-over`."
    )


def apply(source=None, take_over=False):
    if fcntl is None:
        print("myharness: this platform is not supported")
        return 1
    with lock() as held:
        if not held:
            print("myharness: another update is already running; see `status`.")
            return 0
        state = load()
        if source:
            state["source"] = source
        state.update(checked_at=now(), script=str(Path(__file__).resolve()))
        source = state.get("source") or DEFAULT_SOURCE
        result = 1
        try:
            result = update(state, source, take_over)
            resolve(state, "crash")
            if take_over:
                state["notices"] = [
                    n for n in state.get("notices", []) if not n["id"].startswith("take-over")
                ]
        except Exception as exc:
            log(traceback.format_exc())
            notice(
                state,
                "crash",
                "myharness stopped unexpectedly ("
                + type(exc).__name__
                + "); see "
                + str(state_dir() / "bridge.log")
                + ".",
                "action",
            )
        finally:
            # Last, so host directories a fresh AL install created are covered too.
            with contextlib.suppress(Exception):
                sync_harness_skills(state, state.get("latest") or "this release")
            state["completed_at"] = now()
            save(state)
        return result


def spawn(host):
    """Start the worker detached so the session never waits for network or installs."""
    output = state_dir() / "worker.out"
    trim(output)
    with open(output, "ab") as handle:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "apply"],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            start_new_session=True,
            close_fds=True,
        )


def emit(host, lines):
    if not lines:
        return
    text = "myharness (Harness + AL):\n" + "\n".join(lines)
    if host == "claude":
        print(
            json.dumps(
                {
                    "systemMessage": text,
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": text,
                    },
                }
            )
        )
    else:
        print(text)


def hook(host):
    """SessionStart fast path: show pending notices, start a due check. Never fails."""
    if fcntl is None:
        return 0
    try:
        lines, due = [], False
        with lock() as held:
            if not held:
                return 0
            state = load()
            for item in state.get("notices", []):
                if item.get("level") == "action" or not item.get("shown"):
                    lines.append("- " + item["text"])
                    item["shown"] = True
            due = now() - float(state.get("checked_at") or 0) >= interval()
            if due:
                state["checked_at"] = now()
            if lines or due:
                save(state)
        if due:
            spawn(host)
        emit(host, lines)
    except Exception:  # the session must start whatever happens here
        with contextlib.suppress(Exception):
            log("hook error: " + traceback.format_exc())
    return 0


def status(as_json):
    state = load()
    if as_json:
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0

    def when(value):
        if not value:
            return "never"
        return datetime.datetime.fromtimestamp(value).isoformat(timespec="seconds")

    rows = [
        ("source", state.get("source") or DEFAULT_SOURCE),
        ("latest", state.get("latest") or "unknown"),
        ("harness", state.get("harness_tag") or "not installed by myharness"),
        ("alctl", state.get("alctl_tag") or "not bootstrapped by myharness"),
        ("AL (global)", str(state.get("al_version")) + " (" + str(state.get("al_policy")) + ")"),
        ("last check", when(state.get("checked_at")) + "; done " + when(state.get("completed_at"))),
        ("log", str(state_dir() / "bridge.log")),
    ]
    for name, value in rows:
        print((name + ":").ljust(14) + str(value))
    for item in state.get("notices", []):
        print(("ACTION: " if item.get("level") == "action" else "note:   ") + item["text"])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="myharness-bridge", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    hook_parser = commands.add_parser("hook")
    hook_parser.add_argument("--host", choices=HOSTS, default="claude")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--source", help="record and use this release source from now on")
    apply_parser.add_argument(
        "--take-over",
        action="store_true",
        help="also manage a Harness/alctl that was installed outside myharness",
    )
    shown = commands.add_parser("status")
    shown.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "hook":
        return hook(args.host)
    if args.command == "apply":
        return apply(args.source, args.take_over)
    return status(args.json)


if __name__ == "__main__":
    sys.exit(main())
