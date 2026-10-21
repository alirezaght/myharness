# Phase 3 disposable demo

Run `uv run python scripts/demo_phase3.py`. The deterministic scenario uses a
throwaway Git repository, real local SQLite approval and execution reservation,
the actual detached harness worker, a task-scoped blocker, an independent task
that writes a README and test through AL's writer and evidence guards, and a
real Decision Registry record delivered and acknowledged through the AL
adapter. It then reconnects through the dashboard route and verifies that the
committed cursor and completed sibling task remain stable.

The external Claude executable/model boundary is simulated by a disposable
test shim. The real harness worker, installed candidate AL package, guarded AL
CLI, task admission, decision dispatch/acknowledgement and event projection
run normally. The scenario verifies one provider-shim launch; it does not
establish native provider authentication or delegation.

The output below is pinned by `tests/test_demo_phase3.py`:

<!-- DEMO OUTPUT START -->
```text
Phase 3 disposable execution demo
External provider boundary: simulated; no model request was made.
Harness approval: current v1 bound to one execution.
AL source: real guarded CLI, detached worker and committed semantic outbox.
Task A: blocked by the architecture question; independent task B completed.
Decision Registry: DEC-0003 recorded and acknowledged.
Blocker: Resolved; task A admitted after acknowledgement.
Replay: reconnect retained event cursor 22.
Recovery: replay-only reconnect kept task B completed; provider launch count 1.
```
<!-- DEMO OUTPUT END -->
