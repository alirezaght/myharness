# Browser validation

Browser validation is a separate, explicit workflow after an approved AL execution
has a current delivery and staging observation. The dashboard only displays the
stored projection. It never proposes, approves, launches, retries or reconciles a
browser run.

## Capability and plan review

Use the task and execution IDs printed by the execution commands:

```sh
harness browser capability TASK EXECUTION
harness browser propose TASK EXECUTION [--file mapping.json]
harness browser status TASK EXECUTION
```

Capability checks use an isolated anonymous Chrome profile and an `about:blank`
CDP session. They do not visit staging. The current integration is Chrome's
DevTools Protocol pipe. It does not attach to a signed-in desktop browser and
does not import a user's browser profile. Sites that require authentication,
interactive identity-provider challenges, nonstandard controls or unsupported
actions can remain untestable; plan them as excluded or unresolved with reasons.

The proposal lists the exact acceptance criterion text from the sealed spec.
It does not derive selectors or expected behavior from prose. Supply an explicit
mapping for executable criteria, review it, and inspect the target URL, deployment,
navigation scope, viewport, steps, assertions, evidence policy and limitations.
The target can come from the provider's staging URL or from a mapping with an
explicit reason. Plans bind the target and deployment identity. URL query strings
and fragments are outside the supported scope.

## Separate approval and execution

Approval is a distinct command and binds to the current plan hash, execution,
specification, delivery generation, candidate, deployment and target. Use a new
request ID for each deliberate approval attempt:

```sh
harness browser approve TASK EXECUTION --plan PLAN_HASH --request-id REQUEST_ID --attempt 1
harness browser run TASK EXECUTION --approval APPROVAL_ID
harness browser status TASK EXECUTION
harness report TASK EXECUTION
```

The driver accepts only fixed declarative operations; it does not accept caller
JavaScript or shell commands. A completed or uncertain external action is not
replayed. After interruption, inspect the stored result and use the receipt-only
reconciliation command when appropriate:

```sh
harness browser reconcile TASK EXECUTION --run RUN_ID
```

If no durable completion receipt establishes what happened, the run remains
uncertain. Reconciliation does not relaunch Chrome. An operator must resolve the
uncertain external outcome before a separately approved new attempt is possible.

## Private evidence and truthfulness

Screenshots and receipts are stored in the private Harness state area. Reports
show evidence metadata and hashes, not links to local filesystem paths. Missing,
tampered, stale, simulated, unavailable and current real-browser observations
remain distinct. A local fixture or test double cannot satisfy live acceptance.
The Phase 5 exit also requires a separately approved run against the real staging
application and a final report for that run.

## Troubleshooting

- **Capability unavailable:** confirm a supported Chrome/Chromium executable is
  installed and usable by the current user. The probe fails closed; it does not
  search arbitrary caches or reuse a desktop profile.
- **`HARNESS_BROWSER_EXECUTABLE`:** set this environment variable to an absolute
  path to a trusted executable when Chrome is not on the supported standard
  locations. The executable must be compatible with the CDP pipe driver. Do not
  point it at an unverified or quarantined binary.
- **Plan has unresolved criteria:** add concrete mapping steps and expected
  assertions only when the approved spec defines them. Otherwise keep the
  criterion unresolved and document the blocker.
- **Approval is stale:** generate and review a fresh plan after the spec,
  candidate, delivery generation, deployment or target changes.
- **Run is uncertain:** inspect status and reconcile the existing run. Do not
  issue a second approval to work around unresolved ownership.
- **Evidence unavailable:** retain the historical run. Do not recreate missing
  evidence or infer a pass from a completed delivery or pipeline.

Provider/browser credentials are not copied into the plan, screenshot metadata,
or report. Use the existing provider credential mechanism; interactive browser
authentication is not supported by the anonymous-profile driver.
