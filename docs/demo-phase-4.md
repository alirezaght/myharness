# Phase 4 delivery and command safety

Harness publishes an explicitly authorized, committed AL candidate to GitHub or GitLab,
records the pull/merge request, and monitors that candidate's pipeline and staging
deployment. It never merges the request. Dashboard reads only display persisted
observations; they do not publish or retry work.

Configure an approved execution with a JSON file containing `endpoint`, `project`,
`source_branch`, `target_branch`, `staging_environment` and `credential_env`.
Set `provider` to `github` or `gitlab` (legacy default). The endpoint is the instance web URL, such as `https://github.com` or `https://gitlab.com`, without API suffixes. GitHub Enterprise uses its `/api/v3` API; GitLab uses `/api/v4`. Credentials remain in
the referenced environment variable (`GITHUB_TOKEN` or `GITLAB_TOKEN` by default). GitHub can also reuse an existing `gh auth` login when the variable is absent. Set `publication_authorized: true` only when
external publication is authorized; `upload_authorized` separately permits Git
upload when the candidate is missing remotely. AL's exact-revision publication
authorization and checks must also be current. Source and target branches must
differ. Configuration is immutable once recorded.

```sh
uv run harness delivery configure TASK EXECUTION --file delivery.json
uv run harness delivery publish TASK EXECUTION
uv run harness delivery watch TASK EXECUTION
uv run harness delivery status TASK EXECUTION
```

A transport failure during a create leaves an uncertain durable intent. Use
`delivery reconcile TASK EXECUTION` to inspect exact remote identities before
retrying. Existing source branches are never overwritten with a force push.
The upload uses Git over HTTPS authentication separately from REST authentication,
following [GitLab's token documentation](https://docs.gitlab.com/user/profile/personal_access_tokens/).

Known technical failures enter a bounded correction generation of the same AL
run and native conversation. The previous completion is retained. AL must plan,
register a writer, verify and review the correction again. Unknown failures,
design decisions and failed merged candidates require developer review. Exhausted
retries or monitoring limits report Blocked. Staging readiness requires the current
candidate and successful latest deployment in an available environment, using provider environment and deployment observations.

Native Claude and Codex retain their existing signed-in authentication locations.
Their restricted tool catalogs route effects through the Harness controller.
Each operation checks the current approval, native identity, AL task revision and
writer scope. Checks run in separate OS sandboxes and cannot write the AL ledger.
Native login material is excluded from operation environments and filesystem access.
Unsupported native versions or authentication configuration fail explicitly.

Structured diagnostics classify effects before execution. Unknown or mutating
commands need exact approval. SQLite diagnostics use read-only connections,
authorizers and resource bounds; a leading SELECT does not grant permission.
Operation audit records and streamed diagnostics redact secrets.

## Repeatable local demonstration

Run the [Postman simulation](../postman/README.md), then:

```sh
.venv/bin/python -m scripts.demo_phase4 --provider gitlab
.venv/bin/python -m scripts.demo_phase4 --provider github
.venv/bin/pytest tests/test_delivery_monitor.py tests/test_phase4_core_safety.py -q
.venv/bin/pytest al/codex/skills/al/tests/test_execution_corrections.py -q
```

The client demo exercises real HTTP transport with synthetic GitHub/GitLab records.
The correction acceptance uses real guarded AL admission, a scripted engineer's
registered repair, fresh verification/review, replay and bounded final Blocked
status. It does not spend provider tokens or require live GitLab credentials.
The dashboard browser tests cover desktop/mobile delivery views, keyboard focus,
overflow and visual alignment. Live signed-in start/resume checks are opt-in via
`HARNESS_LIVE_NATIVE=1` for `tests/test_execution_operations.py`.

This demonstration establishes local behavior. Live project permissions, runner
configuration and staging deployment policy require a configured provider project.

GitHub Actions readiness aggregates the latest run attempt of each observed
workflow for the exact head SHA. A failure in one workflow cannot be hidden by
another workflow's success. GitHub check summaries appear in the delivery panel;
structured test counts depend on the repository's reporter and are not inferred
from annotation counts. Staging uses the latest deployment and its latest status,
including inactive/failed states. See the official [pull request](https://docs.github.com/en/rest/pulls/pulls),
[workflow run](https://docs.github.com/en/rest/actions/workflow-runs), and
[deployment status](https://docs.github.com/en/rest/deployments/statuses) APIs.
Provider permissions and required-check policies remain repository-specific.

Linux operation isolation requires bubblewrap and usable unprivileged user/network namespaces. The dedicated containment workflow requires those checks; ordinary portable runs explicitly skip missing host capabilities. The older whole-process compatibility probes are macOS-specific. Failed production capability admission never falls back to unrestricted execution.
