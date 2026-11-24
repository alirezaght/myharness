# Service coverage and required checks

Gate discovery records whether AL can account for every service a change may affect and every required check for those services. The generic decision policy is independent of repository discovery; a bounded adapter supplies repository facts. The supported adapters are `python` (conventional Python projects and bounded `services/*` / `packages/*` layouts) and `contract` (a repository's own declared `.al/repository.json`, described below). Other layouts remain explicitly incomplete until an adapter supports them. No adapter recognizes a particular repository by name or by the digest of its code.

The assessment has three outcomes:

- `identified`: dependency analysis found the affected services and their required checks. The selected set is the known affected set plus explicit policy selections.
- `exhaustive`: dependency uncertainty remains, but service enumeration and check discovery are complete. Select the whole potential-service universe and require executable bindings for every mandatory check.
- `incomplete`: relevant enumeration, dependency/consumer inventory or required-check discovery has named gaps. Diagnostic capture and scoped implementation remain available. The gaps are verification obligations; required final proof, review approval and verified completion remain unavailable until those obligations are satisfied.

Unknown or nonliteral dynamic imports add dependency uncertainty. A recognized literal external import can be resolved against declared version and lock information. Uncertainty alone does not block when complete potential coverage and executable checks are proven. Proven unrelated uncertainties remain auditable deferred entries. Opaque boundaries, unsupported layouts, incomplete workspace membership, unreadable declarations, bounded-scan exhaustion, unsupported shared-language effects and missing mandatory bindings remain verification obligations. A concrete extra enumerated check may stay individually unverified only when mandatory coverage is complete; it cannot satisfy a required binding or conceal an unknown inventory. A service whose only sourced verification is a CI job AL cannot run locally is pipeline-verified instead: discovery records it under `checks.pipeline_verified` rather than as a `missing-verification-check` gap, and it is listed as unverified, never blocking (see gates: Pipeline-verified CI jobs).

Discovery prioritizes tracked/base/explicit/declared sources and package manifests, including apps/web. Supported generated/cache exclusions record provenance; they cannot erase protected source. Unknown nonignored artifacts consume bounded scan budgets and overflow remains incomplete. Changed membership or exclusion/ignore rules invalidate coverage currency.

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

Changed data artifacts, including unowned root artifacts, are classified as `openapi-schema` (a JSON/YAML document with a top-level `openapi`/`swagger` key) or `data-fixture`; changed generated Python modules (an `@generated`, `DO NOT EDIT` or `Code generated` header) are `generated-code`. Configuration, manifests, hidden paths and rule sources keep their existing handling. An artifact's consumers are the source files that literally reference its file name or its directory (deliberately over-approximating on shared names, directory loads and globs); their units and the transitive importers of consumer modules are selected. Generated code records the schema files named in its header as producers, so a schema change reaches the generated module's importers. A shared artifact with no consumer reference is an explicit `uncertain-artifact-consumer` requirement (exhaustive selection); an artifact owned by a service with no outside reference is that service's input. Changed files of any other kind (for example `.csv`, `.sql` or templates) are unclassified and keep the exhaustive fallback.

Import-analysis uncertainty (`uncertain-import`) describes who may import a module. After the consumer closure it is retained in `deferred_uncertainties` (audit only, never selecting or blocking) only when it provably cannot reach an affected module: nothing importable is affected, or its literal target cannot name any affected module. An unknown target, an unclassified data change, non-Python source changes, unavailable analysis and incomplete enumeration or check discovery keep their exhaustive selection or incomplete-verification behaviour.


## Task and global inventory in policy v2

Global diagnostics remain visible in the declaration even when a captured exclusion proves them
unrelated to the current task. The task assessment alone selects required units, gaps and
execution bindings. Missing checks in an untouched independent unit do not automatically block
another task; unknown relationships that could conceal a consumer remain conservative.
Check-discovery gaps recorded for one unit's own manifest or policy (missing checks,
pending or unverified package execution, nonliteral package scripts, incomplete declared
architecture chains) are task obligations only when that unit is selected or touched.
A literal runtime consumer outside the unit layout belongs to no service: the units whose
sources literally reference it are selected, and its `unclassified-runtime-consumer`
diagnostic is a task obligation only when the task touches the consumer itself. A consumer
referenced by another unowned file keeps the conservative uncertainty fallback.

