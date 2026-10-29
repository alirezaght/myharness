# Dashboard recovery and status guide

## Read implementation progress

Once execution exists, its current work appears before the approval history.
**What is happening** separates the running agent's reported work, recent guarded
tool activity with timestamps, recorded reports, and the latest reported check
results. Tool arguments, source text and command output are not included in this
activity projection. Observations belong to known agents in the selected epoch.

**Verified completion** counts completed task units. A single registered task can
cover substantial implementation, so `0 of 1` is not an effort estimate. Completed
agent runs and saved reports are shown separately. A running check is labeled as
reported running until a result arrives; a quiet period is explicitly shown.
The status feed being current does not establish that work is advancing.

## Start the dashboard with operator permissions

Run the public `harness dashboard` command from a trusted, ordinary terminal session that can access the existing Harness project state. A dashboard started inside a restricted native agent may still show read-only task data while its process cannot open the worker lock, Registry lock, decision destination, or inbox. The dashboard checks those destinations and shows which operator actions are unavailable. Restart the public dashboard command from the trusted terminal after a permission failure; do not change native-agent containment or remove a lock file.

Permission checks describe the dashboard operator's filesystem access. They do not establish that native discovery, attribution, implementation, or AL evidence is ready. A live worker-lock owner can allow a status refresh while preventing a competing continuation.

## Refresh status or continue the native session

- **Refresh recorded status** reconnects to the saved execution and replays recorded state. It does not continue a native model session. It remains available when a live worker owns the execution lock, because replay is observational.
- **Continue agent in existing session** explicitly requests continuation of the recorded native conversation. The existing execution identity, task binding, lock checks, and uncertain-process checks still apply. It never starts automatically after a page refresh or decision delivery.

An agent process that exits with code zero without a guarded AL implementation result remains incomplete. If validated source-output receipts exist, the dashboard shows the recorded source work and pending verification. Otherwise it reports “The agent stopped without a verified implementation result.” A zero process exit is not a product result.

If a task registration is terminal, it cannot be reopened. New work needs a fresh actual native invocation, its observed child identity, and a valid task binding. Keep the current AL run, approval, execution, and native conversation identities intact when reconnecting.

## Retry a saved decision

The decision form saves a request before delivery. A failed saved request shows **Retry saved decision delivery**. The retry sends only the task, execution, and stored delivery identity; Harness retrieves the frozen request from its existing record. It does not rebuild text from the UI, change the user's wording, or create a second request. Duplicate submissions are disabled while one retry is in flight.

The dashboard distinguishes saved, delivery failed or pending, delivered awaiting AL acknowledgment, and acknowledged. An acknowledgment is the recorded AL outcome; it does not prove a filesystem permission repair or discovery coverage. For example, the saved statement “I will fix harness myself.” records the developer's intent, not proof that the environment was fixed. Failed probes and unsupported capabilities remain separate and visible.

If a new decision cannot pass the operator preflight, the dashboard reports that before creating a new saved intent. Existing failed intents remain available for exact-request retry after the operator restarts the dashboard with access to the same state.

## Understand diagrams

The diagrams panel reads structured design notation. A prose-only design can contain useful architecture decisions while producing no diagram. This does not mean the task is waiting for AL planning, and AL planning prose is not a diagram source.

Pending notation proposals are shown separately from effective diagram versions. A proposal must be explicitly accepted with the existing `harness design decide` workflow before it becomes an effective version. In a design phase, add notation with `harness design propose`, then review and accept it. In a later or sealed phase, use the task's explicit design-revision workflow; it changes phase and requires the corresponding review and approval steps. Merely opening or refreshing the dashboard never changes a specification, phase, or design decision.

## Classify the exact template versions

The current inventory contains ten `.env.example` template versions with matching baseline, `HEAD`, index, and worktree identities. The user's classification context was: “.env files have staging secrets, and .example files is not important at all”. It applies only to those ten exact inventoried versions. It does not classify real `.env` files, other examples, future versions, or newly discovered candidate paths.

A trusted operator can register that classification for the existing execution with the tested CLI form:

```sh
harness --db "$DB" execution classify-templates "$TASK" "$EXECUTION" \
  --inventory "$INVENTORY_JSON" --inventory-sha256 "$INVENTORY_SHA256" \
  --classification "$CLASSIFICATION_JSON" \
  --classification-sha256 "$CLASSIFICATION_SHA256"
```

