# Embedded fixture scripts keep their natural line lengths.
# ruff: noqa: E501
"""The myharness plugin bridge installs and updates through the existing installers only (#70).

A real local Git source with release tags stands in for GitHub. `uv`, the bootstrapped `alctl` and
`harness` are recording fakes, so each test observes exactly which installer commands ran, and that no
command ever bypasses an installer guard.
"""

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
FORBIDDEN = ("--allow-running", "--reviews", "--adopt", "--force-", "--documentation-only")

FAKE_ALCTL = textwrap.dedent("""\
    #!/bin/sh
    \'\'\'exec' PYTHON -I "$0" "$@"
    \' \'\'\'
    import json, os, sys
    from pathlib import Path
    home = Path(os.environ["HOME"])
    calls = home / "calls.jsonl"
    with calls.open("a") as f: f.write(json.dumps(["alctl", *sys.argv[1:]]) + "\\n")
    state = home / "al-target.json"
    version = (Path(__file__).resolve().parent / "alctl.version").read_text().strip()
    command = sys.argv[1]
    if command == "list":
        rows = [json.loads(state.read_text())] if state.exists() else []
        print(json.dumps({"installations": rows, "legacy": []})); sys.exit(0)
    if (home / "refuse").exists():
        print("update deferred: 2 potentially resumable runs", file=sys.stderr); sys.exit(13)
    latest = os.environ["FAKE_LATEST"]
    if command == "install":
        state.write_text(json.dumps({"target": {"scope": "global", "root": str(home)}, "version": latest, "policy": "follow"}))
    elif command == "update":
        row = json.loads(state.read_text()); row["version"] = latest; state.write_text(json.dumps(row))
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
    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.bin = tmp_path / "bin"
        self.source = tmp_path / "source"
        for path in (self.home / ".claude", self.home / ".codex", self.bin, self.source):
            path.mkdir(parents=True)
        bootstrap = textwrap.dedent("""\
            import os, sys
            from pathlib import Path
            here = Path(__file__).resolve().parent
            home = Path(os.environ["HOME"]); target = home / ".local/bin/alctl"
            target.parent.mkdir(parents=True, exist_ok=True)
            with (home / "calls.jsonl").open("a") as f: f.write('["bootstrap", "' + sys.executable + '"]\\n')
            target.write_text((here / "fake_alctl").read_text().replace("PYTHON", sys.executable)); target.chmod(0o755)
            (target.parent / "alctl.version").write_text((here.parent / "VERSION").read_text())
            """)
        write(self.source / "al/alctl.py", bootstrap)
        write(self.source / "al/fake_alctl", FAKE_ALCTL)
        git(self.source, "init", "-q", "-b", "main")
        self.release("v0.1.70")
        uv = textwrap.dedent("""\
            #!/bin/sh
            printf '%s\\n' "$(python3 -c 'import json,sys;print(json.dumps(["uv",*sys.argv[1:]]))' "$@")" >> "$HOME/calls.jsonl"
            if [ "$1 $2" = "tool install" ]; then
              tree="$4"; version=$(cat "$tree/VERSION")
              mkdir -p "$HOME/.local/bin"
              printf '#!/bin/sh\\necho "$HOME" >/dev/null\\nprintf "harness skill for %%s at %s\\\\n" "$4"\\n' "$version" > "$HOME/.local/bin/harness"
              chmod +x "$HOME/.local/bin/harness"; exit 0
            fi
            if [ "$1 $2" = "python find" ]; then echo "PYTHON"; exit 0; fi
            exit 2
            """).replace("PYTHON", sys.executable)
        write(self.bin / "uv", uv, 0o755)
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
            "MYHARNESS_SOURCE": str(self.source),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
        }

    def release(self, tag):
        write(self.source / "VERSION", tag[1:] + "\n")
        git(self.source, "add", "-A")
        git(self.source, "commit", "-qm", tag)
        git(self.source, "tag", "-a", tag, "-m", tag)
        self.latest = tag[1:]

    def bridge(self, *args, extra=None, timeout=120):
        env = dict(self.env, FAKE_LATEST=self.latest, **(extra or {}))
        return subprocess.run(
            [sys.executable, str(BRIDGE), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def calls(self):
        path = self.home / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def status(self):
        return json.loads((self.home / ".local/state/myharness-bridge/status.json").read_text())

    def actions(self):
        return [n["text"] for n in self.status()["notices"] if n["level"] == "action"]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def assert_guarded(calls):
    flat = [arg for call in calls for arg in call]
    assert not [arg for arg in flat if arg.startswith(FORBIDDEN)], calls


def test_clean_machine_installs_harness_alctl_al_and_skills(world):
    assert world.bridge("apply").returncode == 0
    calls = world.calls()
    tree = str(world.home / ".local/state/myharness-bridge/src/v0.1.70")
    assert ["uv", "tool", "install", "--force", tree] in calls
    assert any(call[0] == "bootstrap" for call in calls)
    assert ["alctl", "install", "--global", "--source", str(world.source)] in calls
    assert_guarded(calls)
    for provider, base in (("claude", ".claude"), ("codex", ".codex")):
        assert (
            world.home / base / "skills/harness/SKILL.md"
        ).read_text() == "harness skill for " + provider + " at 0.1.70\n"
    status = world.status()
    assert (status["harness_tag"], status["alctl_tag"], status["al_version"]) == (
        "v0.1.70",
        "v0.1.70",
        "0.1.70",
    )
    assert world.actions() == []


def test_new_release_tag_updates_everything_on_the_next_check(world):
    assert world.bridge("apply").returncode == 0
    world.release("v0.1.71")
    (world.home / "calls.jsonl").unlink()
    assert world.bridge("apply").returncode == 0
    calls = world.calls()
    assert [
        "uv",
        "tool",
        "install",
        "--force",
        str(world.home / ".local/state/myharness-bridge/src/v0.1.71"),
    ] in calls
    assert any(call[0] == "bootstrap" for call in calls)
    assert ["alctl", "update", "--global"] in calls
    assert_guarded(calls)
    assert "0.1.71" in (world.home / ".claude/skills/harness/SKILL.md").read_text()
    assert world.status()["al_version"] == "0.1.71"
    # Same tag again: nothing is reinstalled.
    (world.home / "calls.jsonl").unlink()
    assert world.bridge("apply").returncode == 0
    assert [
        c
        for c in world.calls()
        if c[:2] in (["uv", "tool"], ["alctl", "update"]) or c[0] == "bootstrap"
    ] == []


def test_refused_update_is_reported_with_the_operator_command_never_forced(world):
    assert world.bridge("apply").returncode == 0
    world.release("v0.1.71")
    (world.home / "refuse").write_text("")
    assert world.bridge("apply").returncode == 0
    assert_guarded(world.calls())
    assert world.status()["al_version"] == "0.1.70"
    [action] = [a for a in world.actions() if a.startswith("AL ")]
    assert (
        "deferred" in action
        and "2 potentially resumable runs" in action
        and "`alctl update --global`" in action
    )
    # Action notices repeat every session until resolved.
    for _ in range(2):
        assert (
            "2 potentially resumable runs"
            in world.bridge("hook", extra={"MYHARNESS_BRIDGE_INTERVAL": "99999"}).stdout
        )
    (world.home / "refuse").unlink()
    assert world.bridge("apply").returncode == 0
    assert world.status()["al_version"] == "0.1.71" and not [
        a for a in world.actions() if a.startswith("AL ")
    ]


def test_pinned_target_is_left_pinned_and_the_opt_in_is_named(world):
    assert world.bridge("apply").returncode == 0
    target = world.home / "al-target.json"
    row = json.loads(target.read_text())
    row["policy"] = "pinned"
    target.write_text(json.dumps(row))
    world.release("v0.1.71")
    (world.home / "calls.jsonl").unlink()
    assert world.bridge("apply").returncode == 0
    assert not [c for c in world.calls() if c[:2] in (["alctl", "update"], ["alctl", "install"])]
    assert json.loads(target.read_text())["version"] == "0.1.70"
    assert any(
        "pinned at 0.1.70" in a and "alctl update --global --policy follow" in a
        for a in world.actions()
    )


def test_existing_installs_are_not_replaced_without_adopt(world):
    write(
        world.home / ".local/bin/harness",
        "#!/bin/sh\nprintf 'dev harness skill %s\\n' \"$4\"\n",
        0o755,
    )
    alctl = world.home / ".local/bin/alctl"
    write(alctl, FAKE_ALCTL.replace("PYTHON", sys.executable), 0o755)
    (alctl.parent / "alctl.version").write_text("dev\n")
    assert world.bridge("apply").returncode == 0
    calls = world.calls()
    assert not [c for c in calls if c[:2] == ["uv", "tool"] or c[0] == "bootstrap"]
    assert "dev harness skill" in (world.home / ".claude/skills/harness/SKILL.md").read_text()
    assert any("apply --adopt" in n["text"] for n in world.status()["notices"])
    assert world.bridge("apply", "--adopt").returncode == 0
    calls = world.calls()
    assert [c for c in calls if c[:2] == ["uv", "tool"]] and [
        c for c in calls if c[0] == "bootstrap"
    ]
    assert not any("apply --adopt" in n["text"] for n in world.status()["notices"])


def test_edited_global_skill_is_never_overwritten(world):
    edited = world.home / ".claude/skills/harness/SKILL.md"
    write(edited, "my own edits\n")
    assert world.bridge("apply").returncode == 0
    assert edited.read_text() == "my own edits\n"
    assert "0.1.70" in (world.home / ".codex/skills/harness/SKILL.md").read_text()
    assert any(str(edited) in a for a in world.actions())
    # A file the bridge wrote keeps being refreshed on later releases.
    world.release("v0.1.71")
    assert world.bridge("apply").returncode == 0
    assert "0.1.71" in (world.home / ".codex/skills/harness/SKILL.md").read_text()
    assert edited.read_text() == "my own edits\n"


def test_missing_prerequisite_and_unreachable_source_are_actionable(world):
    (world.bin / "uv").unlink()
    assert world.bridge("apply").returncode == 1
    assert any("needs uv" in a for a in world.actions())
    write(world.bin / "uv", "#!/bin/sh\nexit 2\n", 0o755)
    assert (
        world.bridge("apply", extra={"MYHARNESS_SOURCE": str(world.home / "missing")}).returncode
        == 1
    )
    assert any("Could not read releases" in a for a in world.actions())
    assert not any("needs uv" in a for a in world.actions())


def test_hook_is_fast_rate_limited_and_survives_a_corrupt_status(world):
    state = world.home / ".local/state/myharness-bridge"
    started = time.monotonic()
    result = world.bridge("hook", extra={"MYHARNESS_SOURCE": "https://127.0.0.1:9/unreachable"})
    assert result.returncode == 0 and time.monotonic() - started < 5
    # The due check started a detached worker and stamped the check time.
    assert world.status()["checked_at"] > 0
    for _ in range(100):
        if any("Could not read releases" in a for a in world.actions()):
            break
        time.sleep(0.1)
    assert any("Could not read releases" in a for a in world.actions())
    # Not due again within the interval: no new worker.
    checked = world.status()["checked_at"]
    assert world.bridge("hook").returncode == 0
    assert world.status()["checked_at"] == checked
    (state / "status.json").write_text("{not json")
    assert (
        world.bridge(
            "hook", extra={"MYHARNESS_SOURCE": "https://127.0.0.1:9/unreachable"}
        ).returncode
        == 0
    )


def test_info_notices_show_once_action_notices_every_session(world):
    assert world.bridge("apply").returncode == 0
    quiet = {"MYHARNESS_BRIDGE_INTERVAL": "99999"}
    first = world.bridge("hook", extra=quiet).stdout
    assert "AL 0.1.70 installed globally" in first and "Harness updated to v0.1.70" in first
    assert world.bridge("hook", extra=quiet).stdout == ""


def test_manifests_hooks_and_marketplace_agree():
    claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    codex = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    assert claude["name"] == codex["name"] == "myharness" and claude["version"] == codex["version"]
    assert (PLUGIN / codex["skills"]).is_dir() and (PLUGIN / codex["hooks"]).is_file()
    for path, host in ((PLUGIN / "hooks/hooks.json", "claude"), (PLUGIN / codex["hooks"], "codex")):
        [entry] = json.loads(path.read_text())["hooks"]["SessionStart"]
        [command] = entry["hooks"]
        assert (
            command["command"]
            == 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bridge.py" hook --host ' + host
        )
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
