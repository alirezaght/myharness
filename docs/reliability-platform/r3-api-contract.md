# R3 runtime policy API contract

This document describes the current local dashboard endpoints and the policy facts they expose. The endpoints are scoped to the existing execution database; they are not a separate policy service or a named-user authentication system.

## Read policy state

`GET /api/execution/policy` requires exactly one each of `task`, `execution`, and `epoch`. Optional query parameters are:

| Parameter | Meaning |
| --- | --- |
| `before` | Positive decision-row cursor for the next decision page. |
| `attempt` | One 32-character lowercase hexadecimal attempt ID to include its details. |
| `cap_after` | Capability ID cursor for the next capability page. |

Unknown parameters, repeated or blank values, invalid cursor syntax, or values longer than 128 characters return `400 {"error":"Invalid policy query"}`. A task/execution/epoch mismatch or unavailable epoch returns `404 {"error":"Policy scope unavailable"}`. SQLite or filesystem read failures return `503 {"error":"Policy storage unavailable"}`. The handler opens SQLite read-only, enables `query_only`, and uses the existing reliability scope check.

The version-1 response contains:

```json
{
  "schema_version": 1,
  "scope": {"task": "…", "execution": "…", "epoch": "…"},
  "policies": [],
  "pools": [],
  "limitations": {},
  "decisions": [],
  "next_cursor": null,
  "capabilities": [],
  "capabilities_next_cursor": null,
  "capabilities_has_more": false,
  "capabilities_coverage": {"status": "complete", "returned": 0, "limit": 25}
}
```

Decision and capability navigation are independent and each returns up to 25 rows. `before` selects older decisions by descending row ID. `cap_after` selects capability IDs lexically after that ID. A response returns `next_cursor` for decisions and `capabilities_next_cursor` plus `capabilities_has_more` for capabilities. Capability coverage is `partial` when a continuation cursor was supplied or another page remains, and `complete` only when the first page proves there is no more data. A caller must follow the two cursors separately; a partial page is not complete history.

Policy rows contain identifiers and versions, not policy rule bodies. Pool rows expose the stable pool ID, dimension, domain, latest shared-row maximum, consumed/reserved counts, revision and state. This maximum is shared accounting state, not a role-specific effective bound. For admission, the selected immutable policy documents, including explicitly referenced historical parents, contribute ceilings; the most restrictive selected ceiling applies to the operation. Raising the shared row in a later version does not remove an older selected parent ceiling or reset shared counters. `measured` equals consumed only for discrete `mutating_operations` and `retry_attempts`; other measurements and `unknown` are `null`. `limitations` explicitly identifies unsupported or uncoordinated coverage. Capability rows expose issuance decision reference, current state/use counts, and revocation action and decision references when revoked. They do not return capability resources, authorization text, or caller payloads.

When `attempt` is supplied, it must belong to this task, execution and epoch or the endpoint returns the same scoped 404. The optional object reports safe role/tool/state/boundary and timing fields, policy-version references, outcome, coordination coverage, retry provenance and liability. `reservations` show pool, units, charge state and observed units. `processes` show safe span/attempt references, launch/closure and parent/group/boundary flags. It does not expose raw resources, commands, helper data or failure prose. `uncoordinated_legacy` means the attempt is explicitly outside enrolled coordination; it is not a qualified pass.

## Operator actions

Four POST routes are registered:

| Route | Protected action consumed |
| --- | --- |
| `/api/policy/install` | Install or idempotently acknowledge a policy version. |
| `/api/policy/capability` | Issue an exact-bound capability and issuance action reference. |
| `/api/policy/revoke` | Advance the capability revocation generation and record its action. |
| `/api/policy/workflow` | Register the execution's approved workflow membership. |

Each body must be JSON object with exactly these six keys:

```json
{
  "task": "task-id",
  "execution": "execution-id",
  "epoch": "epoch-id",
  "generation": 0,
  "decision": "protected-decision-id",
  "revision": 0
}
```

`task`, `execution`, `epoch`, and `decision` are nonempty strings of at most 128 characters; `generation` and `revision` are bounded nonnegative integers. The action reads its document from the existing protected decision delivery; no action document or `approved` boolean is accepted in this request. Installation uses `revision` as a compare-and-set; all actions still require the exact six-key envelope. Successful actions return `200 {"schema_version":1,"result":…}`.

Before reading the body, the dashboard checks its loopback `Host`, a same-origin `Origin` and optional same-origin `Sec-Fetch-Site`, JSON content type, and one decimal `Content-Length`; transfer encoding and missing or repeated lengths are refused. A policy body over 4096 bytes returns 413. Header refusals return fixed 400/403/411/415 errors. Policy conflicts or invalid/stale authorization return `409` with a fixed policy code or `{"error":"Policy action refused"}`. SQLite or filesystem storage failures return `503 {"error":"Policy storage unavailable"}`. The local listener and browser-origin checks do not establish a named human identity.

## Protected authorization provenance

The accepted AL delivery text is canonical JSON with exactly `schema_version`, `kind`, `action`, `binding`, and `document`. It must use `schema_version: 1`, `kind: "runtime-policy-authorization"`, the route's exact action, and the full execution binding. The code rechecks the accepted decision, immutable decision-registry provenance, delivery text/verdict, selected execution and epoch, exact binding, current AL state and delivery acknowledgement hash. The decision must be in the accepted resolved/revalidation state. The acknowledged delivery hash must match the stored delivery. Installation, workflow registration, grant issuance and revocation execute under the existing active-epoch check and transaction.

