"Bind the fixed retry broker to trusted AL source and a verified runtime hook."

import ast
from pathlib import Path

from harness.policy.context import CURRENT
from harness.policy.contracts import require
from harness.policy.retry_channel import RetryChannel


def prepare_retry(operation, principal, args, state):
    attempt = CURRENT.get()
    if attempt is None or attempt.legacy or not attempt.intent.retry:
        return None
    require(principal["role"] == "orchestrator", "POLICY_DENIED")
    require(
        len(args) == 4 and args[0] == "retry" and args[2] == "--reason" and bool(args[3].strip())
    )
    # Only inspect the selected immutable pin, never replace it with working-tree scripts.
    script = (
        Path(operation.initialization["pin_root"])
        / "payload"
        / operation.envelope["host"]
        / "skills/al/scripts/al_retry.py"
    )
    require(script.is_file() and not script.is_symlink(), "UNSUPPORTED_ENFORCEMENT_CAPABILITY")
    tree = ast.parse(script.read_text())
    require(
        any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "POLICY_RETRY_PROTOCOL" for t in n.targets)
            and isinstance(n.value, ast.Constant)
            and n.value.value == 1
            for n in tree.body
        ),
        "UNSUPPORTED_ENFORCEMENT_CAPABILITY",
    )
    expected = {
        "run_id": state["run_id"],
        "binding": state["integration"]["binding"],
        "agent": args[1],
        "reason": args[3],
        "failure": state.get("failure"),
        "used": state.get("retries", {}).get(args[1], 0),
    }
    return RetryChannel(attempt, expected)
