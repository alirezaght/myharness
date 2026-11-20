Read all three artifacts fully. No concrete protocol conflict found; the corrected partition/exporter design addresses the actual R5a boundaries.

Proposed ownership:

| Area | Concrete paths |
|---|---|
| Domain persistence | `harness/migrations/0024_standalone_incidents.sql`; `harness/reliability/{catalog,source_queries,investigations,investigation_contracts,grouping}.py`; new `standalone_catalog.py`, `standalone_investigations.py`, `repository_groups.py`, `domain_selection.py` |
| Collector | New `harness/reliability/{standalone_collection,export_storage,export_execution,export_contracts,export_qualification,export_channel}.py` |
| Protected exporter | `al/installer/{runtime_run,runtime_pin_boot,runtime_pin_transaction}.py`; new `runtime_incident_control.py` |
| Both AL distributions | `scripts/al_state.py`; new `al_incident_export.py`, `al_incident_snapshot.py`, `al_incident_rejections.py`; corresponding parity inventories |
| Integration/closure | `harness/dashboard/{incident_scope,incident_view,incident_actions,server}.py`; `harness/execution_bootstrap.py`; build-owned control source maps; narrowly necessary shared containment/helper extraction |
| Focused tests | New filenames below; `tests/test_db.py`, snapshot/manifest and existing control regressions |

Suggested initial managed recipes, all using `/private/tmp/al-reliability-verified-venv/bin/python -m pytest -q`, full dependency scope, unique001 basetemp/XML:

| Area | cwd | Test argv |
|---|---|---|
| `r5b-domain` | `.` | `tests/test_standalone_incident_catalog.py tests/test_standalone_incident_investigations.py tests/test_db.py` |
| `r5b-collector` | `.` | `tests/test_standalone_incident_collection.py tests/test_incident_export_control.py` |
| `r5b-export-codex` | `al/codex/skills/al` | `tests/test_incident_export.py tests/test_incident_rejections.py` |
| `r5b-export-claude` | `al/claude/skills/al` | `tests/test_incident_export.py tests/test_incident_rejections.py` |
| `r5b-installer-export` | `.` | `al/tests/test_incident_export.py al/tests/test_runtime_run.py al/tests/test_runtime_pin.py` |
| `r5b-api` | `.` | `tests/test_standalone_incident_api.py tests/test_incident_api.py` |
| `r5b-source-closure` | `.` | `tests/test_execution_snapshot.py al/tests/test_manifest.py al/tests/test_policy_retry_control.py tests/test_policy_retry_control.py al/codex/skills/al/tests/test_parity.py` |

Each recipe should append `--basetemp=/private/tmp/al-<area>-001 --junitxml=/private/tmp/al-<area>-001.xml`. New selectors must be authored before execution.

The concrete remaining implementation issue is qualification, not a product decision: current `runtime_retry_control.activate()` accepts only retry/finish; `control_boundary.prepare()` requires a real Harness operation; ordinary `al_state` dispatch invokes truncating `al_store.locked()` and mutation-capable `load()`. R5b therefore needs its dedicated observational bootstrap, standalone profile/channel and fixed repository-identity helper. Reusing ordinary dispatch or fabricating an Attempt would violate the accepted design.

No edits, checks, services, database connections or native launches performed.