Policy documents are closed version-1 data, not executable rules. Scope kinds are `global`, `project`, `workflow`, `role` and `execution`. Rule fields are limited to tool allow/deny, required approvals, read/write paths, and the closed `network`, `database`, `production` and `delegation` values. Limits use one of the recognized dimensions and explicit `execution_tree`, `workflow_instance` or `named` accounting domains with an owner reference. Applicable documents and parent policies compose; explicit denies win and existing Harness authority remains mandatory. If the configured restriction cannot be enforced by the current boundary, installation or admission refuses it.

## Accounting and safety boundaries

The existing project SQLite database is the transaction boundary. It atomically reserves all applicable pool units and capability uses with the allow decision. Pool ownership is stable across policy-version replacement and is scoped to its owner inside that database; it is not shared between independent databases or machines. A global policy scope does not change this accounting boundary.

`mutating_operations` charges one admitted mutation invocation at its guarded start; it does not count files or syscalls. `retry_attempts` counts an admitted retry tied to the trusted AL checkpoint flow. A multi-file patch is one mutation invocation. Failure, no-op, cancellation or a normal guarded refusal does not refund a charged unit or capability use. Only a fenced reservation with no start permit may be released as proven not started. A started or uncertain attempt keeps its charge and resource claims until supported settlement; interrupted or absent closure evidence remains unresolved.

Capability issuance binds the immutable developer decision to execution, epoch, generation, principal/role, operation, exact resources, policy digest, binding, maximum uses/units and expiry. Use and revoke are serialized in the same database. Revocation prevents later admissions but does not reverse an already started effect or refund a use. The audit trail records separate issuance/revocation actions, decision references and timeline provenance.

Sticky resource-domain enrollment and stable dependency read claims are independent of a matching budget, role or policy version. Known overlapping write/read effects and unknown liabilities are checked before affected work; dependency claims are established before bytes are read. Live `harness:repository-audit` derives a conservative repository-wide read claim before traversal. Because its Git/source traversal has no qualified coordinated execution boundary, it is refused with the fixed unsupported-enforcement refusal when policy or enrolled coordination applies; it does not proceed outside the claim fence. Independent safe diagnostics remain possible when they have no conflicting resource claim. Untouched legacy work can be marked uncoordinated; the system makes no coordination claim for other project databases or unobserved legacy processes.

`execution_elapsed_ms` binds a wall-clock origin to the current boot identity and also checks monotonic elapsed time while that supervisor boot remains current. It includes downtime on the same boot and refuses on backward wall time or an origin/clock mismatch. For ordinary repository `read_file`, the existing policy deadline is cooperatively checked before descriptor acquisition, after parent traversal/open, before and after bounded `read`/`pread` operations including EOF, after source-version validation and descriptor cleanup, and after decoding before the response returns. `apply_patch` checks before later directory creation, deletion, temporary write/flush/fsync and replacement steps after potentially blocking work, and before successful return. Cleanup remains unconditional; expiry after a completed target mutation does not roll back the effect, and charged units remain consumed. With no active policy attempt, these checks are no-ops; legacy behavior is unchanged. Special `harness:` and `artifact:` resources use their own handlers and are not silently covered by ordinary-file read timing checks. A bound check cannot interrupt an OS syscall already in progress or guarantee hard scheduling latency. It does not qualify arbitrary commands, AL, native tools, providers or unsupported process boundaries. Token, cost and native-child usage are unavailable (`null`); explicit unsupported enforcement refuses rather than claiming a value.

The named tooling/capability control resources return presence or status metadata; their preparation checks filesystem presence and do not read manifest bytes. Unknown `harness:` names that fall through to ordinary file reads are path-validated and classified as file resources. Descriptor-backed diagnostic `source`, `log` and `config` reads acquire the requested file resource claim before bytes and retain it through response construction; these diagnostic routes are not cleanup exemptions and are not qualified for strict deadline enforcement. This description follows the existing named handlers and does not qualify arbitrary opaque helper routes.

## Qualified sealed control methods

`sealed-al-retry-v1` and `sealed-al-writer-finish-v1` are the only control methods with a currently qualified process boundary. Their support is limited to macOS and the exact inspected immutable pin/complete source closure, launcher, interpreter, repository configuration and fixed Git executable. The allowlisted Git version output is `git version 2.54.0 (Apple Git-157)`, `git version 2.50.1 (Apple Git-155)`, `git version 2.50.1`, or `git version 2.54.0` from the resolved `/Library/Developer/CommandLineTools/usr/bin/git`. The repository must use a validated ordinary `.git` directory; linked worktrees, shallow repositories, alternates, grafts, replacements, promisor packs and unsupported gitlinks refuse. The runtime verifies file identities, runs the isolated interpreter with `-I -S -B`, uses finite bounded read-only Git helper forms, and authenticates the private control channel. Separate main/helper sandbox profiles are qualified by source-fixed probes for `os.fork`, `os.forkpty`, `os.posix_spawn` and `subprocess.Popen` with vfork enabled/disabled, close-fds enabled/disabled and new-session enabled. Each must be denied with `EPERM`/`EACCES`; allowed or ambiguous probes refuse. Unsupported configurations, old pins and non-macOS platforms are not silently upgraded or granted this method.

The retry method retains AL role, reason, checkpoint, one-use grant and retry safeguards. The writer-finish method binds the exact active writer fields and one of `success`, `failed` or `abandoned`; it does not settle earlier producer liabilities or verify results. Grant use, original AL state save and a qualified closure receipt are separate facts. A normal refusal may settle as a failed, charged invocation when closure is proven. Missing receipts, interrupted helpers, unqualified process observations and lost state remain fenced. Neither method qualifies arbitrary commands, native provider trees, general strict process deadlines, Linux/container execution or hosted provider behavior.
