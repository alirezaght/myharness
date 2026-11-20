## Codex entry

Run this skill inside the existing Codex conversation that received the user's
request. Use the active `CODEX_SESSION_ID` and `CODEX_THREAD_ID` that Codex
supplies to shell tools; never synthesize, copy from another thread, or ask the
browser to provide these values. Keep the foreground `harness session wait`
tool call in this same conversation. If a shell tool returns a running handle,
poll that exact handle until events or timeout; do not launch another wait.

The project can load this skill locally at `.agents/skills/harness/SKILL.md`.
`harness skill show --provider codex` prints the shared instructions followed by
this entry. The package does not install or update global Codex skills.
