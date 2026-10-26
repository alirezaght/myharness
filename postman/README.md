# GitLab delivery API assets

These assets model the external GitLab REST API subset needed by Phase 4.
The API paths, request names and response fields are derived from the official
[Projects](https://docs.gitlab.com/api/projects/),
[Branches](https://docs.gitlab.com/api/branches/),
[Merge requests](https://docs.gitlab.com/api/merge_requests/),
[Pipelines](https://docs.gitlab.com/api/pipelines/),
[Jobs](https://docs.gitlab.com/api/jobs/),
[Environments](https://docs.gitlab.com/api/environments/),
[Deployments](https://docs.gitlab.com/api/deployments/) and
[Authentication](https://docs.gitlab.com/api/rest/authentication/) docs.
The owner's workspace mock search was skipped because no GitLab owner workspace
was identified and this stage does not access Postman workspaces.

## Contents

- `gitlab-contract.yaml` — OpenAPI subset for project, branch, MR, pipeline,
  jobs, test report, environments and deployments.
- `fixtures/gitlab/` — synthetic example responses; they contain no real
  project or account data.
- `mocks/gitlab/` — local stateful handler. It models branch and MR creation,
  then read/reconciliation. `pm.state` stores synthetic records under `gitlab_data`.
- `simulations/gitlab-delivery.sim.yaml` and adjacent context — local caller
  simulation and run notes.

## Local behavior

The mock requires `PRIVATE-TOKEN` or `Authorization: Bearer …`; it never
validates a token. Project path is the encoded
`example-group%2Fmy-project` (or numeric fixture ID `731`). Branch and MR
creation persist within the mock session. An open MR with the same exact
source and target branch pair returns `409`, allowing a caller to list and
reconcile after an uncertain POST outcome. Unknown paths return `501` so an
unmodeled request is visible as missing coverage. `POST /__test/reset` clears
the mock's `gitlab_` state for a fresh test run.

Run the local simulation, then exercise the real client from a second terminal:

```sh
postman simulation run postman/simulations/gitlab-delivery.sim.yaml --port 4900 --output ndjson
.venv/bin/python -m scripts.demo_phase4
```

The executed demo checks projects, branches, MR creation/read/reconciliation,
duplicate refusal, pipelines, jobs, reports, environment details and deployments.
It uses a synthetic local token. See `simulations/gitlab-delivery.context.md`
for scope and observations. No live GitLab permission or deployment claim is made.

## GitHub delivery

The independent GitHub simulation exercises PRs, Actions, check summaries and
latest deployment/status reads. It uses only synthetic local credentials:

```sh
postman simulation run postman/simulations/github-delivery.sim.yaml --port 4901 --output ndjson
.venv/bin/python -m scripts.demo_phase4 --provider github
```

The executed demo also changes one workflow to failed, starts a new attempt,
and makes staging stale, then restores the default synthetic scenario. It does
not prove live GitHub permissions or repository-specific required-check policy.
Native request paths follow the official [GitHub REST documentation](https://docs.github.com/en/rest).
