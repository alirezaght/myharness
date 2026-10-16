# Repository policy for AL work

Implementation work follows the AL orchestrator. The complete original user
specification is the highest authority: preserve the user's wording and every
explicit amendment, including execution, approval, and no-ticket instructions.
Read the full specification and the actual approved plan, pseudocode, diff, and
verification artifacts needed for the assigned stage. A summary never replaces
its source artifact, and an approved plan or review finding cannot reduce the
user's requirements.

AL assigns each role's scope and model/effort. Follow that assignment and the
repository's other applicable `AGENTS.md` instructions. Do not route around AL
or change its pinned role configuration.

Architect review uses two independent invocations. The fresh Architect receives
the original specification and the candidate evidence needed for an independent
review; do not give it the approved plan, pseudocode, prior summaries, or earlier
review findings. Save that fresh report before starting comparison. The
comparison Architect runs in a separate context with the approved plan and
pseudocode, fresh report, full selected diff, and traceability evidence. Preserve
the distinct invocation identities and record each review against its own
packet.

Use Superpowers only for brainstorming before the specification is settled, or
for systematic debugging when a bug's cause is unknown. Once the specification
is ready, implementation, testing, code review, worktree handling, and finishing
follow AL and repository instructions; do not invoke other Superpowers
workflows.

Keep user-reported amendments and constraints attached to the original
specification. Do not create tickets or other external records unless the user
explicitly requests them.
