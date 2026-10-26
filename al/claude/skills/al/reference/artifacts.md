# Supporting artifacts and named agent outputs

Use the protected managed launcher for the same repository, platform and AL
session on every command. `STATE` in these examples is an argv array, not a
space-separated command string:

```sh
STATE=("$HOME/.local/bin/alctl" run --repository "$repo" --platform codex --session "$session" --existing-run --)
"${STATE[@]}" artifact record --file /tmp/consumer-discovery.md --kind consumer-discovery
```

`artifact record` copies the exact supporting bytes into the run's immutable
artifact store and prints the reference JSON. It does not claim a test passed,
grant approval, prove authorship, or bypass planning/proof admission. Inputs must
be ordinary single-link files, at most 16 MiB. JSON and Markdown are both allowed.

Every `discovery_refs` and `source_refs` entry has exactly these six fields:

```json
{
  "id": "<64 lowercase hex characters>",
  "kind": "consumer-discovery",
  "sha256": "<same hash as id>",
  "stored_path": "<absolute immutable stored path returned by artifact record>",
  "source": "<source description returned by artifact record>",
  "captured_at": 1234567890.0
}
```

Copy the returned object; do not hand-build references. The verifier checks the
file bytes against the hash. A behavior consumer is exactly:

```json
{
  "id": "delivery-dashboard",
  "role": "consumer",
  "kind": "code",
  "contract": "Delivery snapshot read by the dashboard",
  "locations": ["harness/dashboard/view.py"],
  "source_refs": ["<replace this string with the returned reference object>"]
}
```

`role` is `producer` or `consumer`; `kind` is `code`, `link`, `message`, `export`,
or `other`. Put discovery artifacts in the inventory's `discovery_refs` and
supporting source artifacts in each entry's `source_refs`. Record uncertainties
honestly; unresolved discovery still prevents admission.

## Named output files

The supervisor should provide a unique output name in each agent's handoff.
Agents may author only their designated report file outside the source tree;
this reporting permission does not authorize implementation edits by read-only
roles. When direct writes are unavailable, use the Harness `submit_output` tool
or the host's final-message file capture. Do not search arbitrary transcript
text for a report; a prompt or tool message is not the final agent output.

```sh
"${STATE[@]}" artifact output --name architect-resolution.json --file /tmp/architect-resolution.json
"${STATE[@]}" artifact show --name architect-resolution.json
```

`artifact output --stdin` also accepts a bounded report from standard input.
The command creates a private named version file and returns `name`, `path`, and
the six-field `artifact` reference. Old versions remain immutable. Replacing a
name requires its current `--expected-sha256`; repeating identical bytes is
idempotent. These are reporting artifacts, never automatic evidence or approval.
