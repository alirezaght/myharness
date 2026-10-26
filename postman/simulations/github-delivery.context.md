# GitHub delivery simulation

The synthetic service models repository/branch/ref reads and creation, pull
requests, Actions runs and attempt-specific jobs, check summaries, environments,
deployments and deployment statuses. Native routes and fields follow the
[GitHub REST API](https://docs.github.com/en/rest). No live account or workspace
writes are involved.

```sh
postman simulation run postman/simulations/github-delivery.sim.yaml --port 4901 --output ndjson
.venv/bin/python -m scripts.demo_phase4 --provider github
```

The caller uses `http://127.0.0.1:4901/github` as its instance URL, then appends
the Enterprise-style `/api/v3/repos/example/harness` route. Public GitHub uses
`https://api.github.com/repos/OWNER/REPOSITORY` in production.

Observed 2026-10-26: the actual GitHubClient passed branch and PR creation/read,
multiple workflow aggregation, jobs, check summaries, environment and latest
deployment checks. Changing one workflow to failed was detected even while
another succeeded. A rerun changed the attempt identity. A newer staging
deployment for another revision was reported as stale.

`POST /__test/mode` accepts synthetic `failure`, `attempt`, `deployment_sha` and
`deployment_status` values. The demo restores defaults after exercising faults.
Unknown routes return 501. Tokens are synthetic and only checked for presence;
this does not establish live permissions, branch policies or deployment timing.
Test counts are not fabricated from GitHub check annotations or job totals.
