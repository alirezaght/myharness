# Service coverage and required checks

Gate discovery records whether AL can account for every service a change may affect and every required check for those services. The generic decision policy is independent of repository discovery; a bounded adapter supplies repository facts. The supported adapters are `python` (conventional Python projects and bounded `services/*` / `packages/*` layouts) and `contract` (a repository's own declared `.al/repository.json`, described below). Other layouts remain explicitly incomplete until an adapter supports them. No adapter recognizes a particular repository by name or by the digest of its code.

The assessment has three outcomes:

- `identified`: dependency analysis found the affected services and their required checks. The selected set is the known affected set plus explicit policy selections.
- `exhaustive`: dependency analysis contains uncertainty, but service enumeration and check discovery are independently complete. AL selects every service in `potential_services` and requires every discovered binding.
- `incomplete`: enumeration or required-check discovery has gaps. The manifest records those gaps as an UNRESOLVED coverage gate. Work continues: the gate is reported as unverified to the reviewer and in the final report, and every check AL can execute still runs.

Unknown or nonliteral dynamic imports add dependency uncertainty. A recognized literal external import can be resolved against declared version and lock information. Uncertainty by itself does not block a task when the complete potential universe and executable checks are proven. Opaque gitlinks and other opaque submodules, unsupported layouts, incomplete workspace membership, unreadable declarations, bounded-scan exhaustion, unsupported shared-language effects and missing or unrecognized checks remain named gaps, reported as unverified rather than blocking.

Treat opaque repository boundaries as incomplete until the adapter can enumerate and verify their contents.

Completeness is not a manifest claim. Discovery independently observes repository membership, declarations, imports, source ownership, check commands and their inputs against the pinned base and current candidate. It records enumeration boundaries, observed and absent paths, source-chain hashes, scan limits/stability, dependency edges, potential services, check obligations, executable bindings and gaps. Admission re-collects these facts. A caller cannot turn a missing check into a complete inventory by editing `service_coverage` or adding a gate.

Each discovered executable check binds its literal `argv`, `cwd`, execution kind, environment names, declared source capabilities, required scope, source chain, `obligation_ids` and `execution_signature`. The manifest's `service_coverage.bindings` maps each obligation ID to its gate ID and signature. Optional `runner_resource` records a bundled checker name, version and SHA-256; the generic architecture runner is `al_architecture_check.py` version 1. Its fixed isolated invocation is part of the check identity. A nonzero result is failing verification evidence and prevents approval.

The generic adapter recognizes only reviewed, literal forms. Optional `[tool.al.architecture]` with a literal `forbidden` module list invokes the bundled real import checker. Supported `import-linter` declarations retain their actual command. Without either architecture policy, a service still needs a recognized sourced CI check with a real tool and target; an echo, print or directory probe does not prove verification. Supported generic project declarations include literal setuptools/uv source roots and workspace membership, including `dir/*` and `dir/**` globs (and `!` exclusions) under `services/`, `packages/` or `apps/`, expanded to the directories that hold a manifest. Dynamic workspace declarations, custom backends, `setup.py` discovery and unsupported command syntax remain gaps. The contract adapter binds only the argv and hash-bound chain a repository declares in its own contract and records all consulted inputs.

Semantic coverage currency and execution evidence have different lifetimes. Body-only source changes may leave the dependency plan current, while ordinary check evidence is invalidated by changed bytes. Membership, import relationships, declarations, source roots, recipes and unsupported boundaries affect semantic coverage and require rediscovery when changed. Check the current evidence ledger for run-specific validation; this reference describes the contract and is not a current PASS claim.

Schemas: `schemas/service-coverage.json` and `schemas/gate-manifest.json`. The illustrative `examples/service-coverage.json` uses placeholder hashes that must be recomputed before validation.

## Declared repository contract

A repository opts into the `contract` adapter by committing `.al/repository.json`:

```json
{
  "schema_version": 1,
  "kind": "al-repository-contract-v1",
  "units": [{"path": "services/orders-service", "role": "orders", "language": "python"}],
  "containers": ["packages"],
  "architecture_check": {
    "argv": ["make", "lint-imports", "SVC={unit_name}"],
    "unit_name": {"strip_suffix": "-service"},
    "applies_to": "python-services",
    "chain": ["Makefile", "ci/scripts/check-architecture.sh"],
    "env_names": ["PATH", "UV_PYTHON"]
  },
  "selection": [{"changed": ["packages/**"], "selects": "python-services"}],
  "independent_languages": ["go"],
  "service_roots": ["services"]
}
```

- `units` are literal paths (optionally with a role and language); generic discovery still runs, and a declared unit that is absent is a `contract-unit-missing` gap. `containers` are never units; a declared container with its own manifest or `src/` is a `contract-container-has-source` gap.
- `service_roots` (default `["services"]`) names the literal directories whose Python units are services for the architecture check and selection.
- `architecture_check` binds every Python unit under a service root to the literal argv; only `{unit}` and `{unit_name}` placeholders exist, and a unit name that starts with `-` or contains braces is refused. The contract file, every `chain` file and the unit manifest are hashed into the check's source chain, so editing any of them changes the execution signature. A missing chain file or a unit name without the declared suffix is a blocking `unsupported-architecture-chain` gap.
- `selection` globs are evaluated by AL itself: a changed path matching a rule selects every Python service. Repository code is never read, executed or recognized as policy.
- Units whose languages are all in `independent_languages` and that declare a role are independent services; any other non-Python shared source is an `unsupported-shared-language` gap.
- Unknown fields, kinds or versions, non-literal values, absolute or `..` paths and unknown placeholders make the whole contract unusable (`unsupported-contract` gaps, so coverage is incomplete). Once a contract is in the base or recorded by an earlier manifest of the run, deleting it cannot downgrade the run to the generic adapter, and a worktree contract that differs from the pinned base copy (including one the base never had) is a blocking `contract-changed` gap: a task cannot redefine its own coverage, so contract changes land separately.

## Artifact consumers and relevant uncertainty

Changed data artifacts inside an enumerated unit are classified as `openapi-schema` (a JSON/YAML document with a top-level `openapi`/`swagger` key) or `data-fixture`; changed generated Python modules (an `@generated`, `DO NOT EDIT` or `Code generated` header) are `generated-code`. Configuration, manifests, hidden paths and rule sources keep their existing handling. An artifact's consumers are the source files that literally reference its file name or its directory (deliberately over-approximating on shared names, directory loads and globs); their units and the transitive importers of consumer modules are selected. Generated code records the schema files named in its header as producers, so a schema change reaches the generated module's importers. A shared artifact with no consumer reference is an explicit `uncertain-artifact-consumer` requirement (exhaustive selection); an artifact owned by a service with no outside reference is that service's input. Changed files of any other kind (for example `.csv`, `.sql` or templates) are unclassified and keep the exhaustive fallback.

Import-analysis uncertainty (`uncertain-import`) describes who may import a module. After the consumer closure it is retained in `deferred_uncertainties` (audit only, never selecting or blocking) only when it provably cannot reach an affected module: nothing importable is affected, or its literal target cannot name any affected module. An unknown target, an unclassified data change, non-Python source changes, unavailable analysis and incomplete enumeration or check discovery keep their exhaustive or blocking behaviour.
