#!/usr/bin/env python3
"""Replay the Phase 3 approval, detached worker and durable event scenario.

The only simulated boundary is the external model provider. The demo uses a
throwaway Git repository and local SQLite, starts the detached harness worker,
records a real Decision Registry decision, dispatches it through the guarded
AL command, then exercises replay-only reconnect. Only the external provider
executable/model boundary is simulated.
"""

import tempfile
from pathlib import Path

from tests.test_phase3_acceptance import run_phase3_scenario


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="harness-phase3-demo-") as directory:
        result = run_phase3_scenario(Path(directory))
    print("Phase 3 disposable execution demo")
    print("External provider boundary: simulated; no model request was made.")
    print(f"Harness approval: current v{result['approval_version']} bound to one execution.")
    print("AL source: real guarded CLI, detached worker and committed semantic outbox.")
    print("Task A: blocked by the architecture question; independent task B completed.")
    print(f"Decision Registry: {result['decision_registry_id']} recorded and acknowledged.")
    print(f"Blocker: {result['blocker_state']}; task A admitted after acknowledgement.")
    print(f"Replay: reconnect retained event cursor {result['event_cursor']}.")
    print(
        "Recovery: replay-only reconnect kept task B completed; provider launch count "
        f"{result['provider_launch_count']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
