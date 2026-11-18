# Versioned guarded pytest outcomes

`strict-v1` remains the default when the repository has no outcome-policy declaration.
Every collected mandatory node requires exactly one passed setup, call and teardown;
skipped and expected-failure outcomes are insufficient. Historical strict receipts
retain their original policy interpretation. Their hashes and pinned runtime bytes
are never rewritten; a current guard-resource mismatch makes reuse stale.

The reviewed `platform-aware-v1` implementation is selected by source bytes in
`pyproject.toml`, for example:

```toml
[tool.al.pytest-verification]
policy = "platform-aware-v1"
platforms = ["linux", "darwin"]
```

This declaration does not itself grant verification credit. AL's owned gate launcher
supplies the current task revision, run, gate manifest, gate, stage, base, candidate
and invocation. The guard additionally binds test/helper/fixture/configuration bytes,
collection identities, declared marker expression and deselections, lock and actual
plugin profile, trusted guard resource closure, interpreter bytes and observed OS.
The policy declaration participates in the execution signature and source snapshot.
Changed policy/resources/configuration/collection invalidate current proof reuse.

Initially only direct unaliased `pytest.mark.skipif` declarations with literal
`sys.platform ==/!= "linux"/"darwin"/"win32"` or `os.name ==/!= "posix"/"nt"`
conditions qualify. Module `pytestmark`, class decorators and test decorators are
supported. The guard parses AST and matches the entire runtime skip marker list;
it never evaluates source expressions. A skipped setup with passed teardown may
be NOT_APPLICABLE only when this source proof shows that node cannot apply on the
observed platform. NOT_APPLICABLE is counted separately from PASS.

Aliases, strings, dynamic predicates, missing native tools/providers, unknown OS
values, arbitrary skips and expected failures remain UNVERIFIED. Missing/duplicate
stages or identities, failures, source/fixture/plugin drift and incomplete collection
cannot qualify. The versioned collection proof records every actual collector outcome
and finalization, and validates the complete recursive collector graph from pytest's
session root. Every child must resolve to a reported collector or an accounted test
item; final item leaves must equal selected plus declared marker-deselected nodes.
Missing sibling collectors, unaccounted leaves, orphan reports, duplicate edges and
cycles cannot qualify. The session identity `""` and root-directory identity `"."`
remain distinct; genuinely reported empty collectors remain valid.
A module-level `pytest.skip(..., allow_module_level=True)` or `pytest.importorskip`
is an unsupported collection skip, even when another module passes. It remains
UNVERIFIED/INFRA, never NOT_APPLICABLE. New platform-aware admission refuses missing,
unfinished, skipped, failed or inconsistent collection proof; a survivor-only legacy
report cannot establish complete collection. Historical strict receipts retain their
original versioned interpretation. Only successful actual guarded execution proves an applicable pair.

The report contains every node/platform obligation. Unobserved required platforms
remain UNVERIFIED; no other-platform execution is inferred. This implementation
can fully qualify only a matrix whose every pair has actually been observed by the
owned guard. Cross-host guard-proof transport/aggregation is not supplied here;
a multi-platform declaration therefore remains incomplete in one local execution.
CI green, ordinary JUnit, counts, agent statements and arbitrary report JSON are
never guard attestations. The GitHub adapter remains CI_JOB_ATTESTATION only and
retains INFRA when actual guarded proof is unavailable.

Implementation and testing do not activate a running pin. Activation requires
independent review and the supported versioned release/install workflow. Existing
runtime pins stay immutable. This policy grants no task approval, tools, providers,
network/secret authority or business/scientific acceptance changes.
