## Claude Code entry

Run this skill in the existing Claude Code conversation that received the user's
request. Use the locally substituted `${CLAUDE_SESSION_ID}` as the caller
identity on every owner command: `--claude-session-id ${CLAUDE_SESSION_ID}`.
Keep `REPO_ROOT`, request-file paths, capability-file paths, and any database
path absolute. `--repo` selects the project but does not change the shell's
working directory or resolve a relative `--file` against the repository.
Examples:

```sh
harness session start --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --file "$REQUEST_FILE"
harness session attach --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --file "$ATTACH_FILE"
harness session prepare-interview --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --capability "$CAPABILITY_PATH" --file "$OWNER_REQUEST_FILE"
harness session observe-repository --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --capability "$CAPABILITY_PATH" --file "$OWNER_REQUEST_FILE"
harness session wait --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --capability "$CAPABILITY_PATH" --file "$OWNER_REQUEST_FILE" --timeout 25
harness session ack --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --capability "$CAPABILITY_PATH" --file "$ACK_FILE"
harness session publish --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --capability "$CAPABILITY_PATH" --file "$PUBLICATION_FILE"
harness session detach --repo "$REPO_ROOT" --provider claude --claude-session-id "${CLAUDE_SESSION_ID}" --capability "$CAPABILITY_PATH" --file "$OWNER_REQUEST_FILE" --destination manual
```

Read-only status does not require the caller identity:

```sh
harness session status --repo "$REPO_ROOT" --task TASK_ID --context
```

If the literal is unresolved or unavailable, stop with an actionable
identity-unavailable message; do not invent a session ID or use another
conversation's value. Keep the foreground `harness session wait` in this same
conversation. If a shell tool reports a running process, continue polling that
same process until events or timeout; do not start a second wait.

Locally loaded Claude skills can use `${CLAUDE_SESSION_ID}` substitution. The
package can be copied to `.claude/skills/harness/SKILL.md`; synced skills have
different substitution behavior and are not qualified by this entry. The
package does not install or update global Claude configuration.
