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

When a session starts, resumes or is cleared, the hook shows any pending notices and returns immediately. In Claude
they appear to you and to the model; in Codex, to the model. At most once an hour it starts a detached worker. The
worker:

1. reads the newest stable `vMAJOR.MINOR.PATCH` tag (`git ls-remote`) and clones exactly that tag;
2. installs Harness with `uv tool install --force <checkout>`;
3. installs AL globally for Claude and Codex (`alctl install --global --source …`, follow policy) on a clean
   machine, or runs `alctl update --global` for a target that follows releases;
4. bootstraps `alctl` from the same checkout (`al/alctl.py bootstrap`): on a clean machine before step 3, otherwise
   only after AL reached that release, so the CLI never moves under a pinned or deferred target;
5. refreshes `~/.claude/skills/harness/SKILL.md` and `~/.codex/skills/harness/SKILL.md` from `harness skill show`
   where they are absent, unedited, or were written by the bridge.

So when a new release tag is pushed, both apps pick it up within an hour of their next session. Harness and
`alctl` are managed only when absent or provably installed by the bridge (the uv receipt names a bridge checkout;
the `alctl` launcher still has the digest the bridge recorded). Reinstalling either yourself ends that management.

The release source is recorded in `~/.local/state/myharness-bridge/status.json` (default
`https://github.com/alirezaght/myharness`); only `apply --source <url-or-path>` changes it, never the environment.
The bridge executes the newest stable tag's `al/alctl.py bootstrap` and installs its Harness package without an
operator review, so install the plugin only from a source whose tag publishing you control.

## Guards stay in charge

The bridge never forces an update, writes reviews or edits approvals. It reports the following with the exact
command for the operator, and leaves the installation as it is:

- AL target pinned to a version: `alctl update --global --policy follow` opts in to releases.
- An update deferred by active or resumable runs, required instruction reviews, or drift: `alctl update --global`
  when ready, as described in `al/INSTALL.md`. Runs in progress always stay on their pinned runtime.
- Harness or `alctl` installed outside the plugin (for example from a development checkout):
  `python3 <bridge.py> apply --take-over` lets the plugin manage them from then on.
- A global Harness skill file that differs from the installed package and was not written by the bridge is left
  unchanged (noted once per release).

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
