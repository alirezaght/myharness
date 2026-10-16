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
