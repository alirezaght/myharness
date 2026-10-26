# AL recovery and usability fixes

These changes apply to both Codex and Claude packages and remain generic across
repositories. They do not require using AL to implement this session's changes.

1. Planning errors name uncovered outcome IDs and explain the conservation rule.
   The template requires every declared preserve/reset/journey-end/persistence
   outcome in a scenario's `outcome_ids`.
2. `artifact record --file PATH --kind KIND` creates closed immutable references.
   Artifact and consumer-inventory schemas document the accepted fields.
3. Gate timeout evidence and logs identify the timeout duration; status includes
   evidence reasons and log references.
4. `gates run --id ID --retry-failed` resumes that explicit gate. Ambiguous retry
   forms explain the supported command forms.
5. `writer start` prints the run-specific evidence block for the agent handoff.
6. `artifact output --name NAME --file PATH` retains exact named agent outputs;
   replacement requires the previous hash. Harness agents also have a protected
   named report outbox, including read-only agents.
7. Shell examples use an argv array and `"${STATE[@]}"`, which works in zsh.
   The supported direct executable remains `alctl`.
8. When proof admission is pending, `writer start --degraded --reason TEXT`
   provides explicit tracking of writers and evidence. It preserves scope,
   prerequisites and overlap checks. It does not grant implementation approval,
   waive proof, or retroactively attribute earlier untracked edits. Repaired
   admission must observe the partially built source.
9. Refused public commands exit nonzero. A current, valid same-run named
   preflight can satisfy its named prerequisite without an evidence execution
   plan. Stale, altered or invalidated preflights cannot.
10. Both review packets include repository-owned hook/rule contents, immutable
    references and hashes, following static local text references. Limits and
    unreadable sources are explicit omissions. No repository-specific rule IDs
    or new universal ordering rule are embedded in the review prompt.

Model capacity recovery is separate from implementation retry. A one-time
same-model retry requires reconciliation of writers and checks. Content refusals,
permission failures and availability failures have distinct guidance; a model
refusal is never described as a pending user permission request.

Regression coverage lives in each platform's `test_al_usability.py`,
`test_review_rules.py`, `test_failure_recovery.py` and
`test_execution_corrections.py`. Complete release verification also validates
installer behavior and both runtime packages.
