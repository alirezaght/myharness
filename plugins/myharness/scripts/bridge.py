#!/usr/bin/env python3
"""myharness bridge: keep the global Harness + AL installation at the newest release tag.

The plugin carries no copy of the Harness or AL skills and agents. On session start the hook makes a
fast, non-blocking check; when a check is due it starts a detached worker that installs through the
existing, supported routes only:

  * Harness: `uv tool install --force <checkout of the release tag>`
  * alctl:   `al/alctl.py bootstrap` from that checkout
    (both only when absent or installed by this bridge; `apply --adopt` takes over existing ones)
  * AL:      `alctl install --global --source <source>` on a clean machine, else `alctl update --global`
  * the global Harness skill files, refreshed from `harness skill show` where absent or unedited

Every installer guard stays authoritative. A pinned target, active runs, required reviews or drift are
reported with the exact operator command; the bridge never forces, adopts, reviews or edits approvals.
Runs with Python 3.9+ and the standard library only (the hook uses whatever `python3` is on PATH).
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCHEMA = 1
DEFAULT_SOURCE = "https://github.com/alirezaght/myharness"
DEFAULT_INTERVAL = 3600
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
HOSTS = ("claude", "codex")
LOG_LIMIT = 1 << 20


def source():
    return os.environ.get("MYHARNESS_SOURCE") or DEFAULT_SOURCE


def interval():
    try:
        return max(0, int(os.environ.get("MYHARNESS_BRIDGE_INTERVAL", DEFAULT_INTERVAL)))
    except ValueError:
        return DEFAULT_INTERVAL


def home():
    return Path.home()


def state_dir():
    configured = os.environ.get("MYHARNESS_BRIDGE_STATE")
    if configured:
        return Path(configured)
    base = os.environ.get("XDG_STATE_HOME") or str(home() / ".local/state")
    return Path(base) / "myharness-bridge"


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
def lock(blocking=False):
    """One worker at a time across both hosts; yields False when another holds it."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / "lock", "a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()


def log(message):
    path = state_dir() / "bridge.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_LIMIT:
            tail = path.read_bytes()[-LOG_LIMIT // 2:]
            path.write_bytes(tail)
    except OSError:
        pass
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "a") as handle:
        handle.write(stamp + " " + message.rstrip() + "\n")


def notice(state, key, text, level="info"):
    """Record a notice; action notices repeat every session until resolved, info notices once."""
    state["notices"] = [n for n in state.get("notices", []) if n.get("id") != key]
    state["notices"].append({"id": key, "text": text, "level": level, "shown": False})


def resolve(state, prefix):
    state["notices"] = [n for n in state.get("notices", []) if not n.get("id", "").startswith(prefix)]


def run(argv, timeout, cwd=None, env=None):
    log("$ " + " ".join(str(a) for a in argv))
    merged = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    merged.update(env or {})
    try:
        result = subprocess.run([str(a) for a in argv], cwd=cwd, env=merged, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("! " + repr(exc))
        return 127, "", str(exc)
    for name, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        if text.strip():
            log(name + ": " + text.strip()[-4000:])
    log("exit " + str(result.returncode))
    return result.returncode, result.stdout, result.stderr


def summary(stdout, stderr, limit=400):
    text = (stderr.strip() or stdout.strip()).splitlines()
    line = " ".join(text[-3:]) if text else "no output"
    return line[-limit:]


def latest_tag():
    code, out, err = run(["git", "ls-remote", "--tags", "--refs", source()], timeout=60)
    if code:
        return None, summary(out, err)
    tags = []
    for line in out.splitlines():
        parts = line.split("\trefs/tags/")
        if len(parts) == 2 and TAG.match(parts[1]):
            tags.append(tuple(int(x) for x in TAG.match(parts[1]).groups()))
    if not tags:
        return None, "no stable vMAJOR.MINOR.PATCH tag at " + source()
    return "v%d.%d.%d" % max(tags), None


def checkout(tag):
    """A shallow clone of exactly this tag, kept beside the previous one."""
    root = state_dir() / "src"
    target = root / tag
    if (target / "al/alctl.py").is_file():
        return target, None
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".clone-", dir=str(root)))
    code, out, err = run(["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1",
                          "--branch", tag, source(), temporary / "repo"], timeout=600)
    if code:
        shutil.rmtree(temporary, ignore_errors=True)
        return None, summary(out, err)
    os.replace(temporary / "repo", target)
    shutil.rmtree(temporary, ignore_errors=True)
    keep = sorted((p for p in root.iterdir() if p.is_dir() and TAG.match(p.name)),
                  key=lambda p: tuple(int(x) for x in TAG.match(p.name).groups()))[-2:]
    for path in root.iterdir():
        if path.is_dir() and TAG.match(path.name) and path not in keep:
            shutil.rmtree(path, ignore_errors=True)
    return target, None


