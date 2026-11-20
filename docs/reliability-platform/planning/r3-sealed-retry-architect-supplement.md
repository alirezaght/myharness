PLAN_STATUS: REVISED

RESOLUTION

Add **`sealed-al-retry-v1` as one narrowly qualified closed handler** within R3. Until its qualification is implemented, enrolled retry remains refused. Do not exempt `al_command` generally.

Preserve actual checkpoint/candidate/gate semantics. `al_recovery.apply_checkpoint()` calls candidate fingerprinting and gate refresh; `al_git_cache.blobs()` launches `git cat-file --batch`. Those helpers belong to the qualified execution boundary, not outside it.

Required implementation:

1. Bind the exact interpreter, authenticated Git executable, private pinned AL modules and required helper closure in an immutable qualification manifest.
2. Replace PATH-based helper selection within this handler with those bound executables.
3. Permit only the exact read-only Git forms required by existing fingerprint/checkpoint/gate logic. Preserve results and checks; disallow arbitrary Git commands, executable configuration overrides, hooks, external diff/textconv and environment-based command substitution.
4. Bound helper runtime, input/output and object-reading limits. Register every launched helper with the attempt before launch.
5. Publish a normal-completion receipt only after the retry entry point returns through the qualified path and every registered helper is positively settled.
6. Keep **retry authorization consumed**, **AL state saved**, and **qualified boundary closed** as separate facts. A grant/save marker cannot close resource claims.
7. Interruption, missing receipt, helper uncertainty or source drift leaves the attempt fenced. No PID/group-absence shortcut.

Prepare source inventories and executable observations outside SQLite transactions. Inside the short admission transaction, validate the prepared immutable bindings and reserve/start authority. Revalidate required bindings immediately before execution through the qualified launcher.

Ownership remains the current Engineer’s both-platform AL scripts, policy/process/authority/bootstrap and retry-test scope. Designer should name any additional exact path before implementation; no general Git framework, global environment change, installation, pin mutation or native-process qualification is included.

Required evidence:

- Actual protected retry completes with unchanged checkpoint/gate behavior.
- Required Git helpers use bound identities and permitted forms.
- Unsupported argv/config/environment and changed helper bytes refuse before effects.
- Grant/save without complete helper closure cannot release claims.
- Timeout/interruption preserves uncertainty and prevents conflicting reuse.
- Ordinary legacy behavior and unrelated AL commands retain their existing paths.

This supplement was sent directly to Engineer and Designer. No edits or tests were performed.