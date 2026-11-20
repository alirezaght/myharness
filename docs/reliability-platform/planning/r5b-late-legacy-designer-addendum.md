## R5b addendum: late legacy-only R1 incidents

Replace the permanent after-cursor projection scan with bounded, repeatable **unindexed-row selection**. Old immutable bridges may continue inserting legacy-only incidents; only upgraded writers provide atomic catalog insertion.

```text
project_legacy_batch(scope, expected_checkpoint, request_id, limit<=100):
  BEGIN IMMEDIATE
  validate configured repository and actual task/execution scope

  if request_id already committed:
    require identical normalized request
    return its recorded receipt

  require projection checkpoint revision == expected_checkpoint

  rows = SELECT scoped legacy incidents
         WHERE NOT EXISTS (
           SELECT 1 FROM incident_operation_sources
           WHERE legacy_incident_id = legacy.incident_id
         )
         ORDER BY legacy.rowid ASC
         LIMIT limit

  for each row:
    insert exact identity-preserving catalog projection and source relation
    reject conflicting existing content

  more_as_of_commit = EXISTS(scoped legacy incident without source relation)

  increment checkpoint revision
  persist request receipt:
    projected_count
    checkpoint_revision
    more_as_of_commit
    coverage = "observed within this transaction; later inserts may be unindexed"
  COMMIT
```

The checkpoint is a concurrency/replay revision, **not a permanent lower bound on source timestamps or IDs**. Existing `reliability_incidents` is an append-only ordinary SQLite rowid table; insertion order prevents backdated timestamps from moving new rows ahead of previously pending rows. Revalidate this property if the schema changes.

Each explicit operator batch selects remaining unindexed rows from the beginning. A previously empty result does not disable future batches. “No unindexed incidents observed” is an as-of statement, never permanent completion. A new operator request can discover late rows; replaying an old request returns its historical receipt.

Keep the read-only `UNION ALL`/`NOT EXISTS` query unchanged. Late legacy-only rows appear immediately as `legacy_unindexed`, including after a sweep reported no remaining rows. Fresh views may report current unindexed availability using a read-only existence query. GET performs no projection, and old pins remain untouched.

Required test scenarios:

- An old bridge inserts a legacy-only incident after an empty batch; mixed reads show it and the next explicit batch enrolls it.
- Late rows have timestamps older than, equal to, and newer than previously projected rows; all enroll.
- More than 100 pending rows require multiple batches, each bounded to 100.
- Concurrent old-bridge insertion serializes before or after the projection transaction without loss; progress remains explicitly as-of.
- Exact request replay returns its original receipt without enrolling later rows; a new request discovers them.
- Concurrent projection requests preserve checkpoint conflicts, atomicity and duplicate-free source relations.
- Projection between mixed-read pages preserves existing incident sort keys and identities.
- Startup and GET remain nonmutating; no pin upgrade occurs.

OPEN_QUESTIONS
- []