def alctl_path():
    found = shutil.which("alctl")
    if found:
        return found
    default = home() / ".local/bin/alctl"
    return str(default) if default.is_file() else None


def bootstrap_python():
    """The interpreter of the existing alctl launcher when usable, else a uv-provided CPython 3.13."""
    launcher = alctl_path()
    if launcher:
        try:
            match = re.search(r"exec' (\S+) ", Path(launcher).read_text(errors="replace")[:600])
        except OSError:
            match = None
        if match and Path(match.group(1)).is_file():
            return match.group(1)
    code, out, _ = run(["uv", "python", "find", "3.13"], timeout=60)
    if code:
        run(["uv", "python", "install", "3.13"], timeout=900)
        code, out, _ = run(["uv", "python", "find", "3.13"], timeout=60)
    return out.strip() if code == 0 and out.strip() else None


def global_target():
    launcher = alctl_path()
    if not launcher:
        return None, "alctl unavailable"
    code, out, err = run([launcher, "list", "--json"], timeout=120)
    if code:
        return None, summary(out, err)
    try:
        rows = json.loads(out).get("installations", [])
    except (ValueError, AttributeError):
        return None, "unreadable alctl list output"
    root = str(home())
    for row in rows:
        target = row.get("target") or {}
        if target.get("scope") == "global" and target.get("root") == root:
            return row, None
    return {}, None


def sync_harness_skills(state):
    """Write each host's global Harness skill from the installed package, never over an edited file."""
    harness = shutil.which("harness") or str(home() / ".local/bin/harness")
    owned = state.setdefault("skills", {})
    for provider, base in (("claude", home() / ".claude"), ("codex", home() / ".codex")):
        if not base.is_dir():
            continue
        code, out, err = run([harness, "skill", "show", "--provider", provider], timeout=120)
        if code:
            notice(state, "skill:" + provider, "Harness skill for " + provider + " not refreshed: " + summary(out, err), "action")
            continue
        path = base / "skills/harness/SKILL.md"
        new = out.encode()
        digest = hashlib.sha256(new).hexdigest()
        current = path.read_bytes() if path.is_file() else None
        recorded = owned.get(str(path))
        if current is None or current == new or hashlib.sha256(current).hexdigest() == recorded:
            if current != new:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(".SKILL.md.bridge")
                temporary.write_bytes(new)
                os.replace(temporary, path)
            owned[str(path)] = digest
            resolve(state, "skill:" + provider)
        else:
            notice(state, "skill:" + provider, "Left the edited " + str(path) + " unchanged. Delete it to let the "
                   "myharness plugin manage it, or refresh it yourself with `harness skill show --provider "
                   + provider + "`.", "action")


