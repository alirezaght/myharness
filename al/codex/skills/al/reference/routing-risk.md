# Residual implementation risk

Use the same source-bound assessment in Harness and standalone AL. Harness: `harness classify TASK --assessment FILE --repo REPOSITORY`. AL: `classify SMALL|STANDARD|COMPLEX --designer no|yes --assessment-file FILE` with the ordinary implementer and reason arguments.

```json
{"schema_version":1,"scope":"local","existing_pattern":true,"behavior_clear":true,"hard_parts":[],"pseudocode_benefit":"","evidence":[{"path":"src/example.py","sha256":"CURRENT_SHA256","reason":"Existing implementation pattern inspected"}],"reason":"Local change follows the existing validation flow."}
```

`scope` is local, feature or cross_component. `hard_parts` is an explicit array, empty when no new difficult design remains. Each hard part has kind (algorithm, concurrency, transaction, state-machine, security, distributed or domain), new_design (boolean), and a specific description. COMPLEX needs a new hard part and a substantive pseudocode benefit. Existing difficult code alone is insufficient. Source evidence must be current regular repository files; hashes are SHA-256 of raw bytes. The example is a template, never evidence.

Harness records the assessment after raised material questions/decisions are resolved. It maps SMALL to simple interview depth, preserves developer overrides, exposes stale source/clarification and includes current evidence in the immutable AL handoff. A stale recorded assessment requires reassessment before handoff. Without one, initial keyword-free depth remains provisional and AL evaluates the task independently.

Examples: wording changes, one validation guard, and changing an existing retry policy parameter are SMALL; an ordinary feature using established components is STANDARD; designing new concurrent idempotency or transaction semantics with coupled invariants can be COMPLEX. A missing product choice is a clarification question. No target quota or automatic downgrade applies.
