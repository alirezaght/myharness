# Native CLI compatibility

Harness accepts specific qualified CLI versions, rather than a floating version
range. Executable identity and Codex SessionMeta use the same allowlist. Native
metadata must still supply the exact session, protected AL role, parent lineage
and creation provenance required by the existing authority and epoch checks.

## Codex 0.165.0 qualification — 2026-11-17

The installed Codex 0.165.0 was exercised with the production-generated native
policy and a local synthetic Responses service. These checks do not contact a
real model provider and do not establish business acceptance or model quality.

The first qualification attempt passed seven checks and failed all seven AL-role
lineage cases: the executable profile accepted 0.165.0, but the canonical
SessionMeta reader still required 0.164.0. Inspection of only the first native
metadata record confirmed the same explicit session, parent, role and creation
fields. Both readers now use the shared, explicit version allowlist.

The final combined run passed 16 checks, including:

- Actual installed Codex start and resume for all seven protected AL roles, with
  native-injected metadata resolving both root and child principals.
- Omitted shell, patch, image, code execution and foreign MCP handlers rejecting
  synthetic malicious calls; no escape sentinel was created.
- Epoch creation fences and revocation, with existing 0.164.0 and new 0.165.0
  metadata shapes; an unknown future version remains refused.
- Installed Codex and Claude profile prerequisites and generated Claude channel
  permissions. Claude was explicitly selected as installed version 2.1.325.

Reproduce the combined check with locked dependencies:

```sh
HARNESS_TEST_CODEX_VERSION=0.165.0 HARNESS_TEST_CLAUDE_VERSION=2.1.325 \
HARNESS_REQUIRE_NATIVE_TESTS=1 .venv/bin/python -m pytest -q \
  tests/test_native_tool_policy.py tests/test_native_tool_boundary.py \
  tests/test_native_controller_lineage.py tests/test_epoch_principals.py
```

The test selectors accept only the explicit qualification candidates. Without a
selector, the historical default remains Codex 0.164.0 / Claude 2.1.310; missing
optional binaries skip, while `HARNESS_REQUIRE_NATIVE_TESTS=1` makes them fail.
Older versions were not reinstalled or rerun in this qualification. No provider
credentials, runtime permissions, approvals, or existing session pins change.

## Claude 2.1.326 qualification — 2026-11-17

Claude 2.1.326 is accepted as an exact qualified version. The installed binary
passed 29 actual native checks against local synthetic Anthropic services:
restricted root tools, all seven protected roles on fresh start and resume,
child-channel isolation, attested identity hooks, and forbidden Agent effects.

After adding the exact production binding, 17 further checks passed with no
skips: production-generated background-child permissions on fresh start and
resume, owned-command Stop lifecycle, installed Claude prerequisites, exact
version parsing and existing epoch/revocation negatives. One Codex prerequisite
was deliberately deselected. Unknown 2.1.327, 2.1.3260 and malformed version
outputs remain refused; no floating version range is admitted.

Reproduce with locked dependencies and an explicit installed binary:

```sh
HARNESS_TEST_CLAUDE_VERSION=2.1.326 HARNESS_REQUIRE_NATIVE_TESTS=1 \
  .venv/bin/python -m pytest -q \
  tests/test_claude_tool_boundary.py tests/test_claude_child_boundary.py \
  tests/test_claude_native_attestation.py tests/test_claude_agent_effects.py

HARNESS_TEST_CLAUDE_VERSION=2.1.326 HARNESS_REQUIRE_NATIVE_TESTS=1 \
  .venv/bin/python -m pytest -q \
  tests/test_claude_generated_policy.py tests/test_claude_stop_native.py \
  tests/test_native_tool_policy.py tests/test_epoch_principals.py -k 'not codex'
```

Set `HARNESS_TEST_CLAUDE` when the candidate is outside the normal executable
search path. These synthetic checks establish native compatibility, not actual
provider or knowledge-task acceptance. Authentication selection, tool grants,
existing native identities and runtime pins are unchanged.
