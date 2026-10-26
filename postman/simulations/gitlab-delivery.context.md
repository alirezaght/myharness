# gitlab-delivery

Generated 2026-10-26. Contract based on official GitLab REST API docs; local
mock handler and fixtures are synthetic. No GitLab workspace mock was checked:
no owner workspace was identified and this stage has no workspace access.

## Purpose

Stand in for the GitLab project, branch, merge request, pipeline, job, test
report, environment and deployment endpoints used by Phase 4 delivery work.
Routes and synthetic records are in `postman/gitlab-contract.yaml`,
`postman/mocks/gitlab/` and `postman/fixtures/gitlab/`.

## Execution

- Start: `postman simulation run postman/simulations/gitlab-delivery.sim.yaml --port 4900 --output ndjson`
- Start the real caller with its GitLab API base URL changed to
  `http://127.0.0.1:4900/gitlab` (the client appends `/api/v4`).
- Prove it is ready: run `.venv/bin/python -m scripts.demo_phase4`; require `status: passed`.
- Count calls: inspect `request` events in the NDJSON output. Capture logs by redirecting the simulation output to a chosen NDJSON file.
- Stop with Ctrl-C.

## Assumptions

- **The mock accepts either `PRIVATE-TOKEN` or Bearer authentication.** Missing
  credentials receive synthetic `401`; this does not establish permissions or
  auth behavior for any particular GitLab instance. (reasoned 2026-10-26,
  official GitLab authentication documentation)
- **A timed-out create request may have committed remotely.** Reconcile branch
  identity or exact source/target MR identity before retrying; the mock cannot
  reproduce ambiguous network completion. (reasoned 2026-10-26, API operation
  is a POST; not verified against a live instance)
- **A new MR gets no automatically created pipeline in this mock.** The seeded
  MR IID 14 has current and older candidate pipelines, jobs and test results.
  (synthetic fixture design, 2026-10-26)
- **This simulation cannot establish live project permissions, pagination
  completeness beyond the modeled pages, pipeline timing, or staging
  deployment policy.** Check these against a non-production GitLab project
  before relying on them. (reasoned 2026-10-26, simulation scope)

## Observations

<!-- begin:observations -->
2026-10-26: real GitLabClient demo passed project, branch, MR creation/read/list, duplicate 409, pipeline/jobs/report, environment details and deployment checks against the local simulation.
<!-- end:observations -->

## What didn't work

Initial authoring used an invalid simulation manifest, handler entry point and state API. Runtime execution exposed these; the checked-in manifests use version 1, scenario paths, an HTTP server and asynchronous `pm.state.get/set`. The repeated demo passes.
