# Service coverage and required checks

Gate discovery records whether AL can account for every service a change may affect and every required check for those services. The generic decision policy is independent of repository discovery; a bounded adapter supplies repository facts. The supported adapters are `python` (conventional Python projects and bounded `services/*` / `packages/*` layouts) and `amber` (the repository's manifest, workspace and sourced CI conventions). Other layouts remain explicitly incomplete until an adapter supports them.

The assessment has three outcomes:

- `identified`: dependency analysis found the affected services and their required checks. The selected set is the known affected set plus explicit policy selections.
- `exhaustive`: dependency analysis contains uncertainty, but service enumeration and check discovery are independently complete. AL selects every service in `potential_services` and requires every discovered binding.
- `incomplete`: enumeration or required-check discovery has gaps. The manifest records those gaps and an unresolved required coverage gate; phase admission, review and approval remain blocked.

Unknown or nonliteral dynamic imports add dependency uncertainty. A recognized literal external import can be resolved against declared version and lock information. Uncertainty by itself does not block a task when the complete potential universe and executable checks are proven. Opaque gitlinks and other opaque submodules, unsupported layouts, incomplete workspace membership, unreadable declarations, bounded-scan exhaustion, unsupported shared-language effects and missing or unrecognized checks remain named blockers.

Treat opaque repository boundaries as incomplete until the adapter can enumerate and verify their contents.

Completeness is not a manifest claim. Discovery independently observes repository membership, declarations, imports, source ownership, check commands and their inputs against the pinned base and current candidate. It records enumeration boundaries, observed and absent paths, source-chain hashes, scan limits/stability, dependency edges, potential services, check obligations, executable bindings and gaps. Admission re-collects these facts. A caller cannot turn a missing check into a complete inventory by editing `service_coverage` or adding a gate.

Each discovered executable check binds its literal `argv`, `cwd`, execution kind, environment names, declared source capabilities, required scope, source chain, `obligation_ids` and `execution_signature`. The manifest's `service_coverage.bindings` maps each obligation ID to its gate ID and signature. Optional `runner_resource` records a bundled checker name, version and SHA-256; the generic architecture runner is `al_architecture_check.py` version 1. Its fixed isolated invocation is part of the check identity. A nonzero result is failing verification evidence and prevents approval.

The generic adapter recognizes only reviewed, literal forms. Optional `[tool.al.architecture]` with a literal `forbidden` module list invokes the bundled real import checker. Supported `import-linter` declarations retain their actual command. Without either architecture policy, a service still needs a recognized sourced CI check with a real tool and target; an echo, print or directory probe does not prove verification. Supported generic project declarations include literal setuptools/uv source roots and finite conventional workspace membership. Dynamic workspace declarations, custom backends, `setup.py` discovery and unsupported command syntax remain gaps. The Amber adapter follows its exact sourced manifest/workspace/Makefile-to-helper-to-tool chain and records all consulted inputs.

Semantic coverage currency and execution evidence have different lifetimes. Body-only source changes may leave the dependency plan current, while ordinary check evidence is invalidated by changed bytes. Membership, import relationships, declarations, source roots, recipes and unsupported boundaries affect semantic coverage and require rediscovery when changed. Check the current evidence ledger for run-specific validation; this reference describes the contract and is not a current PASS claim.

Schemas: `schemas/service-coverage.json` and `schemas/gate-manifest.json`. The illustrative `examples/service-coverage.json` uses placeholder hashes that must be recomputed before validation.
