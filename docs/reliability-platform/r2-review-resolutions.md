R2-FRESH-001: **fixed in the inspected repair**, for criteria R2-10/R2-06 and invariant R2-I2. No new concrete regression found in the three changed files.

**Source assessment**

At `/private/tmp/al-r2-frozen-review-002/harness/reliability/timeline_store.py:9`, the explicit mapping now selects:

- Run events: no payload reference.
- Agent/task/test events: validated `id`.
- Decision acknowledgement: validated `delivery_id`.
- Authored/invalidated output: validated `task_id`.

At line 97, selection uses only that exact field. Unrelated text, hashes and collections cannot override it. All currently supported semantic kinds are covered.

I traced these fields against `execution_contract.py`: the selected identifiers are validated for their respective kinds. The event’s `source_id` remains the validated envelope `event_id`; semantic links do not copy payload principal, role, parent, bundle or receipt fields. Existing protocol validation remains unchanged, preserving acceptance of otherwise permitted extra fields.

**Evidence assessment**

- `tests/test_reliability_timeline.py:368` drives actual ingestion with raw text, raw/prefixed hashes, dictionaries and lists. It checks safe references, original-payload preservation, identical replay and conflicting-replay refusal.
- `tests/test_reliability_timeline_api.py:150` exercises actual HTTP responses for text/hash disclosure and correct semantic provenance.
- Inspected archived XML and independently checked its hashes against the manifests: E22 records **32 failures / 34 passes**; E25 records **66 passes, zero failures/errors/skips** using the same recipe. The red failures demonstrate the original disclosure and collection-binding defects.
- Current ledger additionally records E26 **84 passes**, E27 **12 passes**, and valid E23/E24 lint/format checks.

**Remaining proof**

This closes the reported defect diagnostically; it is not formal AL approval. Broader regression, snapshot/migration, browser and mandatory CI/service qualification remain separate obligations. E20/E21 and older integration evidence are correctly marked stale; native skips confer no qualification. The repair prevents new unsafe projections; it does not sanitize previously persisted rows.

No tests, services, database connections or writes were performed during this reinspection.
