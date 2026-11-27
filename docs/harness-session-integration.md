# Shared Harness planning sessions

The packaged Harness skill connects the invoking Codex or locally loaded Claude
conversation to one task. The task's browser page and the same foreground TUI
share its current question, answers, progress and editable planning content. The
browser never starts a second model. See the package instructions printed by
`harness skill show --provider codex` or
`harness skill show --provider claude` for the complete protocol and provider
entry guidance.

## Inspect and load the packaged instructions

Use `harness skill show` to inspect the current installed package resources:

```sh
harness skill show --provider codex
harness skill show --provider claude
```

To load them locally, create the provider's normal project skill directory and
copy the output for that provider to its `SKILL.md` file:

```sh
mkdir -p .agents/skills/harness
harness skill show --provider codex > .agents/skills/harness/SKILL.md

mkdir -p .claude/skills/harness
harness skill show --provider claude > .claude/skills/harness/SKILL.md
```

Use the matching entry only inside its provider's active conversation. Codex
uses its active `CODEX_SESSION_ID` and `CODEX_THREAD_ID`; locally loaded Claude
skills use `${CLAUDE_SESSION_ID}` substitution. Missing identity stops the
attachment attempt. Do not copy skill files into global configuration as part
of a Harness task; local installation is an explicit user action. The optional
[myharness plugin](../plugins/myharness/README.md) is that explicit choice for the global copies: once a user
installs it, it writes `~/.claude/skills/harness/SKILL.md` and `~/.codex/skills/harness/SKILL.md` from this
command where they are absent or still unedited. Codex skill
names are not merged across duplicate local/global definitions, so inspect the
active skill source if an older installed `harness` skill takes precedence.

## Session lifecycle

`harness session project --repo PATH` returns the canonical project identity
without opening the database. Start a task atomically from the invoking
conversation with `harness session start`; the packaged skill prints the exact
request fields and publication schemas. Existing tasks are read with
`harness session status --task TASK --context`. Use `--db PATH` before
`session` when the task is in a non-default database.

The active agent owns one foreground wait, bounded to 25 seconds. A running
shell/process handle must be polled until that same command returns events or a
timeout. Browser input is saved immediately and wakes that wait; saved,
received, and applied are distinct states. A normal TUI message can interrupt
the wait and is published with the user's exact wording through the same
attachment. There is no background agent, native session-resume call, terminal
injection, or transcript scraping.

The dashboard shows current session status, conversation event history, current
question, per-event receipt/application state and explicit recovery actions. If
ownership changes or the lease becomes uncertain, it preserves saved task state
and presents recovery details. Reattach is performed from the original provider
context; a healthy different owner is never silently replaced. Standalone and
manual mode switches are explicit and do not imply provider work was started.

## Boundaries

The skill uses only the invoking agent's already authorized tools. Jira is
read-only here. It does not request new authentication, alter native settings,
write Jira content, approve a proposed specification, or start implementation.
Source review, candidate adoption, material-input reconciliation, design review,
test-plan approval, and final approval remain separate explicit steps. Package
availability is not native-provider qualification; provider behavior needs an
actual same-session demonstration.
