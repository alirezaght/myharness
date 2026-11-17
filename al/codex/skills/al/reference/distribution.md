# AL package distribution

Global and repository synchronization is a supervisor action after verification
and review. Discover installations without following symlinks or entering cache,
runtime, dependency or build trees. Read every target repository's instructions
and record that review before planning.

The plan command requires an external ownership registry:

```sh
python3 "$AL_PACKAGE/scripts/al_distribution.py" plan \
  --source "$AL_PACKAGE" --inventory "$INVENTORY" --reviews "$REVIEWS" \
  --platform codex --registry "$EXTERNAL_REGISTRY" \
  --output "$PLAN"
```

Use the platform-specific package and platform name for the other harness.
Registry and backup paths must be canonical, nonsymlinked and outside every
source package and affected repository. A first install without a registry owns
only the independently validated `result_owned` set: previously owned or
explicitly adopted desired files, plus files this transaction actually created
or replaced. Identical unmanaged files remain unowned. Existing registered
ownership and validated adoptions are additive: adopting a subset does not
relinquish previously registered files. For overlapping paths, retain the
existing local-variant rationale checks. Later plans can remove obsolete
resources only when their exact hash and mode are recorded in that target's
registry; deletion uses the normal backup and transaction journal.

For legacy targets, `--prior-managed FILE` accepts only an explicitly reviewed
bootstrap artifact tied to the preserved baseline manifest and target inventory.
It must identify ownership for each eligible file and explain any local variant.
Unrelated files remain unmanaged and are preserved. Do not infer ownership from
the target's current contents.

Apply validates the plan, writes a transaction journal and backs up changed or
deleted files before mutation. Role-file updates use the same transaction but
remain outside the package ownership registry. A failed or interrupted
transaction leaves pending state that requires manual recovery from its journal;
do not retry it destructively. Finish with `verify --manifest`; it checks file
hashes and modes, deletions, preserved local resources, registry generations and
transaction completion.

## Existing runtime pins

Installation and managed bootstrap select code for future runs. Existing runs
keep their immutable pins. Protected `alctl runtime-successor origin` inspects
an existing origin; it does not migrate it. Standalone pin migration is
unsupported. See [implementation-continuity.md](implementation-continuity.md#inspect-a-pin-and-choose-the-supported-route)
for inspection and a fresh standalone run from preserved accepted requirements.

For an existing **Harness execution**, use the trusted Harness operator, its
actual SQLite control store, current sealed approval/authority, and the repaired
managed AL launcher. Prepared successor initialization currently requires the
supported macOS process containment capability; unavailable containment refuses.
Keep a restorable control-store backup and all predecessor artifacts. Settle
owned writers/checks, managed or queued operations, browser work and uncertain
external deliveries through their supported lifecycle operations. Age, missing
PIDs or a retired assignment do not prove settlement.

Use arrays and quote each operand. Set `DB`, `TASK` and `EXECUTION` to the actual
existing Harness records, then read current `epoch_id` and
`authority_generation` from status. Use those exact predecessor values as
`EPOCH` and `GENERATION`; do not assume `legacy-0`/`0`:

```sh
HARNESS=(harness --db "$DB")
"${HARNESS[@]}" execution status "$TASK" "$EXECUTION"
"${HARNESS[@]}" execution runtime-prepare "$TASK" "$EXECUTION" \
  --expected-epoch "$EPOCH" --expected-authority-generation "$GENERATION" \
  --request-id "$REQUEST" --reason 'Repair runtime while preserving accepted requirements'
"${HARNESS[@]}" execution runtime-inspect "$TASK" "$EXECUTION" \
  --request-id "$REQUEST"
"${HARNESS[@]}" execution runtime-activate "$TASK" "$EXECUTION" \
  --request-id "$REQUEST" --preparation-sha256 "$PREPARATION_SHA256" \
  --expected-epoch "$EPOCH" --expected-authority-generation "$GENERATION"
```

Choose one stable request identity for this transition and keep it through
inspection/retries/activation. Inspect the PREPARED receipt and requirement
lineage; copy its exact `preparation_sha256` into `PREPARATION_SHA256`. Preparation
grants no product authority. Keep the same expected predecessor epoch/generation
for activation; changed identities or source/approval conditions must refuse.
Harness supplies the authenticated authority snapshot and supervised coordinator
exchange. Do not invoke low-level `prepare`/`activate` with invented file
descriptors, tokens or authority JSON.

Activation irreversibly fences the predecessor and selects a new epoch in both
the control store and protected issuer metadata. An interrupted transition must
roll forward the same recorded request; never restore the old binding or edit
state, pin, selector or snapshot files. Both operations return
`native_resumed: false`. After activation inspect current status, then explicitly
continue through the normal Harness path using the returned current identities.
The sealed task approval and accepted requirements remain. Old principals,
children, delayed events and technical approvals do not acquire the new epoch's
authority; verification debt remains. Required checks and any reusable claims
must be revalidated under the successor's actual source, scope and authority.
