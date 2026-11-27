---
name: myharness
description: Check or update the Harness + AL installation that the myharness plugin keeps at the newest release; use when the user asks whether Harness or AL is installed, up to date, or why an update did not apply.
---

# myharness status and updates

The myharness plugin does not contain Harness or AL itself. It keeps the user's global installation at the
newest release tag of https://github.com/alirezaght/myharness through the supported installers:
`uv tool install` for Harness, `al/alctl.py bootstrap` for `alctl`, and `alctl install|update --global` for AL
(Claude and Codex). A session-start hook checks at most once an hour and updates in the background.

## Find the bridge script

Use `${CLAUDE_PLUGIN_ROOT}/scripts/bridge.py` when that path exists. Otherwise (for example in Codex), read the
`script` field of `~/.local/state/myharness-bridge/status.json`. If neither exists yet, the plugin has not run
its first check: start a new session.

## Status

```sh
python3 <bridge.py> status
```

Report the versions and every `ACTION:` line to the user verbatim. Action lines name the exact command the user
can run; do not run commands that change the global installation unless the user asks.

## Update now

When the user asks to update immediately, run `python3 <bridge.py> apply` and then `status`.
`apply --adopt` additionally takes over a Harness or `alctl` that was installed outside the plugin (for example
from a development checkout) and replaces it with the newest release; run it only when the user asks for that.

## Guards stay in charge

The bridge never forces an update. A pinned AL target, active AL runs, required instruction reviews and drift are
reported, not overridden: runs already in progress stay on their pinned runtime, and the operator decides when to
run the reported `alctl` command (see `al/INSTALL.md`). Logs: `~/.local/state/myharness-bridge/bridge.log`.
