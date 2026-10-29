# AL source and release model

This directory is the canonical, version-controlled AL source package in this
repository. The same layout can be moved to a standalone repository later.
`al/release-layout.json` lists the runtime files that are shipped for Codex and
Claude; tests, caches, and build output are excluded.

`alctl` resolves numeric `vMAJOR.MINOR.PATCH` Git tags to immutable commits,
checks the selected source tree, creates a content-addressed release cache, and
then installs through AL's existing distribution ownership and transaction
engine. An enrolled installation keeps its source repository and subdirectory;
updates cannot silently switch either one. Project installations keep an
`al.lock.json` recording their selected release and update policy.

The default ownership registry is `~/.local/state/al-distribution/registry`;
installer metadata lives in its `installer/` child. `AL_DISTRIBUTION_STATE`
and `--state-root PATH` each select the ownership registry root directly.
`--home` selects only the global installation target. Legacy records remain
visible without automatic enrollment or migration.

For a clean release, commit the reviewed source and create a numeric version tag
such as `v1.2.0`. The AL release workflow runs installer tests, both platform
runtime suites, and a runtime-layout audit for version tags or manual dispatch.
Tagged verification binds the tested clean checkout to the exact tag and CI
commit. Development verification reports its dirty state without a release
claim. The workflow verifies; it does not publish files or create a tag.
Installers may use a local repository or a credential-free HTTPS Git URL.

The checkout's CLI can be bootstrapped into an independent local snapshot with
`python3 al/alctl.py bootstrap`. This records development provenance and does
not install or replace the AL runtime. See [INSTALL.md](INSTALL.md) for
installation, updates, review/adoption, diagnostics, and recovery.

Phase 3 adds the shared `al-execution-v1` semantic event contract and guarded
task/blocker commands to both packages. A harness execution requires the
installed package to expose this protocol and its role resources; an older
installation reports an explicit compatibility failure. Read-only capabilities
and event replay are available with `al_state.py integration capabilities` and
`integration events --after N --limit N`. See each package's
[`reference/execution-integration.md`](codex/skills/al/reference/execution-integration.md)
for task admission, agent reports, scoped decisions and recovery. Source edits
do not replace an active global or project installation; install a reviewed
candidate through the existing ownership-aware `alctl` workflow.


Read-only agents must never request background execution, detached/daemonized
commands or persistent services such as database proxies; unmet service
prerequisites go to the supervisor. Claude's Bash hook accepts background
metadata only when absent or literal false and retains its shell restrictions.
This does not contain arbitrary foreground executables spawning descendants or
hosts automatically backgrounding work. Codex role instructions and filesystem
sandboxing are not a universal process sandbox. No automatic process cleanup is
claimed; existing role permissions remain unchanged.

Every supported writer dispatch now uses `writer handoff --id ID --body-file BODY`
after writer admission and evidence-plan registration. It preserves the body and
appends concrete writer/run-bound ledger instructions, saving an immutable prompt
receipt before printing. Use its complete output for initial work, finalization,
rework and follow-ups. Generation does not execute checks, intercept every host
spawn, prevent every bare check or certify delivery. Regenerate when bindings
change. These changes apply after installing this release; existing pinned runs
retain their original runtime.

## Bounded rework recovery

New runs begin with two rework returns. Exhaustion after a needed further return
persists a read-only Architect reassessment; at most two automatic Architect
authorizations add two returns each, for six default returns total across review
corrections. A genuine review correction does not replenish rework credits, and
its independent six-correction limit remains in force. Decline, no useful route,
or the automatic cap records the bounded recovery outcome. That outcome does
not prohibit ordinary scoped implementation when requirements and authority
are valid. The runtime records implementation continuity without replenishing
credits or erasing the reassessment history.

The supervisor retrieves a pending packet with `rework-recovery packet`, submits
the approved Architect's closed response with `rework-recovery decide --file
FILE`, and handles explicit user continuation only with a bound
`rework-recovery continue --file FILE` request. Each distinct accepted user
request grants exactly two credits; replay returns its original receipt. These
commands govern the bounded recovery protocol, not a routine developer approval
for continuing implementation. Tooling and incomplete checks remain operational
issues or verification debt; only material specification or engineering choices
require a developer decision. Real permission, identity, ownership and secret
protections remain enforced. An updated
installation does not migrate older runs or change their immutable runtime
issuer; those runs retain their original command set and evidence.
