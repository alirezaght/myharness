# myharness plugin (Claude and Codex)

One plugin installs Harness and AL and keeps them at the newest release tag of this repository. It
is a thin bridge: it ships no copy of the Harness or AL skills and agents. It installs and updates them through the
existing, supported installers, so there is one source of truth and no duplicate registrations.

## Install

Claude Code (CLI, IDE extensions, desktop app Code tab):

```sh
claude plugin marketplace add alirezaght/myharness
claude plugin install myharness@myharness
```

Codex (desktop app and CLI):

```sh
codex plugin marketplace add alirezaght/myharness
codex plugin add myharness@myharness
```

In Codex, review and trust the plugin's SessionStart hook once (`/hooks` in the CLI, or Settings → Hooks in the
app). Codex binds that trust to the hook's content, and the hook file changes only when the plugin itself changes.

Prerequisites: `git`, [`uv`](https://docs.astral.sh/uv/) and `python3` on `PATH`. The bridge reports anything missing.

## What happens

On session start the hook prints any pending notices and returns immediately. At most once an hour it starts a
detached worker. The worker:

1. reads the newest stable `vMAJOR.MINOR.PATCH` tag (`git ls-remote`) and clones exactly that tag;
2. installs Harness with `uv tool install --force <checkout>`;
3. re-bootstraps `alctl` from the same checkout (`al/alctl.py bootstrap`);
4. installs AL globally for Claude and Codex (`alctl install --global --source …`, follow policy) on a clean
   machine, or runs `alctl update --global` for a target that follows releases;
5. refreshes `~/.claude/skills/harness/SKILL.md` and `~/.codex/skills/harness/SKILL.md` from `harness skill show`
   where they are absent, unedited, or were written by the bridge.

So when a new release tag is pushed, both apps pick it up on their next session.

## Guards stay in charge

The bridge never forces an update, writes reviews or edits approvals. It reports the following with the exact
command for the operator, and leaves the installation as it is:

- AL target pinned to a version: `alctl update --global --policy follow` opts in to releases.
- An update deferred by active or resumable runs, required instruction reviews, or drift: `alctl update --global`
  when ready, as described in `al/INSTALL.md`. Runs in progress always stay on their pinned runtime.
- Harness or `alctl` installed outside the plugin (for example from a development checkout):
  `python3 <bridge.py> apply --adopt` lets the plugin take them over.
- A global Harness skill file you edited is left unchanged.

## Status and manual update

Use the `myharness` skill, or run the bridge directly. Its path is the `script` field of
`~/.local/state/myharness-bridge/status.json`.

```sh
python3 <bridge.py> status
python3 <bridge.py> apply
```

The log is `~/.local/state/myharness-bridge/bridge.log`.

## Host coverage

| Surface | Plugin | Automatic update |
|---|---|---|
| Claude Code CLI, IDE extensions, desktop Code tab | yes | yes (SessionStart hook) |
| Codex CLI and desktop app | yes | yes, after trusting the hook once |
| claude.ai / desktop Chat tab | ignores hooks | no; Harness and AL are Code tools |
| Codex IDE extension | no plugins | no; install with `alctl` directly |

The plugin's own files rarely change, so marketplace auto-update is optional. To pick up a new plugin version
automatically, turn on auto-update for the `myharness` marketplace in Claude (`/plugin` → Marketplaces); Codex
upgrades Git marketplaces at startup.

## Uninstall

`claude plugin uninstall myharness@myharness` and `codex plugin remove myharness@myharness` remove only the bridge.
Harness and AL stay installed. Remove them with `uv tool uninstall harness` and the `alctl` commands in `al/INSTALL.md`.
