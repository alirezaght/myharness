# Phase 3 AL capability audit

Date: 2026-10-19. Status: integration choice accepted by the developer on
2026-10-19; candidate implementation and deterministic acceptance are complete,
with independent review and successful live native-host acceptance still
pending. The historical
pre-implementation observations below describe the baseline, not the current
candidate. Approval provenance is recorded in
[`DEC-0019`](decisions/DEC-0019-native-cli-al-integration.md).

## Scope and observed baseline

The requested work is P3-01 through P3-11 in
[the Phase 3 checklist](../tasks/phase-3-al-integration.md), implementing
[SPEC section 9](SPEC.md#9-al-integration-contract). Phases 1 and 2 are complete
in the current working tree. Prior AL performance and installer changes, Phase 2
acceptance/demo work, and other pre-existing changes must be preserved.

The harness currently seals approvals but deliberately does not launch AL:

- `harness/approval.py:156`: `require_approval` is the existing handoff guard.
- `harness/approval.py:105`: approval validity currently requires the approval
  phase; execution must introduce its own immutable binding rather than silently
  changing that guard or treating an execution status as another approval.
- `harness/resume.py:25`: approved tasks wait for the Phase 3 handoff.
- `harness/dashboard/server.py:34`: the existing dashboard already has SSE.
- `harness/dashboard/view.py:104`: dashboard views already combine local task
  state, specifications, diagrams, decisions and approvals.

AL is a host-agent skill, not an executable implementation service. Its Python
scripts enforce workflow state, evidence and review; calling `al_state start`
alone creates bookkeeping and does not execute implementation agents.

## Capability and gap matrix

| Contract requirement | Observed capability | Phase 3 gap |
| --- | --- | --- |
| Start AL execution | Codex and Claude skills direct a host supervisor through AL's state machine | Launch a real host conversation with the installed skill; track its identity separately from the AL run |
| Approved spec and test plan | Harness approval binds immutable spec path, version, revision, content and review hashes; AL accepts immutable task packages | Build and validate a handoff envelope from the approval guard, including the approved test plan |
| Agent lifecycle | AL records phase/model/effort, handoffs and writer registrations | Durable events for actual agent identities, assignments and lifecycle; phase labels alone are insufficient |
| Task progress | AL records phase completion, checks and evidence | Explicit task graph, dependencies, measured progress and optional labelled estimates |
| Blockers | AL supports specification conflicts, exceptional states and guarded recovery | Task-scoped architectural/product blocker protocol, decision delivery and acknowledgement |
| Implementation result | AL has guarded fresh/comparison review and approval | Emit a structured result bound to the exact harness spec; process exit alone is not success |
| Test results | AL evidence ledger records status, scope, counts and logs | Structured test-result events referencing ledger evidence |
| MR/pipeline/deployment | AL has gate/CI planning data, not a completed harness delivery integration | Versioned event types now; real delivery automation remains Phase 4 |
| Recovery | AL has durable run JSON, immutable artifacts, checkpoints and guarded resume | Durable host/run mappings, event replay and an independent worker that survives dashboard exit |
| Live dashboard | Existing HTTP dashboard, SSE, stale-connection handling | Execution projections, agent view, graph, blocker actions and log access |

## Source observations

Both platform copies of `scripts/al_store.py` atomically save run JSON and use
per-session locks. The Codex copy's save and lock boundaries are at lines 112
and 124. The state CLI exposes JSON inspection, guarded start, phases, writers,
checks, reviews, checkpoints, waiting and resume. It does not currently provide
the required durable integration event contract or a host-process launcher.

Writer registration is a safety lifetime, not a complete lifecycle for all
agents: read-only Architect and Designer agents also need explicit events.
Neither terminal panel parsing nor filesystem timestamps establish task
completion, dependency relationships or real agent progress.

Installed CLI audit, performed without a model request or credential access:

- `codex-cli 0.163.0`: `codex exec` supports noninteractive execution, JSONL
  events, workspace sandbox selection, and explicit-session resume.
- Claude Code `2.1.302`: print mode supports stream JSON, explicit session UUIDs,
  resume, streamed input and host permission handling.
- Help captures are at `/private/tmp/harness-p3-{codex-exec,codex-resume,claude}-help.txt`.
- CLI presence and help do not prove model entitlement, authentication success,
  subagent execution or end-to-end AL behavior. Those require separate tests.

Primary references inspected:

- [Codex noninteractive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
  and [app-server](https://learn.chatgpt.com/docs/app-server).
- [Claude Code programmatic execution](https://code.claude.com/docs/en/headless)
  and [CLI reference](https://code.claude.com/docs/en/cli-reference).

## Options for developer decision

1. **Recommended: native CLI adapters plus AL events.** Keep Python/stdlib and
   the installed skills. A durable harness worker launches the selected native
   host, while AL emits the shared semantic contract. Host JSON is auxiliary
   process/session data. This adds the least new infrastructure and supports
   both existing AL packages.
2. **Host SDK/app-server adapters.** Native RPC gives richer interactive host
   approval handling, but introduces more integration surface and potentially
   dependencies. AL still needs the semantic event contract; host events alone
   do not supply it.
3. **Interactive terminal launch plus observer.** Reuses interactive permission
   prompts and can observe AL events, but makes unattended supervision and
   reconnect behavior more dependent on terminal lifecycle. It is less suitable
   for the existing web approval flow.

The accepted design is implemented as the Phase 3 candidate and its v1 semantic
event contract is documented in [`al-event-contract-v1.md`](al-event-contract-v1.md).
The current verification ledger and support evidence are linked from the Phase
3 checklist. CLI capability and project-installed package checks do not prove
provider authentication, successful agent delegation or end-to-end live native
execution; those outcomes are recorded separately by the bounded native smoke.

## Candidate verification limits

The deterministic scenario uses real harness approval, a detached harness
worker, the AL lifecycle/event and decision-registry flow, acknowledgement, and
reconnect; only the external provider executable/model boundary is simulated.
The latest run-specific result is the guarded AL evidence ledger, queried with
`evidence status` for session `al-20261019T112752Z-d843eb`. Earlier scenario,
browser and demo EIDs were captured before the service-coverage amendment and
later support edits; they are historical and do not establish current PASS
status. Desktop/mobile captures and exact metrics must be taken from the latest
browser ledger artifact and Builder handoff report.

Service-check coverage is part of phase admission. Discovery records the
`identified`, `exhaustive`, or `incomplete` outcome. Exhaustive coverage is
permitted only when service enumeration and required-check discovery are both
complete; incomplete coverage records named blockers. See
[`service-coverage.md`](../al/codex/skills/al/reference/service-coverage.md)
for the shared policy and supported repository adapters. Its schemas, parity
manifest, and release layout are the candidate contract; current verification
is reported by the guarded ledger, not this reference page.

The bounded native-host attempts did not establish a successful live AL run:

- E105 (`p3-native-claude`, FAIL): the disposable Claude candidate recorded a
  native ID, but ended `Interrupted` with no AL run ID and event cursor 0;
  it did not time out. The log reports `Claude success: ` and an empty worker
  log tail. This does not establish an authentication failure or a successful
  AL lifecycle. Raw ledger log:
  `.codex/al-runtime/al-20261019T112752Z-d843eb/logs/cbe11401-E105-p3-native-claude-check.log`.
- E106 (`p3-native-codex`, FAIL): the bounded attempt reached timeout cleanup,
  then the runner raised `PermissionError: [Errno 1] Operation not permitted`
  while signaling its owned worker process group. It emitted no structured
  result; the disposable temporary workspace had already been removed, so
  worker diagnostics were unavailable. This is a runner cleanup failure, not
  evidence of either successful live AL execution or provider authentication
  status. Raw ledger log:
  `.codex/al-runtime/al-20261019T112752Z-d843eb/logs/cbe11401-E106-p3-native-codex-check.log`.

The runner now reports process-group cleanup permission errors in its structured
result; deterministic cleanup and diagnostic regressions are tracked in the
current ledger. The failed Codex provider attempt was not repeated. E6 and E7 only established that the
respective CLI binaries are present; they do not establish credentials,
entitlement or model availability. Accordingly, live native-host acceptance
remains open, and no final AL approval is claimed here.
