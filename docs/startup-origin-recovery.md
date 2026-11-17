# Startup origin recovery

Harness can recover a missing initial `runtime-binding.json` only after the trusted
AL issuer independently validates the exact active repository, platform, session,
execution and epoch origin. It verifies the existing protected source and publishes
the binding atomically without replacing prior bytes. It does not initialize that
run again or replace its history.

For a genuinely new durable execution, Harness uses the opt-in
`alctl runtime-successor origin --allow-absent` diagnostic. Only a closed `ABSENT`
response for the exact subject permits the existing guarded first start. Issuer
errors, permission failures, corrupt/foreign/ambiguous metadata, partial journals,
reserved origins and orphaned state are refusals. They are never interpreted as
absence. The original `origin` response remains unchanged without the flag.

Absence discovery inspects protected metadata and the fixed
authority/execution/epoch directory structure. It does not traverse retained
payload, artifact or log trees. An unrelated large payload cannot exhaust the
metadata scan; exact orphan session files and directories still refuse absence.

Install the updated AL coordinator together with Harness before relying on this
interface. An older issuer returns an upgrade/reconciliation diagnostic and no
initialization starts. Updating the coordinator does not rewrite AL runtime pins,
approvals or existing sealed bridge files; those retain their supported upgrade
and recovery procedures.

Verification used disposable local state and synthetic provider fixtures. Focused
runtime/legacy tests passed 86 cases with one Linux-only test skipped on macOS;
47 dashboard/phase acceptance cases passed with an empty operator home and a real
isolated AL installation. Later targeted origin/binding checks passed 33 cases,
two genuine private-operation/detached-startup cases, and 24 bounded-discovery
cases. These groups overlap and are not a combined test count. Two older
proof-phase entry expectations reproduced unchanged on both platforms' baseline
issuer source and remain recorded separately. Fresh full Linux/macOS CI remains
required; no real provider or account migration was involved in these repairs.
