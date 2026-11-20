## R3 compatibility correction — supersedes the always-on enrollment wording

Architect resolution: **use sticky resource-domain enrollment**. Retain the earlier addendum as history; replace its requirement to create permanent policy liabilities for every untouched legacy operation with the following protocol.

### Default and enrollment boundary

```text
every upgraded admission:
  check relevant KNOWN durable liabilities before optional-policy early returns

if resource domain has never been enrolled:
  preserve legacy best-effort behavior
  do not claim qualified closure or comprehensive coordination

if domain is enrolled:
  require resource claims for every overlapping effect and stable dependency use
  regardless of matching role policy, budget caps or later policy removal
```

Enrollment is derived from an actual authorized policy, not a new permission flow. Persist it separately from mutable policy bindings; deleting/replacing a policy cannot erase enrollment or existing obligations.

### Enrollment/launch serialization

Use the same authoritative database and `BEGIN IMMEDIATE` for:

- enrolling a resource domain;
- registering upgraded legacy launch admission;
- checking and reserving enforced resource claims.

```text
enroll(domain):
  validate existing policy authorization and exact resource namespace
  inspect relevant known producers and launch admissions
  refuse unresolved or unqualified predecessors
  persist sticky enrollment
  commit

launch admission:
  check known liabilities
  read enrollment inside transaction
  if enrolled:
    require qualified execution/closure capability
    reserve exact resource claims
  else:
    record bounded legacy launch presence for enrollment serialization
    label its capability/closure limitations explicitly
  commit before launch
```

Legacy launch metadata is not a claim of qualified closure. Parent exit or ordinary timeout cannot retroactively prove an arbitrary tree stopped. Preserve relevant uncertainty when evaluating subsequent enrollment.

No claim is made about independent databases or immutable/uninstrumented producers. Relevant known unsupported producers require qualified closure or refusal; absence of a new table row is not proof of their absence.

### Pre-effect capability refusal in enrolled domains

An arbitrary process whose whole execution boundary cannot be qualified must be refused **before its effect** in an enrolled domain. Do not launch it and surprise the user with a permanently blocked domain afterward.

The closed read-command grammar alone does not establish process-tree closure. Existing supported closed handlers may qualify separately.

Untouched legacy commands keep their existing best-effort behavior. This does not authorize them to cross a known unresolved liability.

### Dependency and concurrency placement

Use the private durable claim table proposed by Engineer; public policy API fields need not expose paths.

For stable dependency consumption:

```text
derive producer identity and resource scope from authenticated receipts/tasks
transactionally check overlap and reserve read-dependency claims
commit
verify output files/hashes outside the database transaction
retain claims through consuming effect and qualified settlement
```

Revalidate receipt/assignment identity at final start. On validation failure, release only through the fenced no-start transition. Per-controller locks are insufficient across execution surfaces; SQLite check-and-reserve provides the shared serialization.

Ordinary diagnostic reads may remain available without claiming stable dependency evidence. Permission revocation/release remains distinct from settlement.

### Pending process rows with no launch

`ProcessHooks.prepare()` currently inserts a pending process row before the start permit.

```text
reserved → released_proven_not_started:
  under exact owner/epoch fencing and one transaction:
    require no effect/start/launcher permit was issued
    cancel the sole remaining ability to start
    close pending process rows and resource claims as proven_not_started
    release eligible unconsumed reservations
```

A pending row alone does not prove an effect occurred. Conversely, once any relevant permit was issued, missing PID or missing launch notification cannot prove no effect. Started/unresolved attempts—and incorrectly terminal attempts with unclosed boundaries—retain their fence.

### Required additional cases

- Untouched no-policy legacy commands retain existing behavior without qualified-closure claims.
- Known unresolved liabilities block unmatched/unbudgeted consumers before early returns.
- Enrollment races with an upgraded legacy launch: one serialized outcome; no invisible launch.
- Enrollment refuses relevant unresolved/unqualified predecessors.
- Sticky enrollment survives policy removal and role changes.
- Unsupported arbitrary process in an enrolled domain refuses before launch.
- Dependency claims precede file verification and survive until consumption settles.
- Fenced no-permit pending rows close safely; permit-issued/missing-PID rows remain uncertain.
- Diagnostic reads and authenticated cleanup remain available.

OPEN_QUESTIONS
- []