Unverified checks and unreachable structure are then scoped to the change's reach: its
changed, anticipated and selected-directory paths, the literal dependency closure (the
dependency analysis's affected source paths), the units owning any of them, and the literally
affected and policy-selected units. A unit's own check-discovery gap (including a missing
check binding) is in scope only when that unit is reached. A symlinked directory, gitlink or
opaque boundary is in scope only when a reached path lies in it, a governing rule names it or
a reached path or unit literally consumes it. An `unclassified-unit` directory, an
`unknown-ci-inventory` CI definition and an `unsupported-ci-provider` definition are in scope
only when the change touches them. Dependency uncertainty still selects every potential
service for the checks that run (`exhaustive`), but a gap in a unit reached only through that
uncertainty is an unverified check, not a safety gap. Out-of-scope gaps are deferred as
`outside-change-reach`, listed in the assessment's `coverage_notes` (and `gates status`
`coverage_notes`) and never block admission, final proof, review or approval. Layout,
manifest and contract gaps that leave a unit's imports unknown could hide a consumer and keep
their conservative relevance; torn captures, opaque governing rules and repository-wide gaps
of unknown extent always stay in scope. A refusal names each in-scope gap with its repair and
the rediscovery commands (`gates discover --extend current --paths-file <file containing []>`
and `gates plan --file <manifest>`).

Unconsumed narrative documentation has no generic runtime-data fallback. This is not a `docs/**`
exclusion: actual prompt/resource consumers, source code, schemas, unknown data, service-owned
inputs and governing policy still participate in dependency selection. A new consumer invalidates
an old exclusion and requires replanning.

Opaque gitlinks and symlinks are observed as metadata without following targets. Their diagnostics
are task-relevant when changed, referenced, governing or inside a requested enumeration boundary.
Expanding one directory does not certify complete root membership or grant writer permission.

Conditional GitLab includes retain bounded alternative definition worlds. A job is deferred only
when every possible present definition is source-provably disjoint or excluded. Unknown include
universes, malformed inheritance, unsupported execution context and variant overflow remain open.
Unbound `changes.compare_to` cannot borrow ordinary task paths; an earlier definite MR arm may
legitimately make it unreachable. No ambient environment or guessed baseline supplies authority.
Captured directory scopes retain possible future descendant checks in GitLab and GitHub. Only
semantic currency removes descendant paths already covered by a positively captured directory;
raw questions and byte-sensitive evidence remain intact. A concrete job whose execution cannot
be mapped remains required and reported as `not verified` under the existing gate policy.
Recognized identity does not supply a passing check or change approval authority.
An unavailable root CI definition remains unknown inventory and prevents coverage admission.
Its internal unavailable reference carries metadata only: it cannot authorize execution, waive
an obligation or claim observed content. Alias targets are not read. Conventional containers
are enumerated only after a no-follow observation proves an ordinary directory; opaque roots
are excluded literally from untracked Git queries and retain only boundary diagnostics.

Version 2 binds one coherent capture of repository facts, CI, instruction/configuration inputs,
include/workflow membership, absences and boundary metadata. A real drift discards the whole
attempt and permits one fresh attempt. Repeated drift refuses; permissions, syntax and limits
are not drift retries. Independent reader dependencies must match the enclosing capture, while
existing closed pytest runtime-resource identities retain their separate authority binding.

Version 1 declarations remain structurally readable under v1 semantics. Using policy v2 for
current admission requires actual replacement discovery and planning. No old manifest, proof,
evidence or approval is rewritten by this transition. Global completeness, task completeness,
recognized job identity, executable mapping and passing verification remain separate claims.