The classification JSON has exactly four nonempty string fields: `question`, `answer`, `inventory_sha256`, and `source`. Its `inventory_sha256` must match the supplied inventory digest. Preserve the original inventory, including its `UNATTESTED` statuses; the separate operator record binds the classification to the repository, execution, AL run, pin, and each exact path, mode, blob identity, and content digest. Register it through the trusted Harness CLI, not from native-agent artifact tools.

If any classified template is added, removed, renamed, aliased, or changes bytes or mode, the old record is stale. Generate a new inventory and obtain a classification for the new exact versions. Unknown protected candidates fail closed; a `.example` suffix is not a general exemption. The narrowly validated template grant does not make arbitrary native `read_file` or `run_command` access broader.

Real `.env` files contain staging secrets. Keep them protected and undisclosed: do not read, print, copy into reports, or include their contents in screenshots. This classification permits only the exact checked template bytes required by the supported attribution and check path; it does not grant access to staging secret files.

## Recover the same execution

After the supported Harness bridge is installed, the trusted operator can request the existing bridge upgrade for the same task and execution:

```sh
harness --db "$DB" execution upgrade-bridge "$TASK" "$EXECUTION" \
  --reason 'Repair supported dashboard discovery and recovery'
```

The bridge upgrade is subject to its existing approval, settled-attempt, writer, and check guards. It preserves the existing AL run, immutable pin, native conversation, and proof history. It does not itself establish complete discovery, successful attribution, a verified implementation result, or AL approval. Register the exact template classification only after the execution's existing lifecycle checks permit it, then refresh recorded status and inspect the actual same-run evidence.

**Refresh recorded status** only replays recorded state. **Continue agent in existing session** explicitly resumes the stored conversation and does not repair operator permissions. A terminal child registration cannot be reopened or assigned a guessed identity. Any new work requires a fresh actual native invocation, its observed child identity, and a valid task binding; keep the existing AL run and parent conversation.

Read-only command output is paged. Drain every response while `more` is true before treating the captured output as complete. Oversized unfinished lines are explicitly suppressed through their terminator; do not interpret that marker as complete file content. Keep real secret payloads out of logs and reports.

## Upgrade the runtime of an existing execution

A bridge repair preserves the pinned runtime. Installing a new global AL version does not change that pin. To repair runtime policy, use the public successor commands after installing the reviewed Harness and AL versions. Keep a restorable SQLite backup and retain the predecessor runtime and evidence.

Prepared successor initialization currently supports the macOS process sandbox. Other platforms report an unsupported containment capability; the command never falls back to an uncontained start. CI runs the complete release verification on macOS and the ordinary Harness checks on both macOS and Linux.

Read the current `epoch_id` and `authority_generation` from `execution status`. For an original execution these are `legacy-0` and `0`. Use one stable request ID throughout preparation, inspection and activation:

```sh
harness --db "$DB" execution runtime-prepare "$TASK" "$EXECUTION" \
  --expected-epoch "$EPOCH" --expected-authority-generation "$GENERATION" \
  --request-id "$REQUEST" --reason 'Repair runtime continuity for the approved task'

harness --db "$DB" execution runtime-inspect "$TASK" "$EXECUTION" \
  --request-id "$REQUEST"

harness --db "$DB" execution runtime-activate "$TASK" "$EXECUTION" \
  --request-id "$REQUEST" --preparation-sha256 "$PREPARATION_SHA256" \
  --expected-epoch "$EPOCH" --expected-authority-generation "$GENERATION"
```

Inspect the exact preparation digest and retained requirement lineage before activation. Preparation grants no product-write authority. Activation irreversibly fences the predecessor, selects the successor in SQLite and publishes the protected issuer selector. If interrupted, roll forward the same recorded request; do not restore the predecessor binding or edit runtime state files.

The execution, approval, original specification, accepted amendments and native parent conversation remain. The AL session/run and authority epoch change. Old agents, delivery acknowledgments, writer authority and technical approvals do not transfer. Their evidence remains historical; incomplete checks remain verification debt. Only material specification or engineering choices need a developer decision. Legacy untyped design forms remain available in their original epoch; new runtime blockers must declare their decision kind, and tooling uses operational issues.

Preparation and activation both return `native_resumed: false`. After activation, explicitly continue the existing native session with the returned epoch and authority generation. The continuation prompt supplies the new runtime identifiers and requires fresh observed child invocations. An old task assignment or cached principal cannot acquire successor authority. When replacing a settled child in the same epoch, use `agent-register` with `replaces` and `release_reason`; the release records capability revocation, not proof that the native host terminated.

Reload the dashboard after installing new static assets. Ordinary live status updates preserve scroll position, open disclosures and decision drafts within the same execution epoch. Crossing an epoch discards old action state so it cannot be submitted to the successor.