def apply(host=None, adopt=False):
    with lock() as held:
        if not held:
            return 0
        state = load()
        state.update(checked_at=now(), source=source(), script=str(Path(__file__).resolve()))
        missing = [tool for tool in ("git", "uv") if not shutil.which(tool)]
        if missing:
            notice(state, "prerequisites", "myharness needs " + " and ".join(missing) + " on PATH to install or update "
                   "Harness and AL (uv: https://docs.astral.sh/uv/).", "action")
            save(state)
            return 1
        resolve(state, "prerequisites")
        tag, error = latest_tag()
        if not tag:
            notice(state, "source", "Could not read releases from " + source() + ": " + error, "action")
            save(state)
            return 1
        resolve(state, "source")
        state["latest"] = tag
        version = tag[1:]
        tree = None
        manage_harness = adopt or bool(state.get("harness_tag")) or not shutil.which("harness")
        manage_alctl = adopt or bool(state.get("alctl_tag")) or not alctl_path()
        if not (manage_harness and manage_alctl):
            notice(state, "adopt", "Harness or alctl was installed outside myharness, so it is left as is. To let myharness keep "
                   "both at the newest release, run `python3 " + str(Path(__file__).resolve()) + " apply --adopt`.")
        else:
            resolve(state, "adopt")
        if manage_harness and (state.get("harness_tag") != tag or not shutil.which("harness")):
            tree, error = checkout(tag)
            if tree:
                code, out, err = run(["uv", "tool", "install", "--force", tree], timeout=1800)
                if code == 0:
                    state["harness_tag"] = tag
                    resolve(state, "harness")
                    notice(state, "harness-updated", "Harness updated to " + tag + ".")
                else:
                    notice(state, "harness", "Harness " + tag + " install failed: " + summary(out, err), "action")
            else:
                notice(state, "harness", "Could not fetch " + tag + ": " + error, "action")
        if shutil.which("harness") or (home() / ".local/bin/harness").is_file():
            sync_harness_skills(state)
        if manage_alctl and (state.get("alctl_tag") != tag or not alctl_path()):
            tree, error = (tree, None) if tree else checkout(tag)
            python = bootstrap_python() if tree else None
            if tree and python:
                code, out, err = run([python, tree / "al/alctl.py", "bootstrap"], timeout=600)
                if code == 0:
                    state["alctl_tag"] = tag
                    resolve(state, "alctl")
                else:
                    notice(state, "alctl", "alctl " + tag + " bootstrap refused: " + summary(out, err), "action")
            else:
                notice(state, "alctl", "alctl " + tag + " not bootstrapped: " + (error or "no Python 3.13 available"), "action")
        row, error = global_target()
        launcher = alctl_path()
        if row is None:
            notice(state, "al", "AL status unavailable: " + error, "action")
        elif not row:
            code, out, err = run([launcher, "install", "--global", "--source", source()], timeout=3600)
            if code == 0:
                resolve(state, "al")
                notice(state, "al-updated", "AL " + version + " installed globally for Claude and Codex.")
                state["al_version"] = version
            else:
                notice(state, "al", "AL install needs attention: " + summary(out, err)
                       + " Run `alctl install --global --source " + source() + "` (see al/INSTALL.md).", "action")
        else:
            state["al_version"], state["al_policy"] = row.get("version"), row.get("policy")
            if row.get("version") == version:
                resolve(state, "al")
            elif row.get("policy") != "follow":
                notice(state, "al", "AL is pinned at " + str(row.get("version")) + "; " + version + " is available. "
                       "To receive releases automatically run `alctl update --global --policy follow`.", "action")
            else:
                code, out, err = run([launcher, "update", "--global"], timeout=3600)
                if code == 0:
                    state["al_version"] = version
                    resolve(state, "al")
                    notice(state, "al-updated", "AL updated to " + version + " (active runs stay on their pinned runtime).")
                else:
                    notice(state, "al", "AL " + version + " is available but the update was deferred: " + summary(out, err)
                           + " Run `alctl update --global` when ready (see al/INSTALL.md).", "action")
        state["completed_at"] = now()
        save(state)
        return 0


def spawn(host):
    """Start the worker detached so the session never waits for network or installs."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "worker.out", "ab") as output:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "apply", "--host", host],
                         stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         start_new_session=True, close_fds=True)


def hook(host):
    """Fast path for SessionStart: print pending notices, start a check when one is due. Never fails."""
    try:
        state = load()
        due = now() - float(state.get("checked_at") or 0) >= interval()
        lines = []
        with lock() as held:
            for item in state.get("notices", []):
                if item.get("level") == "action" or not item.get("shown"):
                    lines.append("- " + item["text"])
                    item["shown"] = True
            if held and lines:
                save(state)
        if due:
            with lock() as free:
                if free:
                    state = load()
                    state["checked_at"] = now()
                    save(state)
            if free:
                spawn(host)
        if lines:
            print("myharness (Harness + AL):\n" + "\n".join(lines))
    except Exception as exc:  # the session must start whatever happens here
        try:
            log("hook error: " + repr(exc))
        except OSError:
            pass
    return 0


def status(as_json):
    state = load()
    if as_json:
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0
    def when(value):
        return datetime.datetime.fromtimestamp(value).isoformat(timespec="seconds") if value else "never"
    print("source:        " + str(state.get("source") or source()))
    print("latest:        " + str(state.get("latest") or "unknown"))
    print("harness:       " + str(state.get("harness_tag") or "not installed by myharness"))
    print("alctl:         " + str(state.get("alctl_tag") or "not bootstrapped by myharness"))
    print("AL (global):   " + str(state.get("al_version") or "unknown") + " (" + str(state.get("al_policy") or "?") + ")")
    print("last check:    " + when(state.get("checked_at")) + "; completed " + when(state.get("completed_at")))
    print("log:           " + str(state_dir() / "bridge.log"))
    for item in state.get("notices", []):
        print(("ACTION: " if item.get("level") == "action" else "note:   ") + item["text"])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="myharness-bridge", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("hook", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--host", choices=HOSTS, default="claude")
        if name == "apply":
            command.add_argument("--adopt", action="store_true",
                                 help="also take over a Harness/alctl installed outside myharness")
    shown = commands.add_parser("status")
    shown.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "hook":
        return hook(args.host)
    if args.command == "apply":
        return apply(args.host, args.adopt)
    return status(args.json)


if __name__ == "__main__":
    sys.exit(main())
