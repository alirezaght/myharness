# Embedded fixture scripts keep their natural line lengths.
# ruff: noqa: E501
"""The myharness plugin bridge installs and updates through the existing installers only (#70).

A real local Git source with release tags stands in for GitHub. `uv`, the bootstrapped `alctl` and
`harness` are recording fakes, so each test observes exactly which installer commands ran, and that no
command ever bypasses an installer guard.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/myharness"
BRIDGE = PLUGIN / "scripts/bridge.py"
# Resolved at import, before fixtures put test shims on PATH.
CLAUDE = shutil.which("claude")
FORBIDDEN = (
    "--allow-running",
    "--reviews",
    "--adopt",
    "--documentation-only",
    "--reviewed-runtime-update",
    "--policy",
)

FAKE_ALCTL = textwrap.dedent("""\
    #!/bin/sh
    \'\'\'exec' PYTHON -I "$0" "$@"
    \' \'\'\'
    import json, os, sys
    from pathlib import Path
    home = Path(os.environ["HOME"])
    with (home / "calls.jsonl").open("a") as f: f.write(json.dumps(["alctl", *sys.argv[1:]]) + "\\n")
    state = home / "al-target.json"
    command = sys.argv[1]
    if command == "list":
        rows = [json.loads(state.read_text())] if state.exists() else []
        print(json.dumps({"installations": rows, "legacy": []})); sys.exit(0)
    if (home / "refuse").exists():
        print("update deferred: 2 potentially resumable runs", file=sys.stderr); sys.exit(13)
    latest = (home / "latest").read_text().strip()
    if command == "install":
        state.write_text(json.dumps({"target": {"scope": "global", "root": str(home)}, "version": latest, "policy": "follow", "source": {"identity": sys.argv[-1]}}))
    elif command == "update" and not (home / "noop").exists():
        row = json.loads(state.read_text()); row["version"] = latest; state.write_text(json.dumps(row))
    """)

BOOTSTRAP = textwrap.dedent("""\
    import os, sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    home = Path(os.environ["HOME"]); target = home / ".local/bin/alctl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with (home / "calls.jsonl").open("a") as f: f.write('["bootstrap", "' + str(here.parent) + '"]\\n')
    target.write_text((here / "fake_alctl").read_text().replace("PYTHON", sys.executable) + "# " + str(here.parent) + "\\n"); target.chmod(0o755)
    """)

UV = textwrap.dedent("""\
    #!/bin/sh
    printf '%s\\n' "$(PYTHON -c 'import json,sys;print(json.dumps(["uv",*sys.argv[1:]]))' "$@")" >> "$HOME/calls.jsonl"
    tools="$HOME/.local/share/uv/tools"; bin="$HOME/.local/bin"
    if [ "$1 $2 $3" = "tool dir --bin" ]; then echo "$bin"; exit 0; fi
    if [ "$1 $2" = "tool dir" ]; then echo "$tools"; exit 0; fi
    if [ "$1 $2" = "tool install" ]; then
      tree="$4"; version=$(cat "$tree/VERSION")
      mkdir -p "$bin" "$tools/harness"
      printf '[tool]\\nrequirements = [{ name = "harness", directory = "%s" }]\\n' "$tree" > "$tools/harness/uv-receipt.toml"
      printf '#!/bin/sh\\nprintf "harness skill for %%s at %s\\\\n" "$4"\\n' "$version" > "$bin/harness"
      chmod +x "$bin/harness"; exit 0
    fi
    if [ "$1 $2" = "python find" ]; then echo "PYTHON"; exit 0; fi
    exit 2
    """)


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )


class World:
    def __init__(self, tmp_path, python=sys.executable):
        self.python = python
        self.home = tmp_path / "home"
        self.bin = tmp_path / "bin"
        self.source = tmp_path / "source"
        for path in (self.home / ".claude", self.home / ".codex", self.bin, self.source):
            path.mkdir(parents=True)
        write(self.source / "al/alctl.py", BOOTSTRAP)
        write(self.source / "al/fake_alctl", FAKE_ALCTL)
        git(self.source, "init", "-q", "-b", "main")
        self.release("v0.1.70")
        write(self.bin / "uv", UV.replace("PYTHON", sys.executable), 0o755)
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join(
                [
                    str(self.home / ".local/bin"),
                    str(self.bin),
                    os.path.dirname(shutil.which("git")),
                    "/usr/bin",
                    "/bin",
                ]
            ),
        }
        self.state = self.home / ".local/state/myharness-bridge"

    def release(self, tag):
        write(self.source / "VERSION", tag[1:] + "\n")
        git(self.source, "add", "-A")
        git(self.source, "commit", "-qm", tag)
        git(self.source, "tag", "-a", tag, "-m", tag)
        self.set_latest(tag[1:])

    def set_latest(self, version):
        (self.home / "latest").write_text(version)

    def bridge(self, *args, extra=None, timeout=120):
        if args[:1] == ("apply",) and not self.state.joinpath("status.json").exists():
            args = (*args, "--source", str(self.source))
        return subprocess.run(
            [self.python, str(BRIDGE), *args],
            env=dict(self.env, **(extra or {})),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def calls(self):
        path = self.home / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def reset_calls(self):
        (self.home / "calls.jsonl").unlink(missing_ok=True)

    def status(self):
        return json.loads((self.state / "status.json").read_text())

    def notices(self, level=None):
        return [n["text"] for n in self.status()["notices"] if level in (None, n["level"])]

    def tree(self, tag):
        [path] = (self.state / "src").glob(tag + "-*")
        return str(path)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def assert_guarded(calls):
    flat = [arg for call in calls for arg in call]
    assert not [arg for arg in flat if arg.startswith(FORBIDDEN)], calls


def installs(calls):
    return [c for c in calls if c[:3] == ["uv", "tool", "install"]]


def bootstraps(calls):
    return [c for c in calls if c[0] == "bootstrap"]


QUIET = {"MYHARNESS_BRIDGE_INTERVAL": "99999"}


def test_clean_machine_installs_harness_alctl_al_and_skills(world):
    assert world.bridge("apply").returncode == 0
    calls = world.calls()
    tree = world.tree("v0.1.70")
    assert installs(calls) == [["uv", "tool", "install", "--force", tree]]
    assert bootstraps(calls) == [["bootstrap", tree]]
    assert ["alctl", "install", "--global", "--source", str(world.source)] in calls
    assert_guarded(calls)
    for provider, base in (("claude", ".claude"), ("codex", ".codex")):
        text = (world.home / base / "skills/harness/SKILL.md").read_text()
        assert text == "harness skill for " + provider + " at 0.1.70\n"
    status = world.status()
    assert (status["harness_tag"], status["alctl_tag"]) == ("v0.1.70", "v0.1.70")
    assert (status["al_version"], status["al_policy"]) == ("0.1.70", "follow")
    assert world.notices("action") == []


def test_new_release_moves_al_first_then_its_coordinator(world):
    assert world.bridge("apply").returncode == 0
    world.release("v0.1.71")
    world.reset_calls()
    assert world.bridge("apply").returncode == 0
    calls = world.calls()
    tree = world.tree("v0.1.71")
    assert installs(calls) == [["uv", "tool", "install", "--force", tree]]
    assert bootstraps(calls) == [["bootstrap", tree]]
    assert calls.index(["bootstrap", tree]) > calls.index(["alctl", "update", "--global"])
    assert_guarded(calls)
    assert "0.1.71" in (world.home / ".claude/skills/harness/SKILL.md").read_text()
    assert world.status()["al_version"] == "0.1.71"
    world.reset_calls()
    assert world.bridge("apply").returncode == 0
    assert not installs(world.calls()) and not bootstraps(world.calls())
    assert ["alctl", "update", "--global"] not in world.calls()


def test_refused_update_is_reported_and_leaves_the_coordinator_alone(world):
    assert world.bridge("apply").returncode == 0
    launcher = (world.home / ".local/bin/alctl").read_bytes()
    world.release("v0.1.71")
    (world.home / "refuse").write_text("")
    world.reset_calls()
    assert world.bridge("apply").returncode == 0
    assert_guarded(world.calls())
    assert not bootstraps(world.calls())
    assert (world.home / ".local/bin/alctl").read_bytes() == launcher
    assert world.status()["al_version"] == "0.1.70"
    [action] = [a for a in world.notices("action") if a.startswith("AL ")]
    assert "deferred" in action and "'update deferred: 2 potentially resumable runs'" not in action
    assert 'details: "update deferred: 2 potentially resumable runs"' in action
    assert "`alctl update --global`" in action
    for _ in range(2):
        shown = json.loads(world.bridge("hook", extra=QUIET).stdout)
        assert "2 potentially resumable runs" in shown["systemMessage"]
        assert shown["hookSpecificOutput"]["additionalContext"] == shown["systemMessage"]
    (world.home / "refuse").unlink()
    assert world.bridge("apply").returncode == 0
    assert world.status()["al_version"] == "0.1.71"
    assert not [a for a in world.notices("action") if a.startswith("AL ")]
    assert bootstraps(world.calls())


def test_pinned_target_keeps_al_and_coordinator_and_names_the_opt_in_once(world):
    assert world.bridge("apply").returncode == 0
    target = world.home / "al-target.json"
    row = json.loads(target.read_text())
    row["policy"] = "pinned"
    target.write_text(json.dumps(row))
    world.release("v0.1.71")
    world.bridge("hook", extra=QUIET)  # show the install notices first
    world.reset_calls()
    assert world.bridge("apply").returncode == 0
    calls = world.calls()
    assert not [c for c in calls if c[:2] in (["alctl", "update"], ["alctl", "install"])]
    assert not bootstraps(calls)
    assert json.loads(target.read_text())["version"] == "0.1.70"
    [pinned] = [n for n in world.notices() if "pinned at 0.1.70" in n]
    assert "alctl update --global --policy follow" in pinned
    assert "pinned at 0.1.70" in world.bridge("hook", extra=QUIET).stdout
    assert "pinned" not in world.bridge("hook", extra=QUIET).stdout
    assert world.bridge("apply").returncode == 0
    assert "pinned" not in world.bridge("hook", extra=QUIET).stdout


def test_update_without_effect_is_reported_not_claimed(world):
    assert world.bridge("apply").returncode == 0
    world.release("v0.1.71")
    (world.home / "noop").write_text("")
    world.reset_calls()
    assert world.bridge("apply").returncode == 0
    assert world.status()["al_version"] == "0.1.70"
    assert not any("AL updated" in n for n in world.notices())
    assert any("left AL at 0.1.70" in n for n in world.notices())
    assert not bootstraps(world.calls())


def test_existing_installs_are_left_alone_until_take_over(world):
    bin_dir = world.home / ".local/bin"
    write(bin_dir / "harness", "#!/bin/sh\nprintf 'dev harness skill %s\\n' \"$4\"\n", 0o755)
    write(bin_dir / "alctl", FAKE_ALCTL.replace("PYTHON", sys.executable) + "# dev\n", 0o755)
    # Not on PATH: ownership still comes from the install locations, not from PATH.
    world.env["PATH"] = world.env["PATH"].replace(str(bin_dir) + os.pathsep, "")
    assert world.bridge("apply").returncode == 0
    calls = world.calls()
    assert not installs(calls) and not bootstraps(calls)
    assert "dev harness skill" in (world.home / ".claude/skills/harness/SKILL.md").read_text()
    assert sum("apply --take-over" in n for n in world.notices()) == 2
    assert world.bridge("apply", "--take-over").returncode == 0
    calls = world.calls()
    assert installs(calls) and bootstraps(calls)
    assert not any("take-over" in n for n in world.notices())


def test_reinstall_outside_the_bridge_ends_ownership(world):
    assert world.bridge("apply").returncode == 0
    receipt = world.home / ".local/share/uv/tools/harness/uv-receipt.toml"
    receipt.write_text(
        '[tool]\nrequirements = [{ name = "harness", editable = "/dev/checkout" }]\n'
    )
    launcher = world.home / ".local/bin/alctl"
    launcher.write_text(launcher.read_text() + "# rebootstrapped from a dev checkout\n")
    world.release("v0.1.71")
    world.reset_calls()
    assert world.bridge("apply").returncode == 0
    assert not installs(world.calls()) and not bootstraps(world.calls())
    assert ["alctl", "update", "--global"] in world.calls()


def test_edited_global_skill_is_never_overwritten(world):
    edited = world.home / ".claude/skills/harness/SKILL.md"
    write(edited, "my own edits\n")
    assert world.bridge("apply").returncode == 0
    assert edited.read_text() == "my own edits\n"
    assert "0.1.70" in (world.home / ".codex/skills/harness/SKILL.md").read_text()
    assert any(str(edited) in n for n in world.notices())
    world.release("v0.1.71")
    assert world.bridge("apply").returncode == 0
    assert "0.1.71" in (world.home / ".codex/skills/harness/SKILL.md").read_text()
    assert edited.read_text() == "my own edits\n"


def test_failures_after_an_install_keep_ownership_and_report(world):
    blocked = world.home / ".claude/skills/harness/SKILL.md"
    blocked.mkdir(parents=True)  # makes the skill write raise
    assert world.bridge("apply").returncode == 0
    status = world.status()
    assert status["harness_tag"] == "v0.1.70" and status["alctl_tag"] == "v0.1.70"
    blocked.rmdir()
    world.release("v0.1.71")
    (world.home / ".local/bin/alctl").write_text("#!/bin/sh\nexit 3\n")
    assert world.bridge("apply").returncode == 1
    assert world.status()["harness_tag"] == "v0.1.71"
    assert any("AL status unavailable" in n for n in world.notices("action"))


def test_unexpected_error_is_reported_and_progress_kept(world):
    assert world.bridge("apply").returncode == 0
    world.release("v0.1.71")
    target = world.home / "al-target.json"
    target.write_text(
        json.dumps({"target": {"scope": "global", "root": str(world.home)}, "version": 7})
    )
    (world.home / "noop").write_text("")
    target.write_text("[1, 2")  # alctl list now prints unparseable rows
    assert world.bridge("apply").returncode in (0, 1)
    assert world.status()["harness_tag"] == "v0.1.71"
    assert world.notices("action")


def test_hook_never_loses_what_a_worker_saved(world, monkeypatch):
    assert world.bridge("apply").returncode == 0
    spec = importlib.util.spec_from_file_location("bridge_under_test", BRIDGE)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    monkeypatch.setenv("HOME", str(world.home))
    monkeypatch.setattr(bridge, "spawn", lambda host: None)
    real_lock = bridge.lock

    def worker_saves_first():
        state = bridge.load()
        state["harness_tag"] = "v9.9.9"
        bridge.notice(state, "late", "a worker saved this just now")
        bridge.save(state)
        return real_lock()

    monkeypatch.setattr(bridge, "lock", worker_saves_first)
    assert bridge.hook("codex") == 0
    status = world.status()
    assert status["harness_tag"] == "v9.9.9" and status["alctl_tag"] == "v0.1.70"
    assert [n["shown"] for n in status["notices"] if n["id"] == "late"] == [True]


def test_release_selection_ordering_and_retention(world):
    for tag in ("v0.1.9", "v0.1.10", "v1.2.3-rc1", "v1.2", "v01.2.3", "v0.1.070"):
        git(world.source, "tag", "-a", tag, "-m", tag)
    assert world.bridge("apply").returncode == 0
    assert world.status()["latest"] == "v0.1.70"
    for tag in ("v0.1.71", "v0.1.72"):
        world.release(tag)
        assert world.bridge("apply").returncode == 0
    names = sorted(p.name.split("-")[0] for p in (world.state / "src").iterdir())
    assert names == ["v0.1.71", "v0.1.72"]
    # A retracted newest tag: the lower requested tag is kept, never deleted on arrival.
    git(world.source, "tag", "-d", "v0.1.72")
    world.set_latest("0.1.71")
    assert world.bridge("apply").returncode == 0
    assert Path(world.tree("v0.1.71"), "al/alctl.py").is_file()


def test_moved_tag_gets_a_fresh_checkout(world):
    assert world.bridge("apply").returncode == 0
    first = world.tree("v0.1.70")
    write(world.source / "VERSION", "0.1.70-moved\n")
    git(world.source, "commit", "-qam", "move")
    git(world.source, "tag", "-f", "-a", "v0.1.70", "-m", "moved")
    world.reset_calls()
    assert world.bridge("apply", "--take-over").returncode == 0
    [second] = [str(p) for p in (world.state / "src").glob("v0.1.70-*") if str(p) != first]
    assert Path(second, "VERSION").read_text() == "0.1.70-moved\n"
    assert installs(world.calls()) == [["uv", "tool", "install", "--force", second]]


def test_source_comes_from_the_recorded_state_never_the_environment(world):
    assert world.bridge("apply").returncode == 0
    elsewhere = world.home / "elsewhere"
    assert world.bridge("apply", extra={"MYHARNESS_SOURCE": str(elsewhere)}).returncode == 0
    assert world.status()["source"] == str(world.source)
    assert not any("Could not read releases" in n for n in world.notices())
    assert world.bridge("apply", "--source", str(elsewhere)).returncode == 1
    assert world.status()["source"] == str(elsewhere)
    assert any("Could not read releases" in n for n in world.notices("action"))


def test_missing_prerequisite_is_actionable(world):
    (world.bin / "uv").unlink()
    assert world.bridge("apply").returncode == 1
    assert any("needs uv" in n for n in world.notices("action"))


def test_hook_is_fast_rate_limited_and_survives_a_corrupt_status(world):
    world.state.mkdir(parents=True)
    (world.state / "status.json").write_text(
        json.dumps({"schema_version": 1, "notices": [], "source": "https://127.0.0.1:9/x"})
    )
    started = time.monotonic()
    result = world.bridge("hook", "--host", "codex")
    assert result.returncode == 0 and time.monotonic() - started < 5
    assert world.status()["checked_at"] > 0
    for _ in range(100):
        if any("Could not read releases" in n for n in world.notices()):
            break
        time.sleep(0.1)
    assert any("Could not read releases" in n for n in world.notices("action"))
    checked = world.status()["checked_at"]
    shown = world.bridge("hook", "--host", "codex").stdout
    assert shown.startswith("myharness (Harness + AL):\n- Could not read releases")
    assert world.status()["checked_at"] == checked
    (world.state / "status.json").write_text("{not json")
    assert world.bridge("hook").returncode == 0


def test_info_notices_show_once_action_notices_every_session(world):
    assert world.bridge("apply").returncode == 0
    first = json.loads(world.bridge("hook", extra=QUIET).stdout)["systemMessage"]
    assert "AL 0.1.70 installed globally" in first and "Harness updated to v0.1.70" in first
    assert world.bridge("hook", extra=QUIET).stdout == ""


def python39():
    for candidate in ("/usr/bin/python3", shutil.which("python3.9")):
        if candidate and Path(candidate).is_file():
            version = subprocess.run(
                [candidate, "-c", "import sys;print(sys.version_info[:2] < (3, 11))"],
                capture_output=True,
                text=True,
            )
            if version.stdout.strip() == "True":
                return candidate
    return None


@pytest.mark.skipif(python39() is None, reason="no Python older than 3.11 available")
def test_bridge_runs_on_old_stock_python(tmp_path):
    world = World(tmp_path, python=python39())
    result = world.bridge("apply")
    assert result.returncode == 0, result.stderr
    assert world.status()["harness_tag"] == "v0.1.70"
    assert world.bridge("hook", extra=QUIET).returncode == 0
    assert "harness:" in world.bridge("status").stdout
    assert "Traceback" not in (world.state / "bridge.log").read_text()


def test_manifests_hooks_and_marketplace_agree():
    claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    codex = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    assert claude["name"] == codex["name"] == "myharness" and claude["version"] == codex["version"]
    assert (PLUGIN / codex["skills"]).is_dir() and (PLUGIN / codex["hooks"]).is_file()
    for path, host, timeout in (
        (PLUGIN / "hooks/hooks.json", "claude", "timeout"),
        (PLUGIN / codex["hooks"], "codex", "timeoutSec"),
    ):
        [entry] = json.loads(path.read_text())["hooks"]["SessionStart"]
        assert entry["matcher"] == "startup|resume|clear"
        [command] = entry["hooks"]
        assert command["command"] == (
            'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bridge.py" hook --host ' + host
        )
        assert command[timeout] == 15
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    [entry] = market["plugins"]
    assert entry["name"] == "myharness" and (ROOT / entry["source"]).resolve() == PLUGIN.resolve()
    # The plugin carries no copy of the AL or Harness skills or agents: one source of truth.
    assert sorted(p.name for p in (PLUGIN / "skills").iterdir()) == ["myharness"]
    assert not (PLUGIN / "agents").exists()


@pytest.mark.skipif(CLAUDE is None, reason="Claude Code CLI not installed")
def test_claude_validates_plugin_and_marketplace():
    for target in (PLUGIN, ROOT):
        result = subprocess.run(
            [CLAUDE, "plugin", "validate", "--strict", str(target)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
