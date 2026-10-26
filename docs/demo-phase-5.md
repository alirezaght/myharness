# Phase 5 isolated demonstration

Run the deterministic local workflow with the development environment:

```sh
.venv/bin/python scripts/demo_phase5.py
```

The demonstration creates a temporary repository and private state, then drives
the controller through a synthetic GitLab-shaped provider, an explicit browser
plan and approval, a fixture capability result, a fixture driver, and the final
report. It performs no provider HTTP request, does not start Chrome, and does not
contact the displayed `staging.fixture.invalid` target. Output identifies both
origins as `test_double`; the report leaves `live_acceptance` as `outstanding`.
The temporary state is removed at process exit.

`tests/test_demo_phase5.py` exercises this scenario. Separate provider tests use
the real Python HTTP client against a standard-library loopback fixture server;
they establish local transport, bounded retry, authentication stop and
single-dispatch behavior only. They are not live GitLab/GitHub evidence. The
production browser driver remains a separate marked browser test and requires
a safely usable Chrome installation.

This demonstration does not satisfy the Phase 5/V1 exit. That still requires an
explicitly approved plan executed against the actual staging deployment, with
current real-browser evidence and a final report.
